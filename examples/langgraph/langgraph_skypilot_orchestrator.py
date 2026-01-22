"""LangGraph-SkyPilot Orchestrator Example.

This script demonstrates how to use LangGraph to orchestrate AI workloads
running on cloud infrastructure managed by SkyPilot.

The example implements an AI-driven ML pipeline that:
1. Analyzes task requirements using an LLM
2. Launches appropriate SkyPilot tasks
3. Monitors progress and handles failures
4. Makes intelligent decisions about next steps

Usage:
    export OPENAI_API_KEY=your-api-key
    python langgraph_skypilot_orchestrator.py
"""

import os
from typing import Annotated, Literal, Optional, TypedDict
import uuid

# LangGraph and LangChain imports
try:
    from langchain_openai import ChatOpenAI
    from langgraph.graph import END
    from langgraph.graph import StateGraph
    from langgraph.graph.message import add_messages
except ImportError:
    print('Please install required packages:')
    print('  pip install langgraph langchain-openai')
    raise

# SkyPilot imports
try:
    import sky
except ImportError:
    print('Please install SkyPilot:')
    print('  pip install "skypilot[kubernetes]"')
    raise


# =============================================================================
# State Definition
# =============================================================================
class PipelineState(TypedDict):
    """State for the ML pipeline workflow.

    Attributes:
        messages: Chat history for the LLM
        task_type: Type of task to run (preprocess, train, evaluate)
        cluster_name: Name of the SkyPilot cluster
        job_id: ID of the current job
        status: Current status (pending, running, completed, failed)
        results: Results from the completed task
        error: Error message if task failed
        iteration: Current iteration count
        max_iterations: Maximum iterations before stopping
    """

    messages: Annotated[list, add_messages]
    task_type: str
    cluster_name: str
    job_id: Optional[int]
    status: str
    results: Optional[str]
    error: Optional[str]
    iteration: int
    max_iterations: int


# =============================================================================
# Task Configurations
# =============================================================================
TASK_CONFIGS = {
    'preprocess': {
        'name': 'data-preprocessing',
        'resources': {
            'cpus': '4+'
        },
        'setup': 'pip install pandas numpy scikit-learn',
        'run': ('echo "Preprocessing data..." && '
                'python -c "import time; time.sleep(5); '
                'print(\'Preprocessing complete: 10000 samples processed\')"'),
    },
    'train': {
        'name': 'model-training',
        'resources': {
            'accelerators': 'L4:1'
        },
        'setup': 'pip install torch transformers',
        'run': ('echo "Training model..." && '
                'python -c "import time; time.sleep(10); '
                'print(\'Training complete: accuracy=0.92\')"'),
    },
    'evaluate': {
        'name': 'model-evaluation',
        'resources': {
            'accelerators': 'L4:1'
        },
        'setup': 'pip install torch transformers',
        'run': ('echo "Evaluating model..." && '
                'python -c "import time; time.sleep(5); '
                'print(\'Evaluation complete: test_accuracy=0.89\')"'),
    },
}


# =============================================================================
# LangGraph Nodes
# =============================================================================
def analyze_requirements(state: PipelineState) -> PipelineState:
    """Use LLM to analyze requirements and determine the next task."""
    llm = ChatOpenAI(model='gpt-4o-mini', temperature=0)

    # Build context from state
    context = f"""
    Current pipeline state:
    - Status: {state['status']}
    - Last task type: {state.get('task_type', 'none')}
    - Results: {state.get('results', 'none')}
    - Error: {state.get('error', 'none')}
    - Iteration: {state['iteration']}/{state['max_iterations']}
    
    Available tasks: preprocess, train, evaluate
    
    Determine the next task to run based on the pipeline state.
    If all tasks are complete or max iterations reached, respond with 'done'.
    """

    messages = state['messages'] + [{'role': 'user', 'content': context}]

    response = llm.invoke(messages)

    # Parse the response to determine next task
    response_text = response.content.lower()
    next_task = 'done'
    for task in ['preprocess', 'train', 'evaluate']:
        if task in response_text:
            next_task = task
            break

    return {
        **state,
        'messages': [response],
        'task_type': next_task,
        'iteration': state['iteration'] + 1,
    }


def launch_sky_task(state: PipelineState) -> PipelineState:
    """Launch a SkyPilot task based on the current state."""
    task_type = state['task_type']

    if task_type not in TASK_CONFIGS:
        return {
            **state,
            'status': 'failed',
            'error': f'Unknown task type: {task_type}',
        }

    config = TASK_CONFIGS[task_type]

    # Generate unique cluster name
    cluster_uuid = str(uuid.uuid4())[:4]
    cluster_name = f'langgraph-{config["name"]}-{cluster_uuid}'

    print(f'\n>>> Launching SkyPilot task: {task_type}')
    print(f'>>> Cluster name: {cluster_name}')

    try:
        # Create SkyPilot task from config
        task = sky.Task.from_yaml_config(config)

        # Launch the task
        request_id = sky.launch(task, cluster_name=cluster_name, down=True)
        job_id, _ = sky.stream_and_get(request_id)

        return {
            **state,
            'cluster_name': cluster_name,
            'job_id': job_id,
            'status': 'running',
            'error': None,
        }
    except Exception as e:  # pylint: disable=broad-except
        return {
            **state,
            'cluster_name': cluster_name,
            'status': 'failed',
            'error': str(e),
        }


