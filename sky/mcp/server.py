"""SkyPilot MCP Server Implementation.

This module implements an MCP server that exposes SkyPilot functionality
for AI assistants to manage cloud infrastructure, launch clusters,
run jobs, and more.
"""

import json
import sys
from typing import Any, Dict, List, Optional, Union

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

# Initialize MCP server
mcp = Server("skypilot")


def _format_result(result: Any) -> str:
    """Format a result for MCP response."""
    if result is None:
        return "Operation completed successfully."
    if isinstance(result, (dict, list)):
        return json.dumps(result, indent=2, default=str)
    return str(result)


def _parse_json_arg(value: Optional[str], default: Any = None) -> Any:
    """Parse a JSON string argument."""
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


@mcp.list_tools()
async def list_tools() -> List[Tool]:
    """List all available SkyPilot tools."""
    return [
        # Cluster Management Tools
        Tool(
            name="skypilot_status",
            description=(
                "Get the status of SkyPilot clusters. Returns information about "
                "cluster state (INIT, UP, STOPPED), resources, cloud provider, "
                "and metadata. Use this to check if clusters are running."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "cluster_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description":
                            "Optional list of cluster names to check. "
                            "If not provided, returns status of all clusters."
                    },
                    "refresh": {
                        "type": "boolean",
                        "description":
                            "If true, refresh status from cloud providers. "
                            "Default is false (use cached status).",
                        "default": False
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="skypilot_launch",
            description=(
                "Launch a new SkyPilot cluster or run a task on a new/existing "
                "cluster. This provisions cloud resources and executes the "
                "specified task. Supports all major cloud providers."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "task_yaml": {
                        "type": "string",
                        "description":
                            "Path to a SkyPilot task YAML file, or inline "
                            "YAML content defining the task."
                    },
                    "cluster_name": {
                        "type": "string",
                        "description":
                            "Name for the cluster. If not provided, "
                            "a name will be auto-generated."
                    },
                    "cloud": {
                        "type": "string",
                        "description":
                            "Cloud provider to use (aws, gcp, azure, "
                            "kubernetes, etc.). If not specified, SkyPilot "
                            "will auto-select the cheapest option."
                    },
                    "gpus": {
                        "type": "string",
                        "description":
                            "GPU requirement (e.g., 'A100:4', 'V100', 'H100:8'). "
                            "Format: GPU_TYPE or GPU_TYPE:COUNT."
                    },
                    "cpus": {
                        "type": "string",
                        "description":
                            "Number of vCPUs required (e.g., '4', '16+')."
                    },
                    "memory": {
                        "type": "string",
                        "description":
                            "Memory requirement (e.g., '32', '64+'). In GB."
                    },
                    "idle_minutes_to_autostop": {
                        "type": "integer",
                        "description":
                            "Auto-stop cluster after this many idle minutes. "
                            "Helps save costs."
                    },
                    "down": {
                        "type": "boolean",
                        "description":
                            "If true, tear down the cluster after the task "
                            "completes (instead of just stopping).",
                        "default": False
                    },
                    "dryrun": {
                        "type": "boolean",
                        "description":
                            "If true, only show what would be done without "
                            "actually launching.",
                        "default": False
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="skypilot_exec",
            description=(
                "Execute a task or command on an existing SkyPilot cluster. "
                "This skips provisioning and setup, directly running the "
                "specified commands."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "cluster_name": {
                        "type": "string",
                        "description": "Name of the existing cluster to use."
                    },
                    "task_yaml": {
                        "type": "string",
                        "description":
                            "Path to task YAML file or inline YAML content."
                    },
                    "command": {
                        "type": "string",
                        "description":
                            "Direct command to execute (alternative to task_yaml)."
                    },
                    "down": {
                        "type": "boolean",
                        "description":
                            "If true, tear down cluster after execution.",
                        "default": False
                    }
                },
                "required": ["cluster_name"]
            }
        ),
        Tool(
            name="skypilot_stop",
            description=(
                "Stop a running SkyPilot cluster. The cluster's storage/disk "
                "is preserved, and billing for compute stops. The cluster can "
                "be restarted later with 'skypilot_start'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "cluster_name": {
                        "type": "string",
                        "description": "Name of the cluster to stop."
                    },
                    "purge": {
                        "type": "boolean",
                        "description":
                            "If true, force mark as stopped even if cloud "
                            "operation fails.",
                        "default": False
                    }
                },
                "required": ["cluster_name"]
            }
        ),
        Tool(
            name="skypilot_start",
            description=(
                "Start (restart) a stopped SkyPilot cluster. This re-provisions "
                "the compute resources while preserving the cluster's disk."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "cluster_name": {
                        "type": "string",
                        "description": "Name of the cluster to start."
                    },
                    "idle_minutes_to_autostop": {
                        "type": "integer",
                        "description":
                            "Auto-stop after this many idle minutes."
                    },
                    "down": {
                        "type": "boolean",
                        "description":
                            "If true, tear down instead of stop on autostop.",
                        "default": False
                    },
                    "retry_until_up": {
                        "type": "boolean",
                        "description":
                            "If true, retry provisioning until successful.",
                        "default": False
                    }
                },
                "required": ["cluster_name"]
            }
        ),
        Tool(
            name="skypilot_down",
            description=(
                "Tear down a SkyPilot cluster completely. This deletes all "
                "cloud resources including storage. Use with caution as data "
                "will be lost."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "cluster_name": {
                        "type": "string",
                        "description": "Name of the cluster to tear down."
                    },
                    "purge": {
                        "type": "boolean",
                        "description":
                            "If true, force remove from status even if cloud "
                            "operation fails.",
                        "default": False
                    }
                },
                "required": ["cluster_name"]
            }
        ),
        Tool(
            name="skypilot_autostop",
            description=(
                "Configure auto-stop for a cluster. The cluster will "
                "automatically stop or tear down after being idle for the "
                "specified duration."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "cluster_name": {
                        "type": "string",
                        "description": "Name of the cluster to configure."
                    },
                    "idle_minutes": {
                        "type": "integer",
                        "description":
                            "Minutes of idleness before auto-stop triggers. "
                            "Use -1 to disable."
                    },
                    "down": {
                        "type": "boolean",
                        "description":
                            "If true, tear down instead of stop.",
                        "default": False
                    }
                },
                "required": ["cluster_name", "idle_minutes"]
            }
        ),

        # Cluster Job Management Tools (jobs on a specific cluster)
        Tool(
            name="skypilot_cluster_queue",
            description=(
                "Get the job queue for a specific cluster. Shows all jobs "
                "running on that cluster with their status (PENDING, RUNNING, "
                "SUCCEEDED, FAILED, CANCELLED), resources, and timing info. "
                "This is for jobs on a single cluster, not managed jobs."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "cluster_name": {
                        "type": "string",
                        "description": "Name of the cluster."
                    },
                    "skip_finished": {
                        "type": "boolean",
                        "description":
                            "If true, exclude completed/failed jobs.",
                        "default": False
                    }
                },
                "required": ["cluster_name"]
            }
        ),
        Tool(
            name="skypilot_cluster_cancel",
            description=(
                "Cancel jobs on a specific cluster. Can cancel specific jobs "
                "by ID or all jobs on that cluster. This is for jobs on a "
                "single cluster, not managed jobs."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "cluster_name": {
                        "type": "string",
                        "description": "Name of the cluster."
                    },
                    "job_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description":
                            "List of job IDs to cancel. If not provided "
                            "with 'all', no jobs are cancelled."
                    },
                    "all": {
                        "type": "boolean",
                        "description": "If true, cancel all jobs.",
                        "default": False
                    }
                },
                "required": ["cluster_name"]
            }
        ),
        Tool(
            name="skypilot_cluster_logs",
            description=(
                "Get logs from a job running on a specific cluster. Can "
                "tail/follow logs in real-time or retrieve historical logs. "
                "This is for jobs on a single cluster, not managed jobs."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "cluster_name": {
                        "type": "string",
                        "description": "Name of the cluster."
                    },
                    "job_id": {
                        "type": "integer",
                        "description":
                            "Job ID to get logs for. If not provided, "
                            "gets logs for the latest job."
                    },
                    "tail": {
                        "type": "integer",
                        "description":
                            "Number of lines to show from the end. "
                            "Default 0 means show all.",
                        "default": 100
                    }
                },
                "required": ["cluster_name"]
            }
        ),

        # Managed Jobs Tools (SkyPilot managed job lifecycle)
        Tool(
            name="skypilot_jobs_launch",
            description=(
                "Launch a SkyPilot managed job. Managed jobs provide automatic "
                "recovery from preemptions and failures, cost optimization with "
                "spot instances, and lifecycle management. The job runs on "
                "automatically provisioned infrastructure."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "task_yaml": {
                        "type": "string",
                        "description":
                            "Path to a SkyPilot task YAML file, or inline "
                            "YAML content defining the job."
                    },
                    "name": {
                        "type": "string",
                        "description":
                            "Name for the managed job. If not provided, "
                            "a name will be auto-generated."
                    },
                    "command": {
                        "type": "string",
                        "description":
                            "Direct command to run (alternative to task_yaml)."
                    },
                    "cloud": {
                        "type": "string",
                        "description":
                            "Cloud provider to use (aws, gcp, azure, etc.)."
                    },
                    "gpus": {
                        "type": "string",
                        "description":
                            "GPU requirement (e.g., 'A100:4', 'V100', 'H100:8')."
                    },
                    "cpus": {
                        "type": "string",
                        "description": "Number of vCPUs required."
                    },
                    "memory": {
                        "type": "string",
                        "description": "Memory requirement in GB."
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="skypilot_jobs_queue",
            description=(
                "Get the queue of SkyPilot managed jobs. Shows all managed "
                "jobs with their status, recovery count, duration, and "
                "resource information. Managed jobs can span multiple clusters."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "refresh": {
                        "type": "boolean",
                        "description":
                            "If true, refresh the jobs controller if stopped.",
                        "default": False
                    },
                    "skip_finished": {
                        "type": "boolean",
                        "description": "If true, exclude finished jobs.",
                        "default": False
                    },
                    "job_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Specific job IDs to query."
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="skypilot_jobs_cancel",
            description=(
                "Cancel SkyPilot managed jobs. Can cancel by job name, "
                "job IDs, or all jobs."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name of the managed job to cancel."
                    },
                    "job_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "List of job IDs to cancel."
                    },
                    "all": {
                        "type": "boolean",
                        "description": "If true, cancel all managed jobs.",
                        "default": False
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="skypilot_jobs_logs",
            description=(
                "Get logs from a SkyPilot managed job. Shows the output "
                "and status of the job execution."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description":
                            "Name of the managed job. If not provided with "
                            "job_id, gets logs for the latest job."
                    },
                    "job_id": {
                        "type": "integer",
                        "description": "ID of the managed job."
                    },
                    "tail": {
                        "type": "integer",
                        "description":
                            "Number of lines to show from the end.",
                        "default": 100
                    }
                },
                "required": []
            }
        ),

        # Resource Query Tools
        Tool(
            name="skypilot_show_gpus",
            description=(
                "List available GPU/accelerator types across cloud providers. "
                "Shows instance types, pricing, and availability. Useful for "
                "finding the right GPU for your workload."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "gpu_name": {
                        "type": "string",
                        "description":
                            "Filter by GPU name (e.g., 'A100', 'V100', 'H100'). "
                            "Supports partial matching."
                    },
                    "cloud": {
                        "type": "string",
                        "description":
                            "Filter by cloud provider (aws, gcp, azure, etc.)."
                    },
                    "region": {
                        "type": "string",
                        "description": "Filter by region (e.g., 'us-east-1')."
                    },
                    "quantity": {
                        "type": "integer",
                        "description":
                            "Filter by minimum GPU count available."
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="skypilot_check",
            description=(
                "Check which cloud providers are enabled and have valid "
                "credentials configured. Returns a list of available clouds."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="skypilot_cost_report",
            description=(
                "Get a cost report for clusters. Shows estimated costs for "
                "active and historical clusters over a specified period."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description":
                            "Number of days to include in report. Default 30.",
                        "default": 30
                    }
                },
                "required": []
            }
        ),

        # Storage Tools
        Tool(
            name="skypilot_storage_ls",
            description=(
                "List all SkyPilot managed storage buckets/volumes. Shows "
                "storage name, cloud provider, size, and status."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="skypilot_storage_delete",
            description=(
                "Delete a SkyPilot managed storage bucket/volume. This "
                "permanently deletes the storage and all its contents."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name of the storage to delete."
                    }
                },
                "required": ["name"]
            }
        ),

        # Endpoints Tool
        Tool(
            name="skypilot_endpoints",
            description=(
                "Get the endpoints (URLs) for services running on a cluster. "
                "Shows ports and their corresponding public URLs."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "cluster_name": {
                        "type": "string",
                        "description": "Name of the cluster."
                    },
                    "port": {
                        "type": "integer",
                        "description":
                            "Specific port to query. If not provided, "
                            "returns all endpoints."
                    }
                },
                "required": ["cluster_name"]
            }
        ),

        # API Server Tools
        Tool(
            name="skypilot_api_info",
            description=(
                "Get information about the SkyPilot API server. Shows server "
                "status, version, and connection details."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
    ]


@mcp.call_tool()
async def call_tool(name: str, arguments: Dict[str, Any]) -> List[TextContent]:
    """Execute a SkyPilot tool and return the result."""
    # Lazy import sky to avoid slow startup
    import sky

    try:
        result = await _execute_tool(name, arguments, sky)
        return [TextContent(type="text", text=_format_result(result))]
    except Exception as e:
        error_msg = f"Error executing {name}: {type(e).__name__}: {str(e)}"
        return [TextContent(type="text", text=error_msg)]


async def _execute_tool(name: str, arguments: Dict[str, Any],
                        sky) -> Any:
    """Execute the appropriate SkyPilot function based on tool name."""

    # Cluster Management
    if name == "skypilot_status":
        cluster_names = arguments.get("cluster_names")
        refresh = arguments.get("refresh", False)
        refresh_mode = (sky.StatusRefreshMode.REFRESH
                        if refresh else sky.StatusRefreshMode.NONE)
        request_id = sky.status(cluster_names=cluster_names,
                                refresh=refresh_mode)
        result = sky.get(request_id)
        # Convert to serializable format
        return [_status_to_dict(s) for s in result]

    elif name == "skypilot_launch":
        task = _create_task_from_args(arguments, sky)
        request_id = sky.launch(
            task,
            cluster_name=arguments.get("cluster_name"),
            idle_minutes_to_autostop=arguments.get("idle_minutes_to_autostop"),
            down=arguments.get("down", False),
            dryrun=arguments.get("dryrun", False),
        )
        if arguments.get("dryrun", False):
            return {"status": "dryrun", "message": "Dry run completed"}
        job_id, handle = sky.get(request_id)
        return {
            "status": "launched",
            "job_id": job_id,
            "cluster_name": arguments.get("cluster_name") or
                           (handle.cluster_name if handle else None)
        }

    elif name == "skypilot_exec":
        cluster_name = arguments["cluster_name"]
        task = _create_task_from_args(arguments, sky)
        request_id = sky.exec(
            task,
            cluster_name=cluster_name,
            down=arguments.get("down", False),
        )
        job_id, handle = sky.get(request_id)
        return {
            "status": "executed",
            "job_id": job_id,
            "cluster_name": cluster_name
        }

    elif name == "skypilot_stop":
        request_id = sky.stop(
            cluster_name=arguments["cluster_name"],
            purge=arguments.get("purge", False),
        )
        sky.get(request_id)
        return {"status": "stopped", "cluster_name": arguments["cluster_name"]}

    elif name == "skypilot_start":
        request_id = sky.start(
            cluster_name=arguments["cluster_name"],
            idle_minutes_to_autostop=arguments.get("idle_minutes_to_autostop"),
            down=arguments.get("down", False),
            retry_until_up=arguments.get("retry_until_up", False),
        )
        sky.get(request_id)
        return {"status": "started", "cluster_name": arguments["cluster_name"]}

    elif name == "skypilot_down":
        request_id = sky.down(
            cluster_name=arguments["cluster_name"],
            purge=arguments.get("purge", False),
        )
        sky.get(request_id)
        return {
            "status": "terminated",
            "cluster_name": arguments["cluster_name"]
        }

    elif name == "skypilot_autostop":
        request_id = sky.autostop(
            cluster_name=arguments["cluster_name"],
            idle_minutes=arguments["idle_minutes"],
            down=arguments.get("down", False),
        )
        sky.get(request_id)
        return {
            "status": "autostop_configured",
            "cluster_name": arguments["cluster_name"],
            "idle_minutes": arguments["idle_minutes"]
        }

    # Cluster Job Management
    elif name == "skypilot_cluster_queue":
        request_id = sky.queue(
            cluster_name=arguments["cluster_name"],
            skip_finished=arguments.get("skip_finished", False),
        )
        result = sky.get(request_id)
        return [_job_record_to_dict(j) for j in result]

    elif name == "skypilot_cluster_cancel":
        request_id = sky.cancel(
            cluster_name=arguments["cluster_name"],
            job_ids=arguments.get("job_ids"),
            all=arguments.get("all", False),
        )
        sky.get(request_id)
        return {
            "status": "cancelled",
            "cluster_name": arguments["cluster_name"]
        }

    elif name == "skypilot_cluster_logs":
        from io import StringIO
        output = StringIO()
        exit_code = sky.tail_logs(
            cluster_name=arguments["cluster_name"],
            job_id=arguments.get("job_id"),
            follow=False,
            tail=arguments.get("tail", 100),
            output_stream=output,
        )
        return {
            "logs": output.getvalue(),
            "exit_code": exit_code
        }

    # Managed Jobs
    elif name == "skypilot_jobs_launch":
        from sky.jobs.client import sdk as jobs_sdk
        task = _create_task_from_args(arguments, sky)
        request_id = jobs_sdk.launch(
            task,
            name=arguments.get("name"),
        )
        job_id, handle = sky.get(request_id)
        return {
            "status": "launched",
            "job_id": job_id,
            "job_name": arguments.get("name"),
        }

    elif name == "skypilot_jobs_queue":
        from sky.jobs.client import sdk as jobs_sdk
        request_id = jobs_sdk.queue(
            refresh=arguments.get("refresh", False),
            skip_finished=arguments.get("skip_finished", False),
            job_ids=arguments.get("job_ids"),
        )
        result = sky.get(request_id)
        return [_managed_job_record_to_dict(j) for j in result]

    elif name == "skypilot_jobs_cancel":
        from sky.jobs.client import sdk as jobs_sdk
        request_id = jobs_sdk.cancel(
            name=arguments.get("name"),
            job_ids=arguments.get("job_ids"),
            all=arguments.get("all", False),
        )
        sky.get(request_id)
        return {"status": "cancelled"}

    elif name == "skypilot_jobs_logs":
        from io import StringIO
        from sky.jobs.client import sdk as jobs_sdk
        output = StringIO()
        exit_code = jobs_sdk.tail_logs(
            name=arguments.get("name"),
            job_id=arguments.get("job_id"),
            follow=False,
            tail=arguments.get("tail", 100),
            output_stream=output,
        )
        return {
            "logs": output.getvalue(),
            "exit_code": exit_code
        }

    # Resource Queries
    elif name == "skypilot_show_gpus":
        result = sky.list_accelerators(
            gpus_only=True,
            name_filter=arguments.get("gpu_name"),
            region_filter=arguments.get("region"),
            quantity_filter=arguments.get("quantity"),
            clouds=arguments.get("cloud"),
        )
        # Convert to serializable format
        formatted = {}
        for gpu_name, instances in result.items():
            formatted[gpu_name] = [_instance_info_to_dict(i) for i in instances]
        return formatted

    elif name == "skypilot_check":
        clouds = sky.enabled_clouds()
        return {"enabled_clouds": clouds}

    elif name == "skypilot_cost_report":
        request_id = sky.cost_report(days=arguments.get("days", 30))
        result = sky.get(request_id)
        return result

    # Storage
    elif name == "skypilot_storage_ls":
        request_id = sky.storage_ls()
        result = sky.get(request_id)
        return [_storage_record_to_dict(s) for s in result]

    elif name == "skypilot_storage_delete":
        request_id = sky.storage_delete(name=arguments["name"])
        sky.get(request_id)
        return {"status": "deleted", "name": arguments["name"]}

    # Endpoints
    elif name == "skypilot_endpoints":
        request_id = sky.endpoints(
            cluster=arguments["cluster_name"],
            port=arguments.get("port"),
        )
        result = sky.get(request_id)
        return result

    # API Server
    elif name == "skypilot_api_info":
        result = sky.api_info()
        return result

    else:
        raise ValueError(f"Unknown tool: {name}")


def _create_task_from_args(arguments: Dict[str, Any], sky) -> 'sky.Task':
    """Create a SkyPilot Task from tool arguments."""
    import os
    import tempfile
    import yaml

    task_yaml = arguments.get("task_yaml")
    command = arguments.get("command")

    if task_yaml:
        # Check if it's a file path or inline YAML
        if os.path.isfile(task_yaml):
            task = sky.Task.from_yaml(task_yaml)
        else:
            # Treat as inline YAML
            try:
                yaml_content = yaml.safe_load(task_yaml)
                # Write to temp file and load
                with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                                  delete=False) as f:
                    yaml.dump(yaml_content, f)
                    temp_path = f.name
                try:
                    task = sky.Task.from_yaml(temp_path)
                finally:
                    os.unlink(temp_path)
            except yaml.YAMLError:
                # Treat as a command
                task = sky.Task(run=task_yaml)
    elif command:
        task = sky.Task(run=command)
    else:
        # Empty task - just provision cluster
        task = sky.Task()

    # Apply resource overrides
    resources_kwargs = {}
    if arguments.get("cloud"):
        cloud_name = arguments["cloud"].lower()
        cloud_cls = getattr(sky, cloud_name.upper(), None)
        if cloud_cls:
            resources_kwargs["cloud"] = cloud_cls()
    if arguments.get("gpus"):
        resources_kwargs["accelerators"] = arguments["gpus"]
    if arguments.get("cpus"):
        resources_kwargs["cpus"] = arguments["cpus"]
    if arguments.get("memory"):
        resources_kwargs["memory"] = arguments["memory"]

    if resources_kwargs:
        task = task.set_resources(sky.Resources(**resources_kwargs))

    return task


