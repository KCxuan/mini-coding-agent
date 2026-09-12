from dataclasses import dataclass, asdict
from pathlib import Path
import json
import secrets


@dataclass
class Task:
    """
    每个人物是一个json文件，存在./tasks目录下
    文件名是./tasks/{id}.json
    """
    id: str
    subject: str
    description: str
    status: str          # pending, in_progress, completed
    owner: str | None    # 负责该任务的agent
    blockedBy: list[str] # 依赖于哪些任务

class TaskStore:
    def __init__(self, tasks_dir: Path, *,workdir: Path):
        """
        任务存储类，负责管理任务的创建、读取、更新和删除。
        tasks_dir: 任务目录
        """
        self.directory = tasks_dir
        self.workdir = workdir.resolve()

    def _root(self, create: bool = False) -> Path:
        """
        获取任务目录的根路径。
        create: 如果为True，则创建任务目录。
        """
        if create:
            self.directory.mkdir(parents=True, exist_ok=True)
        root = self.directory.resolve()
        if not root.is_relative_to(self.workdir):
            raise ValueError("Task store escapes the workspace")
        return root
    
    def _path(self, task_id: str, create_root: bool = False) -> Path:
        """
        获取任务文件的路径。
        task_id: 任务ID
        create_root: 如果为True，则创建任务目录。
        """
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("Invalid task ID")
        root = self._root(create=create_root)
        path = (root / f"{task_id}.json").resolve()
        if not path.is_relative_to(root):
            raise ValueError("Invalid task ID")
        return path
    
    def exists(self, task_id: str) -> bool:
        """
        检查任务是否存在。
        task_id: 任务ID
        """
        return self._path(task_id).is_file()


    def create(self, subject: str, description: str, status: str = "pending", owner: str | None = None) -> Task:
        """
        检查 subject，分配随机 ID，随机ID要注意不能重复，再把任务写入 .tasks/{id}.json。
        新任务的 blockedBy 固定为空，工具结果会把运行时生成的 ID 返回给模型。
        subject: 任务主题
        description: 任务描述
        status: 任务状态
        owner: 负责该任务的agent
        blockedBy: 依赖于哪些任务
        """
        subject = subject.strip()
        if not subject:
            raise ValueError("Subject is required")
        
        self._root(create=True)
        for _ in range(100):
            task = Task(
                id = f"task_{secrets.token_hex(4)}",
                subject = subject,
                description = description,
                status = "pending",
                owner = None,
                blockedBy = [],
            )
            try:
                with self._path(task.id, create_root=True).open("x", encoding="utf-8") as file:
                    json.dump(asdict(task), file, ensure_ascii=False)
                return task
            except FileExistsError:
                continue
        raise RuntimeError("Failed to create task")

    def _depends_on(self, task_id: str, target_id: str) -> bool:
        """
        检查task_id任务是否依赖于target_id任务。
        task_id: 任务ID
        target_id: 目标任务ID
        """
        pending = [task_id]
        visited = set()
        while pending:
            current = pending.pop()
            if current == target_id:
                return True
            if current in visited:
                continue
            visited.add(current)
            pending.extend(self.load(current).blockedBy)
        return False
        

    def update_dependencies(self, task_id: str, add_blocked_by: list[str]) -> Task:
        """
        更新任务的依赖关系。
        task_id: 任务ID
        blockedby: 依赖于哪些任务
        """
        if not isinstance(add_blocked_by, list):
            raise ValueError("addBlockedBy must be a list of task IDs")
        
        task = self.load(task_id)
        if task.status != "pending" or task.owner is not None:
            raise ValueError(
                f"Task {task_id} dependencies can only be updated while "
                "pending and unowned"
            )
        # 去重
        dependencies = list(dict.fromkeys(add_blocked_by))
        for dependency in dependencies:
            if dependency == task_id:
                raise ValueError("Task cannot depend on itself")
            if not self.exists(dependency):
                raise ValueError(f"Dependency not found: {dependency}")
            if dependency not in task.blockedBy and self._depends_on(
                dependency, task_id
            ):
                raise ValueError(
                    f"Dependency cycle detected: {task_id} -> {dependency}"
                )

        task.blockedBy.extend(
            dependency for dependency in dependencies
            if dependency not in task.blockedBy
        )
        self.save(task)
        return task

    def save(self, task: Task) -> None:
        """
        保存或者更新任务到 .tasks/{id}.json。
        task: 任务
        """
        self._path(task.id, create_root=True).write_text(
            json.dumps(asdict(task), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load(self, task_id: str) -> Task | None:
        """
        从 .tasks/{id}.json 加载任务。
        task_id: 任务ID
        """
        data = json.loads(self._path(task_id).read_text(encoding="utf-8"))
        task = Task(**data)
        if not task.status in ("pending", "in_progress", "completed"):
            raise ValueError("Invalid task status")
        if task.id != task_id:
            raise ValueError("Task ID mismatch")
        return task

    def list(self) -> list[Task]:
        """
        列出所有任务。
        """
        if not self.directory.exists():
            return []
        root = self._root()
        return [self.load(path.stem) for path in sorted(root.glob("task_*.json"))]


class TaskBoard:
    def __init__(self, store: TaskStore):
        self.store = store
    
    def create_task(self,subject: str, description: str = "") -> Task:
        return self.store.create(subject, description)


    def update_task(self, task_id: str, addBlockedBy: list[str]) -> Task:
        return self.store.update_dependencies(task_id, addBlockedBy)


    def load_task(self, task_id: str) -> Task:
        return self.store.load(task_id)


    def list_tasks(self) -> list[Task]:
        return self.store.list()


    def get_task(self, task_id: str) -> str:
        return json.dumps(asdict(self.load_task(task_id)), indent=2)

    def incomplete_dependencies(self, task: Task) -> list[str]:
        incomplete = []
        for dependency in task.blockedBy:
            try:
                if self.store.load(dependency).status != "completed":
                    incomplete.append(dependency)
            except ValueError:
                incomplete.append(dependency)
        return incomplete

    def can_start(self, task_id: str) -> bool:
        return not self.incomplete_dependencies(self.load_task(task_id))

    def claim_task(self, task_id: str, owner: str = "agent") -> str:
        task = self.load_task(task_id)
        if task.status != "pending" or task.owner is not None:
            return f"Task {task_id} is not pending or unowned"
        dependencies = self.incomplete_dependencies(task)
        if dependencies:
            return f"Task {task_id} has incomplete dependencies: {dependencies}"
        task.owner = owner
        task.status = "in_progress"
        self.store.save(task)
        print(f"  [claim] {task.subject} -> in_progress (owner: {owner})")
        return f"Claimed {task.id} ({task.subject})"

    def complete_task(self, task_id: str, owner: str = "agent") -> str:
        """
        任务做完后，设为 completed。同时扫描所有其他任务，找出刚刚被解锁的下游任务
        """
        task = self.load_task(task_id)
        if task.status != "in_progress" or task.owner != owner:
            return f"Task {task_id} is not in progress or not owned by {owner}"
        ready_before = {
            candidate.id
            for candidate in self.store.list()
            if candidate.status == "pending"
            and candidate.owner is None
            and candidate.blockedBy
            and self.can_start(candidate.id)
        }
        task.status = "completed"
        self.store.save(task)
        unblocked = [candidate.subject for candidate in self.list_tasks()
                    if candidate.status == "pending"
                    and candidate.blockedBy
                    and candidate.id not in ready_before
                    and self.can_start(candidate.id)]
        print(f"  [complete] {task.subject}")
        message = f"Completed {task.id} ({task.subject})"
        if unblocked:
            message += f"\nUnblocked: {', '.join(unblocked)}"
            print(f"  [unblocked] {', '.join(unblocked)}")
        return message

    def run_create_task(self, subject: str, description: str = "") -> str:
        """
        创建一个任务。
        subject: 任务主题
        description: 任务描述
        """
        task = self.create_task(subject, description)
        print(f"  [create task] {task.subject}")
        return f"Created task {task.id}: ({task.subject})"

    def run_update_task(self, task_id: str, addBlockedBy: list[str]) -> str:
        """
        更新一个任务的依赖关系。
        task_id: 任务ID
        addBlockedBy: 依赖于哪些任务
        """
        task = self.update_task(task_id, addBlockedBy)
        dependencies = ",".join(task.blockedBy) or "(none)"
        print(f"  [update task] {task.subject} -> {dependencies}")
        return f"Updated task {task.id}: ({task.subject}) -> {dependencies}"

    def run_list_tasks(self) -> str:
        """
        列出所有任务。
        """
        tasks = self.list_tasks()
        if not tasks:
            return "No tasks. Use create_task to add some."
        lines = []
        for task in tasks:
            marker = {
                "pending": "[ ]",
                "in_progress": "[>]",
                "completed": "[x]",
            }.get(task.status, "[?]")
            dependencies = (
                f" (blockedBy: {', '.join(task.blockedBy)})"
                if task.blockedBy else ""
            )
            owner = f" [{task.owner}]" if task.owner else ""
            lines.append(
                f"{marker} {task.id}: {task.subject} "
                f"[{task.status}]{owner}{dependencies}"
            )
        return "\n".join(lines)

    def run_get_task(self, task_id: str) -> str:
        """
        获取一个任务的详细信息。
        task_id: 任务ID
        """
        return self.get_task(task_id)

    def run_claim_task(self, task_id: str, owner: str = "agent") -> str:
        """
        认领一个任务。
        task_id: 任务ID
        owner: 认领者
        """
        return self.claim_task(task_id, owner="agent")

    def run_complete_task(self, task_id: str, owner: str = "agent") -> str:
        """
        完成一个任务，并解锁所有下游任务。
        task_id: 任务ID
        owner: 完成者
        """
        return self.complete_task(task_id, owner="agent")