def monitor_task(state: PipelineState) -> PipelineState:
    """Monitor the running task and collect results."""
    cluster_name = state['cluster_name']
    job_id = state['job_id']

    if not cluster_name or job_id is None:
        return {
            **state,
            'status': 'failed',
            'error': 'No cluster or job to monitor',
        }

    print(f'\n>>> Monitoring task on cluster: {cluster_name}')

    try:
        # Stream logs (this blocks until job completes)
        sky.tail_logs(cluster_name=cluster_name, job_id=job_id, follow=True)

        # Job completed successfully
        return {
            **state,
            'status': 'completed',
            'results': f'Task {state["task_type"]} completed successfully',
        }
    except Exception as e:  # pylint: disable=broad-except
        return {
            **state,
            'status': 'failed',
            'error': str(e),
        }


def cleanup_resources(state: PipelineState) -> PipelineState:
    """Clean up SkyPilot resources."""
    cluster_name = state.get('cluster_name')

    if cluster_name:
        print(f'\n>>> Cleaning up cluster: {cluster_name}')
        try:
            down_id = sky.down(cluster_name)
            sky.stream_and_get(down_id)
        except Exception as e:  # pylint: disable=broad-except
            print(f'Warning: Failed to clean up cluster: {e}')

    return state


def handle_error(state: PipelineState) -> PipelineState:
    """Handle errors with LLM-based analysis."""
    llm = ChatOpenAI(model='gpt-4o-mini', temperature=0)

    error_context = f"""
    A task failed with the following error:
    Task type: {state['task_type']}
    Error: {state['error']}
    
    Should we:
    1. Retry the same task
    2. Skip to the next task
    3. Stop the pipeline
    
    Respond with: retry, skip, or stop
    """

    response = llm.invoke([{'role': 'user', 'content': error_context}])
    action = response.content.lower()

    if 'retry' in action:
        return {**state, 'status': 'pending', 'error': None}
    elif 'skip' in action:
        return {**state, 'status': 'skipped'}
    else:
        return {**state, 'status': 'stopped'}


# =============================================================================
# Routing Functions
# =============================================================================
def should_launch(
        state: PipelineState) -> Literal['launch', 'cleanup', 'error', 'end']:
    """Determine next step after analysis."""
    if state['iteration'] >= state['max_iterations']:
        return 'cleanup'
    if state['task_type'] == 'done':
        return 'cleanup'
    if state['status'] == 'failed':
        return 'error'
    return 'launch'


def after_monitor(state: PipelineState) -> Literal['analyze', 'error']:
    """Determine next step after monitoring."""
    if state['status'] == 'failed':
        return 'error'
    return 'analyze'


def after_error(
        state: PipelineState) -> Literal['launch', 'analyze', 'cleanup']:
    """Determine next step after error handling."""
    if state['status'] == 'pending':  # Retry
        return 'launch'
    elif state['status'] == 'skipped':  # Skip to next
        return 'analyze'
    else:  # Stop
        return 'cleanup'


# =============================================================================
# Build the Graph
# =============================================================================
def create_pipeline_graph() -> StateGraph:
    """Create the LangGraph workflow for the ML pipeline."""
    workflow = StateGraph(PipelineState)

    # Add nodes
    workflow.add_node('analyze', analyze_requirements)
    workflow.add_node('launch', launch_sky_task)
    workflow.add_node('monitor', monitor_task)
    workflow.add_node('error', handle_error)
    workflow.add_node('cleanup', cleanup_resources)

    # Set entry point
    workflow.set_entry_point('analyze')

    # Add edges
    workflow.add_conditional_edges('analyze', should_launch, {
        'launch': 'launch',
        'cleanup': 'cleanup',
        'error': 'error',
        'end': END,
    })

    workflow.add_edge('launch', 'monitor')

    workflow.add_conditional_edges('monitor', after_monitor, {
        'analyze': 'analyze',
        'error': 'error',
    })

    workflow.add_conditional_edges('error', after_error, {
        'launch': 'launch',
        'analyze': 'analyze',
        'cleanup': 'cleanup',
    })

    workflow.add_edge('cleanup', END)

    return workflow


# =============================================================================
# Main Entry Point
# =============================================================================
def run_pipeline():
    """Run the ML pipeline orchestrated by LangGraph."""
    # Verify OpenAI API key
    if not os.environ.get('OPENAI_API_KEY'):
        print('Error: OPENAI_API_KEY environment variable not set')
        print('Please set it: export OPENAI_API_KEY=your-api-key')
        return

    # Create and compile the graph
    workflow = create_pipeline_graph()
    app = workflow.compile()

    # Initial state
    initial_state: PipelineState = {
        'messages': [{
            'role': 'system',
            'content': 'You are an ML pipeline orchestrator. '
                       'Determine the next task to run in sequence: '
                       'preprocess -> train -> evaluate. '
                       'After evaluate, respond with done.',
        }],
        'task_type': '',
        'cluster_name': '',
        'job_id': None,
        'status': 'pending',
        'results': None,
        'error': None,
        'iteration': 0,
        'max_iterations': 5,
    }

    print('=' * 60)
    print('LangGraph-SkyPilot ML Pipeline Orchestrator')
    print('=' * 60)
    print('\nStarting pipeline...')
    print('This will launch SkyPilot tasks on cloud infrastructure.')
    print('Press Ctrl+C to stop.\n')

    try:
        # Run the graph
        final_state = app.invoke(initial_state)

        print('\n' + '=' * 60)
        print('Pipeline Complete')
        print('=' * 60)
        print(f'Final status: {final_state["status"]}')
        print(f'Iterations: {final_state["iteration"]}')
        if final_state.get('results'):
            print(f'Results: {final_state["results"]}')
        if final_state.get('error'):
            print(f'Error: {final_state["error"]}')

    except KeyboardInterrupt:
        print('\n\nPipeline interrupted by user.')


if __name__ == '__main__':
    run_pipeline()