def _status_to_dict(status) -> Dict[str, Any]:
    """Convert a StatusResponse to a dictionary."""
    return {
        "cluster_name": status.get("name"),
        "status": str(status.get("status")),
        "handle": {
            "cloud": str(status.get("handle", {}).get("cloud", "")),
            "region": status.get("handle", {}).get("region"),
            "zone": status.get("handle", {}).get("zone"),
        } if status.get("handle") else None,
        "autostop": status.get("autostop"),
        "resources": status.get("resources"),
        "duration": status.get("duration"),
    }


def _job_record_to_dict(job) -> Dict[str, Any]:
    """Convert a ClusterJobRecord to a dictionary."""
    return {
        "job_id": getattr(job, 'job_id', None),
        "job_name": getattr(job, 'job_name', None),
        "status": str(getattr(job, 'status', None)),
        "submitted_at": str(getattr(job, 'submitted_at', None)),
        "started_at": str(getattr(job, 'started_at', None)),
        "ended_at": str(getattr(job, 'ended_at', None)),
        "resources": getattr(job, 'resources', None),
    }


def _managed_job_record_to_dict(job) -> Dict[str, Any]:
    """Convert a ManagedJobRecord to a dictionary."""
    # Handle both dict and object formats
    if isinstance(job, dict):
        return {
            "job_id": job.get('job_id'),
            "job_name": job.get('job_name'),
            "status": str(job.get('status')),
            "submitted_at": str(job.get('submitted_at')),
            "end_at": str(job.get('end_at')),
            "job_duration": job.get('job_duration'),
            "recovery_count": job.get('recovery_count'),
            "resources": job.get('resources'),
            "cluster_resources": job.get('cluster_resources'),
            "region": job.get('region'),
        }
    return {
        "job_id": getattr(job, 'job_id', None),
        "job_name": getattr(job, 'job_name', None),
        "status": str(getattr(job, 'status', None)),
        "submitted_at": str(getattr(job, 'submitted_at', None)),
        "end_at": str(getattr(job, 'end_at', None)),
        "job_duration": getattr(job, 'job_duration', None),
        "recovery_count": getattr(job, 'recovery_count', None),
        "resources": getattr(job, 'resources', None),
        "cluster_resources": getattr(job, 'cluster_resources', None),
        "region": getattr(job, 'region', None),
    }


