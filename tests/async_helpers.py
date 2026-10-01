"""异步测试隔离设施：执行真实函数，只替换模型、键盘和外部服务。"""

import asyncio
import unittest
from unittest.mock import patch

from tests.helpers import IsolatedTestCase, ScriptedClient, load_main_functions


class AsyncIsolatedTestCase(IsolatedTestCase, unittest.IsolatedAsyncioTestCase):
    """每例独立事件循环和临时目录；沿用禁止联网/启动进程的保护。"""

    def setUp(self):
        super().setUp()
        self.enterContext(patch("builtins.input", side_effect=AssertionError(
            "Async test attempted blocking terminal input")))


class AsyncScriptedClient(ScriptedClient):
    async def create(self, **kwargs):
        # 模拟模型请求让出事件循环；保留请求快照和意外多请求检查。
        await asyncio.sleep(0)
        return super().create(**kwargs)


def load_async_main(namespace, *names):
    # 不 import main_async：导入入口会读取本地配置并构造真实客户端。
    return load_main_functions(namespace, *names, source="main_async.py")
