import threading
import time

from .tools.shell import ShellRunner, format_bash_output
from xml.sax.saxutils import escape

class BackgroundManager:
    def __init__(self, shell: ShellRunner):
        self.tasks = {}      # bg_0001 → {tool_use_id, command, status}
        self.results = {}    # bg_0001 → 输出文本
        self._ready = []     # 已完成、待收集的 task_id 列表
        self._counter = 0
        self._lock = threading.Lock()

        self.shell = shell

    def start(self, block) -> str:
        # 1. 校验：只有 bash、command 非空
        # 2. 生成 task_id = f"bg_{counter:04d}"
        # 3. 登记 tasks[task_id] = {..., status: "running"}
        # 4. threading.Thread(target=self._run, daemon=True).start()
        # 5. 立即返回 task_id
        if block.name != "bash" or not block.input.get("command"):
            return "Error: Invalid command"
        
        command = block.input.get("command")
        with self._lock:
            self._counter += 1
            task_id = f"bg_{self._counter:04d}"
            self.tasks[task_id] = {
                "tool_use_id": block.id,
                "command": command,
                "status": "running"
            }
            thread = threading.Thread(target=self._run, args=(task_id, command), daemon=True)
        try:
            thread.start()
        except Exception as e:
            with self._lock:
                self.tasks.pop(task_id, None)
            raise
        print(f"\033[34m  [background] started {task_id}: {command[:60]}\033[0m")
        return task_id

    def _run(self, task_id, command):
        # 1. 调用 _run_bash_process(command)
        # 2. 根据 exit_code 设 status = "completed" / "failed"
        # 3. 写入 results，追加到 _ready
        # 4. 清理进程
        exit_code = None
        try:
            output, exit_code = self.shell.run_bash_process(command)
            result = format_bash_output(output, exit_code)
            status = "completed" if exit_code == 0 else "failed"
        except Exception as e:
            result = f"Error: {e}"
            status = "failed"
        
        with self._lock:
            task = self.tasks.get(task_id, None)
            if task is None:
                return
            task["status"] = status
            task["exit_code"] = exit_code
            self.results[task_id] = result
            self._ready.append(task_id)
            

    def collect(self) -> list[str]:
        # 1. 首先获取 _ready 列表，再逐个处理。
        # 2. 对于每个 task_id，获取 task 和 result。
        # 3. 调用 shell.prepare_output(result, inline_limit=500) 获取 preview 和 full_path。
        # 4. 构建 notification，追加到 notifications 列表。
        notifications = []

        with self._lock:
            ready_ids = list(self._ready)

            # 先准备好全部通知，再移除原记录。
            for task_id in ready_ids:
                task = self.tasks[task_id]
                result = self.results[task_id]

                preview, full_path = self.shell.prepare_output(
                    result,
                    inline_limit=500,
                )

                exit_code = task.get("exit_code")
                exit_text = (
                    str(exit_code)
                    if exit_code is not None
                    else "unknown"
                )

                full_output_field = ""
                if full_path is not None:
                    full_output_field = (
                        f"  <full_output>{escape(full_path)}</full_output>\n"
                        "  <hint>需要更多详情时，使用 read_file "
                        "读取 full_output 指向的文件。</hint>\n"
                    )

                notifications.append(
                    "<task_notification>\n"
                    f"  <task_id>{escape(task_id)}</task_id>\n"
                    f"  <status>{escape(task['status'])}</status>\n"
                    f"  <exit_code>{exit_text}</exit_code>\n"
                    f"  <command>{escape(task['command'])}</command>\n"
                    f"{full_output_field}"
                    f"  <summary>{escape(preview)}</summary>\n"
                    "</task_notification>"
                )

            # 此时结果已保存到文件，
            # 或者完整内容已放进即将返回的通知。
            for task_id in ready_ids:
                self.tasks.pop(task_id, None)
                self.results.pop(task_id, None)

            self._ready.clear()

        for task_id in ready_ids:
            print(f"\033[34m  [background] collected {task_id}\033[0m")

        return notifications

    def has_running(self) -> bool:
        with self._lock:
            return any(task["status"] == "running" for task in self.tasks.values())
        
    def running_tasks(self) -> list[dict]:
        with self._lock:
            return [
                {"task_id": task_id, **task}
                for task_id, task in self.tasks.items()
                if task["status"] == "running"
            ]

    def inject_background_results(self, messages: list) -> int:
        # 将工具的返回结果注入到messages中，大模型会根据这些结果继续推理
        # 调用 collect_background_results()
        # 若有 notification，追加到 messages 最后一条 user message
        # 或新建一条 user message
        # 返回注入条数
        notifications = self.collect()
        if not notifications:
            return 0
        
        block = [{"type": "text", "text": item} for item in notifications]
        if messages and messages[-1].get("role") == "user":
            content = messages[-1].get("content")
            if isinstance(content, list):
                content.extend(block)
            else:
                messages[-1]["content"] = [
                    {"type": "text", "text": content},
                    *block,
                ]
        else:
            messages.append({"role": "user", "content": block})
        return len(notifications)

    def drain_background_tasks(
        self,
        messages: list,
        *,
        timeout: float = 120.0,
        poll_interval: float = 0.2,
    ) -> list[str]:
        deadline = time.monotonic() + timeout

        while True:
            self.inject_background_results(messages)

            if not self.has_running():
                return []

            if time.monotonic() > deadline:
                break

            time.sleep(poll_interval)

        self.inject_background_results(messages)

        return [
            task["task_id"]
            for task in self.running_tasks()
        ]


def should_run_background(tool_name, tool_input) -> bool:
    return tool_name == "bash" and tool_input.get("run_in_background") is True


