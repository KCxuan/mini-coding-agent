from copy import deepcopy

BASE_TOOLS = [
    {
        "name": "bash",
        "description": (
            "Run a shell command. Set run_in_background to true "
            "for long-running independent commands."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                },
                "run_in_background": {
                    "type": "boolean",
                }
            },
            "required": ["command"]
        }
    },
    {
        "name": "read_file",
        "description": (
            "Read a numbered page of a UTF-8 file. offset is the 1-based "
            "starting line (default 1). limit defaults to 200 and is capped "
            "at 200 lines. Use the returned next offset for the next page."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200,
                    "default": 200,
                },
                "offset": {
                    "type": "integer",
                    "minimum": 1,
                    "default": 1,
                },
            },
            "required": ["path"]
        }
    },
    {
        "name": "write_file",
        "description": "Write to a file",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                },
                "content": {
                    "type": "string",
                }
            },
            "required": ["path", "content"]
        }
    },
    {
        "name": "edit_file",
        "description": "Edit to a file by replacing content with new content",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                },
                "old_content": {
                    "type": "string",
                },
                "new_content": {
                    "type": "string",
                }
            },
            "required": ["path", "old_content", "new_content"]
        }
    },
    {
        "name": "glob",
        "description": "Search for files matching a pattern,** matches recursively.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                }
            },
            "required": ["pattern"]
        }
    },
    {
        "name": "load_skill",
        "description": "Load a skill from the skills directory",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
            "required": ["name"]
        }
    },
]

TASK_BOARD_TOOLS = [
    {
        "name": "create_task", 
        "description": "Create a task and return its runtime-generated ID.",
        "input_schema": {
            "type": "object", 
            "properties": {
                "subject": {
                    "type": "string"
                }, 
                "description": {
                    "type": "string"
                }
            }, 
            "required": ["subject"], 
            "additionalProperties": False
        }
    },
    {
        "name": "update_task", 
        "description": "Add dependencies using IDs returned by create_task.",
        "input_schema": {
            "type": "object", 
            "properties": {
                "task_id": {
                    "type": "string", 
                    "pattern": "^task_[0-9a-f]{8}$"
                }, 
                "addBlockedBy": {
                    "type": "array", 
                    "items": {
                        "type": "string", 
                        "pattern": "^task_[0-9a-f]{8}$"
                    }, 
                    "minItems": 1
                }
            }, 
            "required": ["task_id", "addBlockedBy"], 
            "additionalProperties": False
        }
    },
    {
        "name": "list_tasks", 
        "description": "List tasks with status, owner, and dependencies.",
        "input_schema": {
            "type": "object", 
            "properties": {}
        }
    },
    {
        "name": "get_task", 
        "description": "Get a task by ID.",
        "input_schema": {
            "type": "object", 
            "properties": {"task_id": {"type": "string"}}, 
            "required": ["task_id"],
        }
    },
    {
        "name": "claim_task", 
        "description": "Claim a pending task whose dependencies are complete.",
        "input_schema": {
            "type": "object", 
            "properties": {"task_id": {"type": "string"}}, 
            "required": ["task_id"],
        }
    },
    {"name": "complete_task", "description": "Complete the task claimed by this agent.",
     "input_schema": {
            "type": "object", 
            "properties": {"task_id": {"type": "string"}}, 
            "required": ["task_id"],
        }
    },
]

COMPACT_TOOL = {
    "name": "compact",
    "description": "Compact the conversation history to fit within the context limit.",
    "input_schema": {
        "type": "object",
        "properties": {

        },
    },
}

SUBAGENT_STATUS_TOOL = {
    "name": "subagent_status",
    "description": (
        "Query one subagent run by run_id. "
        "Returns running, cancelling, its final result, or not_found. "
        "Query only when needed; final results arrive automatically. "
        "This does not consume the automatic completion notification."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "run_id": {
                "type": "string",
                "description": "The run_id returned by the task tool.",
            },
        },
        "required": ["run_id"],
        "additionalProperties": False,
    },
}

SUBAGENT_CANCEL_TOOL = {
    "name": "subagent_cancel",
    "description": (
        "Request cancellation of one subagent run by run_id. "
        "A cancelling receipt means cancellation is pending. "
        "An operation already in progress may finish or time out "
        "before the final cancelled result arrives automatically. "
        "Already finished runs keep their original results."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "run_id": {
                "type": "string",
                "description": "The run_id returned by the task tool.",
            },
        },
        "required": ["run_id"],
        "additionalProperties": False,
    },
}


def build_task_tool(max_subagents: int) -> dict:
    return {
        "name": "task",
        "description": (
            "Start a background subagent for an existing claimed task. "
            "Pass its task_id and a self-contained prompt. "
            "Returns a startup receipt immediately, not the final result. "
            f"At most {max_subagents} subagents may be active. "
            "If capacity is full, wait for existing runs rather than "
            "repeatedly retrying. "
            "Final results arrive automatically in subagent_result messages. "
            "Do not complete the task merely because its subagent started. "
            "The subagent is read-only; delegate code reading, investigation, "
            "or review, not file changes or command execution. "
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The ID of an existing claimed task.",
                },
                "prompt": {
                    "type": "string",
                    "description": (
                        "The specific work, necessary context, "
                        "constraints, and expected result."
                    ),
                },
            },
            "required": ["task_id", "prompt"],
            "additionalProperties": False,
        },
    }

def build_tool_schemas(*, max_subagents: int) -> list[dict]:
    return deepcopy([
        *BASE_TOOLS,
        build_task_tool(max_subagents),
        *TASK_BOARD_TOOLS,
        COMPACT_TOOL,
        SUBAGENT_STATUS_TOOL,
        SUBAGENT_CANCEL_TOOL,
    ])