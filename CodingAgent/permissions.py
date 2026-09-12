import re
import threading
from collections.abc import Callable
from pathlib import Path

DENY_LIST = [
    "rm -rf /", "sudo", "shutdown", "reboot",
    "mkfs", "dd if=", "> /dev/sda",
]

# 第一道拒绝：权限拒绝，当命令包含在DENY_LIST中返回拒绝
def check_deny_list(command: str) -> str | None:
    for pattern in DENY_LIST:
        if pattern in command:
            return f"Blocked: '{pattern}' is on the deny list"
    return None

# 第二道拒绝：规则匹配，负责说明在什么情况下应该问用户
DESTRUCTIVE_COMMAND_WORD = re.compile(
    r"(?i)(?:^|[;&|()\n])\s*(?:rm|del)(?=\s|$|[;&|()])"
)

def contains_destructive_command(command: str) -> bool:
    return bool(DESTRUCTIVE_COMMAND_WORD.search(command))

def ask_user(
    tool_name: str,
    args: dict,
    reason: str,
) -> str:
    # 暂不实现跨线程审批队列。
    # 后台操作需要询问时，直接拒绝，
    # 由子 Agent 在交接中说明阻碍。
    if (
        threading.current_thread()
        is not threading.main_thread()
    ):
        print(
            f"[permission] 后台工具 {tool_name} "
            f"需要确认，已拒绝：{reason}"
        )
        return "deny"

    print(f"\n⚠  {reason}")
    print(f"   Tool: {tool_name}({args})")

    choice = input(
        "   Allow? [y/N] "
    ).strip().lower()

    return (
        "allow"
        if choice in ("y", "yes")
        else "deny"
    )

class PermissionManager:
    def __init__(
        self,
        workdir: Path,
        *,
        get_mcp_policy: Callable[[str], str],
    ):
        self.workdir = workdir.resolve()
        self.get_mcp_policy = get_mcp_policy

        self.rules = [
            {
                "tools": ["read_file", "write_file", "edit_file"],
                "check": lambda args: not (
                    self.workdir / args.get("path", "")
                ).resolve().is_relative_to(self.workdir),
                "message": "Access outside workspace",
            },
            {
                "tools": ["bash"],
                "check": lambda args: (
                    contains_destructive_command(
                        args.get("command", "")
                    )
                    or any(
                        kw in args.get("command", "")
                        for kw in ["rm ", "> /etc/", "chmod 777"]
                    )
                ),
                "message": "Potentially destructive command",
            },
        ]

    def check_rules(self, tool_name: str, args: dict) -> str | None:
        for rule in self.rules:
            if tool_name in rule["tools"] and rule["check"](args):
                return rule["message"]
        return None

    def check_permission(self, block) -> str | None:
        if block.name == "bash":
            """
            检查命令是否在拒绝列表DENY_LIST中,
            如果包含，则返回拒绝原因
            """
            reason = check_deny_list(block.input.get("command", ""))
            if reason:
                print(f"\n⛔ {reason}")
                return reason
        
        if block.name == "bash" or block.name in ["read_file", "write_file", "edit_file"]:
            """
            检查命令是否在权限规则PERMISSION_RULES中,
            如果包含，则返回拒绝原因
            """
            reason = self.check_rules(block.name, block.input)
            if reason:
                decision = ask_user(block.name, block.input, reason)
                if decision == "deny":
                    return reason

        if block.name.startswith("mcp__"):
            """
            检查MCP工具是否在mcp_tool_policies中,
            如果包含，则返回拒绝原因
            """
            policy = self.get_mcp_policy(block.name)
            if policy != "allow":
                decision = ask_user(block.name, block.input, "External MCP tool")
                if decision == "deny":
                    return "Permission denied by user"

        return None