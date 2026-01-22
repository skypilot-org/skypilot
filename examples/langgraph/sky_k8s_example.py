"""LangGraph-SkyPilot Kubernetes Example.

This script demonstrates how to run SkyPilot tasks on Kubernetes
as part of a LangGraph workflow, similar to the Prefect integration.

The example runs a simple ML pipeline:
1. Data Preprocessing (CPU)
2. Model Training (GPU)
3. Model Evaluation (GPU)

Usage:
    # Verify Kubernetes access
    sky check kubernetes

    # Run the example
    python sky_k8s_example.py
"""

from typing import Literal, TypedDict
import uuid

try:
    from langgraph.graph import END
    from langgraph.graph import StateGraph
except ImportError:
    print('Please install LangGraph:')
    print('  pip install langgraph')
    raise

try:
    import sky
except ImportError:
    print('Please install SkyPilot:')
    print('  pip install "skypilot[kubernetes]"')
    raise


# =============================================================================
# Pipeline State
# =============================================================================
class PipelineState(TypedDict):
    """State for the ML pipeline."""

    stage: str  # Current stage: preprocess, train, evaluate, done
    cluster_prefix: str
    results: dict  # Results from each stage
    error: str | None


# =============================================================================
# Task Configurations
# =============================================================================
PREPROCESS_CONFIG = {
    'name': 'preprocess',
    'resources': {
        'cpus': '2+',
        'infra': 'kubernetes',
    },
    'run': """
        echo "=== Data Preprocessing Stage ==="
        echo "Generating synthetic training data..."
        python3 -c "
import random
import json

# Simulate data preprocessing
data = {
    'samples': 10000,
    'features': 128,
    'train_split': 0.8,
    'preprocessing_complete': True
}
print(json.dumps(data, indent=2))
"
        echo "Preprocessing complete!"
    """,
}

TRAIN_CONFIG = {
    'name': 'train',
    'resources': {
        'cpus': '4+',
        'memory': '8+',
        'infra': 'kubernetes',
    },
    'run': """
        echo "=== Model Training Stage ==="
        echo "Training model on preprocessed data..."
        python3 -c "
import random
import json
import time

# Simulate training
print('Epoch 1/3: loss=0.892')
time.sleep(1)
print('Epoch 2/3: loss=0.456')
time.sleep(1)
print('Epoch 3/3: loss=0.234')

results = {
    'final_loss': 0.234,
    'accuracy': 0.92,
    'epochs': 3,
    'training_complete': True
}
print(json.dumps(results, indent=2))
"
        echo "Training complete!"
    """,
}

EVALUATE_CONFIG = {
    'name': 'evaluate',
    'resources': {
        'cpus': '2+',
        'infra': 'kubernetes',
    },
    'run': """
        echo "=== Model Evaluation Stage ==="
        echo "Evaluating model on test data..."
        python3 -c "
import json

# Simulate evaluation
results = {
    'test_accuracy': 0.89,
    'precision': 0.91,
    'recall': 0.87,
    'f1_score': 0.89,
    'evaluation_complete': True
}
print('Evaluation Results:')
print(json.dumps(results, indent=2))
"
        echo "Evaluation complete!"
    """,
}


# =============================================================================
# Helper Functions
# =============================================================================
def run_sky_task(task_config: dict, cluster_prefix: str) -> str:
    """Run a SkyPilot task and return the cluster name.

    Args:
        task_config: Dictionary with task configuration
        cluster_prefix: Prefix for cluster naming

    Returns:
        The cluster name used for the task
    """
    # Force Kubernetes infrastructure
    task_config = task_config.copy()
    if 'resources' not in task_config:
        task_config['resources'] = {}
    task_config['resources']['infra'] = 'kubernetes'

    # Create the SkyPilot task
    sky_task = sky.Task.from_yaml_config(task_config)

    # Generate unique cluster name
    task_name = task_config.get('name', 'task')
    cluster_uuid = str(uuid.uuid4())[:4]
    cluster_name = f'{cluster_prefix}-{task_name}-{cluster_uuid}'

    print(f'\n>>> Launching SkyPilot task: {task_name}')
    print(f'>>> Cluster: {cluster_name}')

    # Launch the task (down=True ensures cleanup after completion)
    request_id = sky.launch(sky_task, cluster_name=cluster_name, down=True)
    job_id, _ = sky.stream_and_get(request_id)

    # Stream logs
    sky.tail_logs(cluster_name=cluster_name, job_id=job_id, follow=True)

    return cluster_name


# =============================================================================
# LangGraph Nodes
# =============================================================================
def preprocess_node(state: PipelineState) -> PipelineState:
    """Run the preprocessing stage."""
    print('\n' + '=' * 60)
    print('STAGE: Data Preprocessing')
    print('=' * 60)

    try:
        cluster_name = run_sky_task(PREPROCESS_CONFIG, state['cluster_prefix'])
        return {
            **state,
            'stage': 'train',
            'results': {
                **state['results'], 'preprocess': {
                    'status': 'success',
                    'cluster': cluster_name
                }
            },
        }
    except Exception as e:  # pylint: disable=broad-except
        return {**state, 'stage': 'error', 'error': str(e)}