def _storage_record_to_dict(storage) -> Dict[str, Any]:
    """Convert a StorageRecord to a dictionary."""
    return {
        "name": getattr(storage, 'name', None),
        "launched_at": str(getattr(storage, 'launched_at', None)),
        "store": str(getattr(storage, 'store', None)),
        "status": str(getattr(storage, 'status', None)),
    }


def _instance_info_to_dict(info) -> Dict[str, Any]:
    """Convert an InstanceTypeInfo to a dictionary."""
    return {
        "instance_type": getattr(info, 'instance_type', None),
        "cloud": str(getattr(info, 'cloud', None)),
        "region": getattr(info, 'region', None),
        "price": getattr(info, 'price', None),
        "spot_price": getattr(info, 'spot_price', None),
        "memory": getattr(info, 'memory', None),
        "cpu_count": getattr(info, 'cpu_count', None),
        "accelerator_count": getattr(info, 'accelerator_count', None),
    }


def main():
    """Run the SkyPilot MCP server."""
    import asyncio
    asyncio.run(run_server())


async def run_server():
    """Run the MCP server using stdio transport."""
    async with stdio_server() as (read_stream, write_stream):
        await mcp.run(read_stream, write_stream, mcp.create_initialization_options())


if __name__ == "__main__":
    main()
