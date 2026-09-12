import asyncio
import threading
from collections.abc import Coroutine
from typing import Any

class AsyncBridge:
    """在后台跑一条常驻事件循环，供同步代码提交协程"""

    def __init__(self):
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
    
    @property
    def running(self) -> bool:
        return self._loop is not None and self._loop.is_running()

    def start(self) -> None:
        with self._lock:
            if self.running:
                return
            ready = threading.Event()
            self._thread = threading.Thread(
                target=self._run_loop,
                args=(ready,),
                name="mcp-async-bridge",
                daemon=True,
            )
            self._thread.start()
            if not ready.wait(timeout=10):
                raise RuntimeError("Failed to start MCP async bridge")
    
    def _run_loop(self, ready: threading.Event) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        ready.set()
        loop.run_forever()
        loop.close()
        self._loop = None
        
    def run(self, coro: Coroutine[Any, Any, Any], timeout: float = 120.0) -> Any:
        """
        提交一个协程到事件循环，等待结果或超时
        """
        self.start()
        if self._loop is None:
            raise RuntimeError("MCP async bridge not running")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=timeout)
        except asyncio.TimeoutError:
            future.cancel()
            raise TimeoutError(f"MCP async bridge timed out after {timeout} seconds")
    
    def submit(self, coro: Coroutine[Any, Any, Any]):
        """提交一个协程到事件循环但不等待他结束，用来挂住MCP的async with"""
        self.start()
        if self._loop is None:
            raise RuntimeError("MCP async bridge not running")
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def call_soon(self, fn, *args) -> None:
        """在事件循环中调用一个函数"""
        if self._loop is None:
            raise RuntimeError("MCP async bridge not running")
        self._loop.call_soon_threadsafe(fn, *args)
    
    def close(self, timeout: float = 10.0) -> None:
        """关闭事件循环"""
        loop = self._loop
        thread = self._thread
        if loop is None or thread is None:
            return
        
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=timeout)
        self._thread = None