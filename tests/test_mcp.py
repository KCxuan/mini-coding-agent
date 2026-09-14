"""MCP 配置与工具池测试；用替身服务，不启动 stdio/HTTP 服务。"""

import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from CodingAgent.mcp.client import MCPClient
from CodingAgent.mcp.config import MCPServerConfig, load_mcp_config
from CodingAgent.mcp.manager import MCPManager
from tests.helpers import IsolatedTestCase


def fake_server(*tool_names):
    server = Mock()
    server.is_connected.return_value = True
    server.tools = [{"name": name, "description": name,
                     "input_schema": {"type": "object", "properties": {}}}
                    for name in tool_names]
    return server


class MCPTests(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.config_path = self.workdir / "mcp.json"
        self.bridge = Mock()
        self.manager = MCPManager({}, self.bridge, host_policy={})

    def write_config(self, value):
        self.config_path.write_text(json.dumps(value), encoding="utf-8")

    def test_valid_stdio_and_http_configs_are_loaded(self):
        self.write_config({"mcpServers": {
            "local": {"command": "python", "args": ["server.py"]},
            "remote": {"url": "https://example.invalid/mcp", "headers": {"X-Test": "fake"}},
        }})
        result = load_mcp_config(self.config_path)
        self.assertEqual(result["local"].transport_kind, "stdio")
        self.assertEqual(result["remote"].transport_kind, "http")
        self.assertEqual(result["local"].args, ["server.py"])
        self.assertEqual(result["remote"].headers, {"X-Test": "fake"})

    def test_invalid_config_shapes_and_transports_are_rejected(self):
        for value in (
            [], {"mcpServers": []}, {"mcpServers": {"x": {}}},
            {"mcpServers": {"x": {"command": "python", "url": "https://example.invalid"}}},
            {"mcpServers": {"x": {"command": "python", "args": "server.py"}}},
            {"mcpServers": {"x": {"command": "python", "env": {"PORT": 1}}}},
            {"mcpServers": {"x": {"command": "python", "headers": {"X": "y"}}}},
        ):
            with self.subTest(value=value):
                self.write_config(value)
                with self.assertRaises(ValueError):
                    load_mcp_config(self.config_path)

    def test_missing_and_malformed_config_fail_clearly(self):
        with self.assertRaises(FileNotFoundError):
            load_mcp_config(self.config_path)
        self.config_path.write_text("{broken", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Invalid MCP config"):
            load_mcp_config(self.config_path)

    def test_tool_name_normalization_collision_is_rejected(self):
        self.manager.clients = {"server": fake_server("a.b", "a b")}
        with self.assertRaisesRegex(ValueError, "collision"):
            self.manager.assemble_tool_pool([], {})

    def test_server_name_normalization_collision_is_rejected(self):
        self.manager.clients = {"a.b": fake_server("lookup"), "a b": fake_server("lookup")}
        with self.assertRaisesRegex(ValueError, "collision"):
            self.manager.assemble_tool_pool([], {})

    def test_builtin_name_collision_is_rejected(self):
        self.manager.clients = {"one": fake_server("lookup")}
        with self.assertRaisesRegex(ValueError, "collision"):
            self.manager.assemble_tool_pool([{"name": "mcp__one__lookup"}], {})

    def test_overlong_name_and_non_object_schema_are_rejected(self):
        for invalid_schema in (False, True):
            with self.subTest(invalid_schema=invalid_schema):
                server = fake_server("lookup" if invalid_schema else "x" * 65)
                if invalid_schema:
                    server.tools[0]["input_schema"] = {"type": "array"}
                self.manager.clients = {"one": server}
                with self.assertRaises(ValueError):
                    self.manager.assemble_tool_pool([], {})

    def test_handlers_route_to_correct_server_and_original_tool_name(self):
        one, two = fake_server("lookup"), fake_server("lookup")
        one.call_tool.return_value, two.call_tool.return_value = "one result", "two result"
        self.manager.clients = {"one": one, "two": two}
        _, handlers = self.manager.assemble_tool_pool([], {})
        self.assertEqual(handlers["mcp__one__lookup"](query="A"), "one result")
        self.assertEqual(handlers["mcp__two__lookup"](query="B"), "two result")
        one.call_tool.assert_called_once_with("lookup", {"query": "A"})
        two.call_tool.assert_called_once_with("lookup", {"query": "B"})

    def test_external_tools_default_to_confirmation(self):
        self.manager.clients = {"one": fake_server("lookup", "write")}
        self.manager.host_policy = {("one", "lookup"): "allow"}
        self.manager.assemble_tool_pool([], {})
        self.assertEqual(self.manager.get_tool_policy("mcp__one__lookup"), "allow")
        self.assertEqual(self.manager.get_tool_policy("mcp__one__write"), "confirm")
        self.assertEqual(self.manager.get_tool_policy("unknown"), "confirm")

    def test_disconnected_server_tools_are_removed_from_pool_and_policies(self):
        server = fake_server("lookup")
        self.manager.clients = {"one": server}
        self.manager.assemble_tool_pool([], {})
        server.is_connected.return_value = False
        tools, handlers = self.manager.assemble_tool_pool([], {})
        self.assertNotIn("mcp__one__lookup", [tool["name"] for tool in tools])
        self.assertNotIn("mcp__one__lookup", handlers)
        self.assertEqual(self.manager.tool_policies, {})

    def test_connect_failure_does_not_publish_client(self):
        self.manager.configs = {"one": MCPServerConfig("one", command="unused")}
        with patch("CodingAgent.mcp.manager.MCPClient") as factory:
            factory.return_value.connect.side_effect = RuntimeError("fake connect failure")
            result = self.manager.connect_mcp("one")
        self.assertIn("fake connect failure", result)
        self.assertEqual(self.manager.clients, {})

    def test_disconnect_continues_after_one_server_fails(self):
        one, two = fake_server("a"), fake_server("b")
        one.disconnect.side_effect = RuntimeError("fake disconnect failure")
        self.manager.clients = {"one": one, "two": two}
        self.manager.disconnect_all_mcp()
        two.disconnect.assert_called_once()
        self.bridge.close.assert_called_once()
        self.assertEqual(self.manager.clients, {})

    def test_client_formats_error_and_structured_results(self):
        client = MCPClient(MCPServerConfig("one", command="unused"), self.bridge)
        error = SimpleNamespace(is_error=True, content=[SimpleNamespace(type="text", text="bad input")])
        self.assertEqual(client._format_mcp_tool_result(error), "MCP error: bad input")
        structured = SimpleNamespace(is_error=False, content=[], structured_content={"count": 2})
        self.assertEqual(json.loads(client._format_mcp_tool_result(structured)), {"count": 2})
