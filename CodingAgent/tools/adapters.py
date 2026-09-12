import json

from ..subagent.manager import SubagentManager
from ..subagent.results import format_subagent_result


class SubagentTools:
    def __init__(self, manager: SubagentManager):
        self.manager = manager

    def run_subagent(
        self,
        task_id: str,
        prompt: str,
    ) -> str:
        """启动后台运行，立即返回启动回执，而不是最终结果。"""
        try:
            run_id = self.manager.start(
                task_id,
                prompt,
            )

        except Exception as exc:
            return json.dumps(
                {
                    "status": "not_started",
                    "task_id": task_id,
                    "run_id": None,
                    "error": (
                        f"{type(exc).__name__}: {exc}"
                    ),
                },
                ensure_ascii=False,
            )

        return json.dumps(
            {
                "status": "started",
                "task_id": task_id,
                "run_id": run_id,
                "message": (
                    "子 Agent 已启动。最终结果将自动"
                    "作为 subagent_result 通知送达。"
                ),
            },
            ensure_ascii=False,
        )

    def inject_subagent_results(
        self,
        messages: list[dict],
    ) -> int:
        completed = self.manager.collect()

        if not completed:
            return 0

        blocks = [
            {
                "type": "text",
                "text": (
                    "<subagent_result>\n"
                    + format_subagent_result(state)
                    + "\n</subagent_result>"
                ),
            }
            for state in completed
        ]

        # 完成通知作为新的文本交给模型，
        # 不重复使用原 task 调用的 tool_use_id。
        if (
            messages
            and messages[-1].get("role") == "user"
        ):
            content = messages[-1].get("content")

            if isinstance(content, list):
                content.extend(blocks)

            else:
                messages[-1]["content"] = [
                    {
                        "type": "text",
                        "text": str(content),
                    },
                    *blocks,
                ]

        else:
            messages.append({
                "role": "user",
                "content": blocks,
            })

        return len(completed)

    def run_subagent_status(self, run_id: str) -> str:
        return json.dumps(
            self.manager.get(run_id),
            ensure_ascii=False,
            indent=2,
        )

    def run_subagent_cancel(self, run_id: str) -> str:
        return json.dumps(
            self.manager.cancel(run_id),
            ensure_ascii=False,
            indent=2,
        )

def run_compact(**kwargs) -> str:
    return f"本轮工具调用存在压缩请求，agent将会首先执行其他工具调用请求，最后压缩"