def train_node(state: PipelineState) -> PipelineState:
    """Run the training stage."""
    print('\n' + '=' * 60)
    print('STAGE: Model Training')
    print('=' * 60)

    try:
        cluster_name = run_sky_task(TRAIN_CONFIG, state['cluster_prefix'])
        return {
            **state,
            'stage': 'evaluate',
            'results': {
                **state['results'], 'train': {
                    'status': 'success',
                    'cluster': cluster_name
                }
            },
        }
    except Exception as e:  # pylint: disable=broad-except
        return {**state, 'stage': 'error', 'error': str(e)}


def evaluate_node(state: PipelineState) -> PipelineState:
    """Run the evaluation stage."""
    print('\n' + '=' * 60)
    print('STAGE: Model Evaluation')
    print('=' * 60)

    try:
        cluster_name = run_sky_task(EVALUATE_CONFIG, state['cluster_prefix'])
        return {
            **state,
            'stage': 'done',
            'results': {
                **state['results'], 'evaluate': {
                    'status': 'success',
                    'cluster': cluster_name
                }
            },
        }
    except Exception as e:  # pylint: disable=broad-except
        return {**state, 'stage': 'error', 'error': str(e)}


def error_node(state: PipelineState) -> PipelineState:
    """Handle errors."""
    print('\n' + '=' * 60)
    print('ERROR')
    print('=' * 60)
    print(f'Pipeline failed with error: {state["error"]}')
    return state


# =============================================================================
# Routing
# =============================================================================
def route_next_stage(
        state: PipelineState) -> Literal['train', 'evaluate', 'error', 'end']:
    """Route to the next stage based on current state."""
    stage = state['stage']
    if stage == 'train':
        return 'train'
    elif stage == 'evaluate':
        return 'evaluate'
    elif stage == 'error':
        return 'error'
    else:  # done
        return 'end'


def route_after_train(
        state: PipelineState) -> Literal['evaluate', 'error', 'end']:
    """Route after training stage."""
    if state['stage'] == 'error':
        return 'error'
    elif state['stage'] == 'evaluate':
        return 'evaluate'
    return 'end'


def route_after_evaluate(state: PipelineState) -> Literal['error', 'end']:
    """Route after evaluation stage."""
    if state['stage'] == 'error':
        return 'error'
    return 'end'


# =============================================================================
# Build Graph
# =============================================================================
def build_pipeline() -> StateGraph:
    """Build the LangGraph pipeline."""
    workflow = StateGraph(PipelineState)

    # Add nodes
    workflow.add_node('preprocess', preprocess_node)
    workflow.add_node('train', train_node)
    workflow.add_node('evaluate', evaluate_node)
    workflow.add_node('error', error_node)

    # Set entry point
    workflow.set_entry_point('preprocess')

    # Add edges
    workflow.add_conditional_edges('preprocess', route_next_stage, {
        'train': 'train',
        'evaluate': 'evaluate',
        'error': 'error',
        'end': END,
    })

    workflow.add_conditional_edges('train', route_after_train, {
        'evaluate': 'evaluate',
        'error': 'error',
        'end': END,
    })

    workflow.add_conditional_edges('evaluate', route_after_evaluate, {
        'error': 'error',
        'end': END,
    })

    workflow.add_edge('error', END)

    return workflow


# =============================================================================
# Main
# =============================================================================
def main():
    """Run the ML pipeline on Kubernetes using LangGraph and SkyPilot."""
    print('=' * 60)
    print('LangGraph + SkyPilot Kubernetes ML Pipeline')
    print('=' * 60)
    print('\nThis pipeline runs 3 stages on Kubernetes:')
    print('  1. Data Preprocessing (CPU)')
    print('  2. Model Training (CPU/Memory)')
    print('  3. Model Evaluation (CPU)')
    print('\nEach stage runs on a separate Kubernetes pod managed by SkyPilot.')
    print('Clusters are automatically terminated after each stage completes.')
    print('\nPress Ctrl+C to cancel.\n')

    # Build and compile the graph
    workflow = build_pipeline()
    app = workflow.compile()

    # Initial state
    initial_state: PipelineState = {
        'stage': 'preprocess',
        'cluster_prefix': 'langgraph',
        'results': {},
        'error': None,
    }

    try:
        # Run the pipeline
        final_state = app.invoke(initial_state)

        # Print summary
        print('\n' + '=' * 60)
        print('Pipeline Complete!')
        print('=' * 60)
        print('\nResults:')
        for stage, result in final_state['results'].items():
            status = result.get('status', 'unknown')
            cluster = result.get('cluster', 'N/A')
            print(f'  {stage}: {status} (cluster: {cluster})')

        if final_state['error']:
            print(f'\nError: {final_state["error"]}')

    except KeyboardInterrupt:
        print('\n\nPipeline cancelled by user.')


if __name__ == '__main__':
    main()
