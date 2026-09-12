from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

from mcp import Client, StdioServerParameters

from .bridge import AsyncBridge
from .config import MCPServerConfig

def _schema_to_dict(schema: Any) -> dict:
    """将MCP schema转换为字典"""
    if schema is None:
        return {"type": "object", "properties": {}}
    if isinstance(schema, dict):
        return schema
    if hasattr(schema, "model_dump"):
        dumped = schema.model_dump(by_alias=False, exclude_none=True)
        return dumped if isinstance(dumped, dict) else {"type": "object"}
    return {"type": "object", "properties": {}}

class MCPClient:
    def __init__(self, config: MCPServerConfig, bridge: AsyncBridge):
        self.config = config
        self.name = config.name
        self.transport_kind = config.transport_kind
        self.tools: list[dict] = []
        self._session = None
        self._bridge = bridge
        self._closed: asyncio.Event | None = None
        self._ready = threading.Event()
        self._error: str | None = None
        self._session_future = None

    def _target(self) -> StdioServerParameters | str:
        if self.config.url:
            return self.config.url
        if not self.config.command:
            raise ValueError(f"Invalid MCP server config: {self.config}, missing command")
        return StdioServerParameters(
            command=self.config.command, 
            args=self.config.args or [], 
            env=self.config.env
        )
        

    def is_connected(self) -> bool:
        return self._session is not None


    def connect(self, timeout: float = 60.0) -> list[dict]:
        if self.is_connected():
            return self.tools
        self._ready.clear()
        self._error = None
        self._session_future = self._bridge.submit(self._session_loop())
        if not self._ready.wait(timeout=timeout):
            raise TimeoutError(f"Connecting MCP server {self.name!r} timed out")
        if self._error:
            raise RuntimeError(self._error)
        return self.tools
    
    async def _list_all_tools(self, session: Client) -> list[dict]:
        collected = []
        cursor = None
        while True:
            page = await session.list_tools(cursor=cursor)
            for tool in page.tools:
                collected.append({
                    "name": tool.name,
                    "description": tool.description or "",
                    "input_schema": _schema_to_dict(tool.input_schema),
                })
            if not page.next_cursor:
                return collected
            cursor = page.next_cursor

    async def _open_session(self):
        """
        打开一个MCP会话，并列出所有工具
        """
        if self.config.url and self.config.headers:
            """
            http形式连接MCP服务器
            """
            import httpx2
            from mcp.client.streamable_http import streamable_http_client

            timeout = httpx2.Timeout(30.0, read=300.0)
            async with httpx2.AsyncClient(
                headers=self.config.headers,
                timeout=timeout,
            ) as http:
                transport = streamable_http_client(
                    self.config.url,
                    http_client=http,
                )
                async with Client(transport) as session:
                    yield session
            return
        """
        stdio形式连接MCP服务器
        """
        async with Client(self._target()) as session:
            yield session


    async def _session_loop(self) -> None:
        # asyncio.Event 必须在 bridge 的 loop 里创建
        self._closed = asyncio.Event()
        try:
            async for session in self._open_session():
                self._session = session
                self.tools = await self._list_all_tools(session)
                # 设置ready事件，通知bridge连接成功
                self._ready.set()
                # 等待关闭事件，通知bridge连接关闭
                await self._closed.wait()
        except Exception as error:
            self._error = (
                f"Failed to connect MCP server {self.name!r}: "
                f"{type(error).__name__}: {error}"
            )
            self._ready.set()
        finally:
            # 清理会话和关闭事件
            self._session = None
            self._closed = None

    def disconnect(self, timeout: float = 10.0) -> None:
        closed = self._closed
        if closed is not None:
            # 通知bridge连接关闭，触发_session_loop中的await self._closed.wait()
            self._bridge.call_soon(closed.set)
        future = self._session_future
        if future is not None:
            try:
                future.result(timeout=timeout)
            except TimeoutError:
                future.cancel()
            self._session_future = None
        self.tools = []

    def call_tool(self, tool_name: str, args: dict | None = None, timeout: float = 120.0) -> str:
        if self._session is None:
            return f"MCP error: server {self.name!r} is not connected"
        try:
            result = self._bridge.run(
                self._session.call_tool(tool_name, args or {}),
                timeout=timeout,
            )
            return self._format_mcp_tool_result(result)
        except TimeoutError:
            return (
                f"MCP error: calling {tool_name!r} on {self.name!r} "
                f"timed out after {timeout}s"
            )
        except Exception as error:
            return f"MCP error: {type(error).__name__}: {error}"

    def _format_mcp_tool_result(self, result: Any) -> str:
        """把 SDK 的 CallToolResult 收成一段给模型看的文字。"""
        is_error = bool(getattr(result, "is_error", False))
        texts: list[str] = []
        for block in getattr(result, "content", None) or []:
            if getattr(block, "type", None) == "text":
                text = getattr(block, "text", "")
                if text:
                    texts.append(str(text))
        body = "\n".join(texts).strip()
        if not body:
            structured = getattr(result, "structured_content", None)
            if structured is not None:
                body = json.dumps(structured, ensure_ascii=False)
        if not body:
            body = "(empty MCP tool result)"
        if is_error:
            return f"MCP error: {body}"
        return body