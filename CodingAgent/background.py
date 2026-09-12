import threading
import time

from .tools.shell import ShellRunner, format_bash_output

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
        print(f"  [background] started {task_id}: {command[:60]}")
        return task_id

    def _run(self, task_id, command):
        # 1. 调用 _run_bash_process(command)
        # 2. 根据 exit_code 设 status = "completed" / "failed"
        # 3. 写入 results，追加到 _ready
        # 4. 清理进程
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
            self.results[task_id] = result
            self._ready.append(task_id)
            

    def collect(self) -> list[str]:
        # 1. 从 _ready 取出所有已完成任务
        # 2. 格式化为 <task_notification> XML 字符串
        # 3. 清空 _ready，返回 notification 列表
        # 4. 返回 notification 列表
        with self._lock:
            ready = []
            for task_id in self._ready:
                task = self.tasks.pop(task_id, None)
                result = self.results.pop(task_id, None)
                if task is not None:
                    ready.append((task_id, task, result))
            self._ready.clear()

        notifications = []
        for task_id, task, result in ready:
            notifications.append(
                f"<task_notification>\n"
                f"  <task_id>{task_id}</task_id>\n"
                f"  <status>{task['status']}</status>\n"
                f"  <command>{task['command']}</command>\n"
                f"  <summary>{result[:500]}</summary>\n"
                f"</task_notification>"
            )
            print(f"  [background] collected {task_id}: {task['status']}")
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


