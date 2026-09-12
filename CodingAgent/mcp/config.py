import json
from dataclasses import dataclass
from pathlib import Path

@dataclass
class MCPServerConfig:
    name: str
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    url: str | None = None
    headers: dict[str, str] | None = None

    @property
    def transport_kind(self) -> str:
        if self.url:
            return "http"
        if self.command:
            return "stdio"
        raise ValueError(f"Invalid MCP server config: {self}, missing command or url")

def load_mcp_config(path: Path) -> dict[str, MCPServerConfig]:
    if not path.is_file():
        raise FileNotFoundError(f"MCP config file not found: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ValueError(f"Invalid MCP config file: {path}, {e}")
    if not isinstance(raw, dict):
        raise ValueError("MCP config must be a JSON object")
    
    servers = raw.get("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError("mcpServers must be a JSON object")

    loaded: dict[str, MCPServerConfig] = {}
    for name, spec in servers.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("MCP server name must be a non-empty string")
        if not isinstance(spec, dict):
            raise ValueError(f"MCP server spec for {name} must be a JSON object")
        
        command = spec.get("command")
        args = spec.get("args", [])
        env = spec.get("env")
        url = spec.get("url")
        headers = spec.get("headers")

        if (command and url) or (not command and not url):
            raise ValueError(f"MCP server {name} must have exactly one of command or url")
        if command is not None and not isinstance(command, str):
            raise ValueError(f"MCP server {name!r} command must be a string")
        if url is not None and not isinstance(url, str):
            raise ValueError(f"MCP server {name!r} url must be a string")
        if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
            raise ValueError(f"MCP server {name!r} args must be a list of strings")
        if env is not None and (
            not isinstance(env, dict)
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items())
        ):
            raise ValueError(f"MCP server {name!r} env must be a string-to-string object")
        if headers is not None and (
            not isinstance(headers, dict)
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items())
        ):
            raise ValueError(f"MCP server {name!r} headers must be a string-to-string object")
        if headers is not None and (url is None or url.strip() == ""):
            raise ValueError(f"MCP server {name!r} headers must be provided with url")


        loaded[name] = MCPServerConfig(
            name=name,
            command=command,
            args=list(args) if command else None,
            env=dict(env) if env else None,
            url=url,
            headers=dict(headers) if headers else None,
        )
    return loaded