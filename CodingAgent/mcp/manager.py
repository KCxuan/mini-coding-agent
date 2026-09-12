import re

from .bridge import AsyncBridge
from .client import MCPClient
from .config import MCPServerConfig

_DISALLOWED_CHARS = re.compile(r"[^a-zA-Z0-9_-]")

def normalize_mcp_name(name: str) -> str:
    normalized = _DISALLOWED_CHARS.sub("_", name)
    if not normalized:
        raise ValueError(f"Invalid MCP name: {name!r}")
    return normalized

class MCPManager:
    def __init__(
        self,
        configs: dict[str, MCPServerConfig],
        bridge: AsyncBridge,
        *,
        host_policy: dict[tuple[str, str], str],
    ):
        self.configs = configs
        self.bridge = bridge
        self.host_policy = host_policy

        self.clients: dict[str, MCPClient] = {}
        self.tool_policies: dict[str, str] = {}

    def connect_mcp(self, name: str) -> str:
        existing = self.clients.get(name)
        if existing is not None and existing.is_connected():
            names = ", ".join(tool["name"] for tool in existing.tools) or "(none)"
            return f"MCP server {name!r} already connected. Tools: {names}"
        
        config = self.configs.get(name)
        if config is None:
            avaliable = ", ".join(self.configs) or "(none)"
            return f"MCP server {name!r} not found in config. Available: {avaliable}"
        
        server = MCPClient(config, self.bridge)
        try:
            tools = server.connect()
        except Exception as e:
            return f"Error: {e}"

        self.clients[name] = server
        names = ", ".join(tool["name"] for tool in tools) or "(none)"
        print(f"  [mcp] connected: {name} -> {names}")
        return (
            f"Connected to MCP server {name!r}. "
            f"Discovered {len(tools)} tools: {names}"
        )

    def run_connect_mcp(self, name: str) -> str:
        try:
            return self.connect_mcp(name)
        except Exception as e:
            return f"Error: {e}"

    def disconnect_all_mcp(self) -> None:
        for server_name, server in list(self.clients.items()):
            try:
                server.disconnect()
            except Exception as e:
                print(f"  [mcp] error: {server_name!r} -> {e}")
            self.clients.pop(server_name, None)
        
        self.tool_policies.clear()
        self.bridge.close()

    def connect_tool(self) -> dict:
        return {
            "name": "connect_mcp",
            "description": (
                "Connect to a configured MCP server and discover its tools. "
                "Call this before using any mcp__server__tool. "
                "Available servers are listed in the enum."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "enum": list(self.configs) or [
                            "(no servers configured)"
                        ],
                    }
                },
                "required": ["name"],
            },
        }
    
    def assemble_tool_pool(
        self,
        builtin_tools: list[dict],
        builtin_handlers: dict,
    ) -> tuple[list[dict], dict]:
        """每轮把内置工具和已连接 MCP 工具装进同一个池。"""
        tools = [*builtin_tools, self.connect_tool()]
        handlers = {**builtin_handlers, "connect_mcp": self.run_connect_mcp}
        policies: dict[str, str] = {}
        origins = {tool["name"]: f"built-in tool {tool['name']!r}" for tool in tools}

        for server_name, server in self.clients.items():
            if not server.is_connected():
                continue
            safe_server = normalize_mcp_name(server_name)
            for tool_def in server.tools:
                raw_name = tool_def["name"]
                safe_tool = normalize_mcp_name(raw_name)
                prefixed = f"mcp__{safe_server}__{safe_tool}"
                if len(prefixed) > 64:
                    raise ValueError(f"MCP tool name {raw_name!r} too long: {len(prefixed)} > 64")
                origin = f"MCP tool {server_name!r}.{raw_name!r}"
                if prefixed in origins:
                    raise ValueError(
                        "MCP tool name collision after normalization: "
                        f"{prefixed!r} maps both {origins[prefixed]} and {origin}"
                    )
                schema = tool_def.get("input_schema") or {"type": "object", "properties": {}}
                if not isinstance(schema, dict) or schema.get("type", "object") != "object":
                    raise ValueError(f"Invalid input schema for {origin}")
                
                origins[prefixed] = origin
                tools.append({
                    "name": prefixed,
                    "description": tool_def.get("description", ""),
                    "input_schema": schema,
                })
                handlers[prefixed] = (
                    lambda *, client=server, tool=raw_name, **kwargs:
                    client.call_tool(tool, kwargs)
                )
                policies[prefixed] = self.host_policy.get(
                    (safe_server, raw_name), "confirm"
                )
        self.tool_policies = policies
        return tools, handlers

    def available_servers(self) -> list[str]:
        return list(self.configs)

    def connected_servers(self) -> list[str]:
        return [
            name
            for name, server in self.clients.items()
            if server.is_connected()
        ]

    def get_tool_policy(self, tool_name: str) -> str:
        return self.tool_policies.get(tool_name, "confirm")