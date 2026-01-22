# Running SkyPilot Tasks with LangGraph

This example demonstrates two integration patterns between SkyPilot and LangGraph:

1. **Using LangGraph to orchestrate SkyPilot tasks** - Use LangGraph's stateful graph-based workflows to orchestrate AI workloads on cloud infrastructure managed by SkyPilot
2. **Running LangGraph agents on SkyPilot** - Deploy and run LangGraph-based agents on cloud GPUs using SkyPilot

## Prerequisites

```bash
# Install required packages
pip install "skypilot[kubernetes]" langgraph langchain-openai

# Verify cloud access
sky check
```

## Pattern 1: LangGraph Orchestrating SkyPilot Tasks

LangGraph is excellent for building stateful, multi-step AI workflows. When combined with SkyPilot, you can:

- Use LLMs to make decisions about which cloud tasks to run
- Build agentic workflows that dynamically launch compute resources
- Create self-healing pipelines that react to failures intelligently

### Example: AI-Driven ML Pipeline

The `langgraph_skypilot_orchestrator.py` script demonstrates an AI agent that:
1. Analyzes a dataset to determine preprocessing needs
2. Launches appropriate SkyPilot tasks based on the analysis
3. Monitors job progress and handles failures
4. Makes decisions about next steps based on results

```bash
# Set your OpenAI API key
export OPENAI_API_KEY=your-api-key

# Run the orchestrator
python langgraph_skypilot_orchestrator.py
```

### How It Works

The orchestrator uses LangGraph's `StateGraph` to define a workflow:

```python
from langgraph.graph import StateGraph, END
import sky

# Define the workflow state
class PipelineState(TypedDict):
    task_name: str
    cluster_name: str
    status: str
    logs: str
    next_action: str

# Create nodes that interact with SkyPilot
def launch_sky_task(state: PipelineState) -> PipelineState:
    """Launch a SkyPilot task based on current state."""
    task = sky.Task.from_yaml(f"{state['task_name']}.yaml")
    request_id = sky.launch(task, cluster_name=state['cluster_name'])
    job_id, _ = sky.stream_and_get(request_id)
    return {**state, "status": "running", "job_id": job_id}

def analyze_results(state: PipelineState) -> PipelineState:
    """Use LLM to analyze results and decide next steps."""
    # LLM analyzes logs and determines next action
    ...

# Build the graph
workflow = StateGraph(PipelineState)
workflow.add_node("launch", launch_sky_task)
workflow.add_node("analyze", analyze_results)
workflow.add_edge("launch", "analyze")
workflow.add_conditional_edges("analyze", decide_next_step)

app = workflow.compile()
```

## Pattern 2: Running LangGraph Agents on SkyPilot

For compute-intensive LangGraph applications (e.g., agents using local LLMs, RAG systems with GPU inference), you can deploy them on SkyPilot-managed infrastructure.

### Example: Deploy a LangGraph Agent Service

```bash
# Launch the agent on a GPU instance
sky launch langgraph_agent.yaml

# Or use SkyServe for production deployment
sky serve up -n langgraph-agent langgraph_agent_service.yaml
```

### SkyPilot YAML for LangGraph Agent

```yaml
name: langgraph-agent

resources:
  accelerators: L4:1
  
setup: |
  pip install langgraph langchain-openai vllm
  
run: |
  python agent_server.py --port 8080
```

## Advanced: Multi-Agent Systems

LangGraph excels at building multi-agent systems. With SkyPilot, you can:

- Run different agents on different cloud resources (CPU vs GPU)
- Scale agent pools dynamically based on workload
- Use SkyPilot's managed jobs for long-running agent tasks

### Example: Distributed Research Agent

```python
from langgraph.graph import StateGraph
import sky

# Agent 1: Research Agent (CPU-intensive, web scraping)
research_task = sky.Task.from_yaml_config({
    "name": "research-agent",
    "resources": {"cpus": 4},
    "run": "python research_agent.py"
})

# Agent 2: Analysis Agent (GPU-intensive, LLM inference)  
analysis_task = sky.Task.from_yaml_config({
    "name": "analysis-agent", 
    "resources": {"accelerators": "L4:1"},
    "run": "python analysis_agent.py"
})

# LangGraph coordinates the agents
def route_to_agent(state):
    if state["requires_gpu"]:
        sky.launch(analysis_task, cluster_name="analysis")
    else:
        sky.launch(research_task, cluster_name="research")
```

## Using with SkyPilot API Server

For production deployments, use the SkyPilot API Server for centralized management:

```bash
# Set the API server endpoint
export SKYPILOT_API_SERVER_ENDPOINT=http://your-api-server:46580

# Run the LangGraph orchestrator
python langgraph_skypilot_orchestrator.py
```

This enables:
- Shared cluster state across multiple LangGraph workflows
- Centralized logging and monitoring
- Team collaboration on AI workloads

## Files in This Example

| File | Description |
|------|-------------|
| `langgraph_skypilot_orchestrator.py` | LangGraph workflow that orchestrates SkyPilot tasks |
| `langgraph_agent.yaml` | SkyPilot YAML for deploying a LangGraph agent |
| `agent_server.py` | Example LangGraph agent with HTTP API |
| `tasks/` | Sample SkyPilot task YAMLs for the ML pipeline |

## Related Resources

- [LangGraph Documentation](https://langchain-ai.github.io/langgraph/)
- [SkyPilot Documentation](https://docs.skypilot.co/)
- [Airflow Integration](../airflow/) - Similar pattern for Apache Airflow
- [Prefect Integration](../prefect/) - Similar pattern for Prefect
