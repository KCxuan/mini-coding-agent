import asyncio

from .async_client import AsyncMCPClient
from .manager import MCPManager
from .config import MCPServerConfig


class AsyncMCPManager(MCPManager):
    def __init__(self, configs: dict[str, MCPServerConfig], *, host_policy):
        # 复用旧管理器的工具池、命名检查和策略查询逻辑。
        # 不创建 AsyncBridge。
        self.configs = configs
        self.host_policy = host_policy

        self.clients: dict[str, AsyncMCPClient] = {}
        self.tool_policies: dict[str, str] = {}
        self._connect_lock = None

    async def connect_mcp(self, name):
        if self._connect_lock is None:
            self._connect_lock = asyncio.Lock()

        async with self._connect_lock:
            existing = self.clients.get(name)

            if existing is not None and existing.is_connected():
                names = ", ".join(
                    tool["name"] for tool in existing.tools
                ) or "(none)"
                return (
                    f"MCP server {name!r} already connected. "
                    f"Tools: {names}"
                )

            config = self.configs.get(name)
            if config is None:
                available = ", ".join(self.configs) or "(none)"
                return (
                    f"MCP server {name!r} not found in config. "
                    f"Available: {available}"
                )

            if existing is not None:
                await existing.disconnect()
                self.clients.pop(name, None)

            server = AsyncMCPClient(config)

            # connect() 自身负责失败、超时、取消后的清理。
            tools = await server.connect()

            # 成功后才发布客户端。
            self.clients[name] = server

            names = ", ".join(
                tool["name"] for tool in tools
            ) or "(none)"

            print(f"\033[34m  [mcp] connected: {name} -> {names}\033[0m")

            return (
                f"Connected to MCP server {name!r}. "
                f"Discovered {len(tools)} tools: {names}"
            )

    async def run_connect_mcp(self, name):
        try:
            return await self.connect_mcp(name)
        except Exception as exc:
            return f"Error: {exc}"

    async def disconnect_all_mcp(self):
        for name, server in list(self.clients.items()):
            try:
                await server.disconnect()
            except Exception as exc:
                print(f"\033[31m  [mcp] error: {name!r} -> {exc}\033[0m")
            finally:
                self.clients.pop(name, None)

        self.tool_policies.clear()