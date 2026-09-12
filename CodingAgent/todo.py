"""Historical in-memory TODO implementation; the Agent currently uses the task board."""

import ast
import json


class TODOManager:
    def __init__(self):
        self.items = []
    def update(self, todos: list | None) -> str:
        # parse and validate todos
        if isinstance(todos, str):
            try:
                todos = json.loads(todos)
            except json.JSONDecodeError:
                try:
                    todos = ast.literal_eval(todos)
                except (SyntaxError, ValueError):
                    return "Invalid todos format"
        if not isinstance(todos, list):
            raise ValueError("Todos must be a list")
        if len(todos) > 20:
            raise ValueError("Too many todos")
        
        validated = []
        in_progress_count = 0
        for index, item in enumerate(todos):
            if not isinstance(item, dict):
                raise ValueError(f"Todo {index} is not an object")
            content = item.get("content", "").strip()
            status = item.get("status", "pending").strip().lower()
            if not content:
                raise ValueError(f"Todo {index} has no content")
            if status not in ["pending", "in_progress", "completed"]:
                raise ValueError(f"Todo {index} has an invalid status")
            validated.append({
                "content": content,
                "status": status,
            })
            if status == "in_progress":
                in_progress_count += 1
        
        if in_progress_count > 1:
            raise ValueError("Too many in_progress todos")
        
        self.items = validated
        return self.render()
        

    def render(self) -> str:
        # []pending, [>]in_progress, [x]completed
        lines = []
        for item in self.items:
            status = "[]" if item["status"] == "pending" else "[>]" if item["status"] == "in_progress" else "[x]"
            lines.append(f"{status} {item['content']}")
        done = [item for item in self.items if item["status"] == "completed"]
        lines.append(f"Completed: {len(done)}/{len(self.items)}")
        return "\n".join(lines)

    def run_todo_write(self, todos: list) -> str:
        try:
            return self.update(todos)
        except Exception as e:
            return f"Error: {e}"
