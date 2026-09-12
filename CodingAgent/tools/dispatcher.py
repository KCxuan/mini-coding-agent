from ..background import BackgroundManager, should_run_background
from ..hooks import HookRegistry


class ToolDispatcher:
    def __init__(
        self,
        hooks: HookRegistry,
        background: BackgroundManager,
    ):
        self.hooks = hooks
        self.background = background

    def execute_tool(
        self,
        tool_call,
        handlers: dict,
        *,
        allow_background: bool = True,
    ) -> str:
        blocked = self.hooks.trigger("PreToolUse", tool_call)
        if blocked:
            return str(blocked)

        if allow_background and should_run_background(
            tool_call.name,
            tool_call.input,
        ):
            try:
                task_id = self.background.start(tool_call)
                output = (
                    f"Background task started: {task_id}\n"
                    "The result will be collected and injected "
                    "into the messages later."
                )
            except Exception as e:
                output = f"Error: {e}"
        else:
            handler = handlers.get(tool_call.name)
            try:
                output = (
                    handler(**tool_call.input)
                    if handler
                    else f"Unknown: {tool_call.name}"
                )
            except Exception as e:
                output = f"Error: {e}"

        self.hooks.trigger("PostToolUse", tool_call, output)
        return str(output)