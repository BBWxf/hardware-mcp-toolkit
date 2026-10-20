"""MCP 工具与真实 stdio 通信测试；只使用假安装或静态查询，不访问 PK4。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult

from ipecmd import Ipecmd
from server import create_server


class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.exe = self.root / "ipecmd.exe"
        self.exe.touch()
        self.config = self.root / "config.json"
        self.config.write_text(json.dumps({
            "ipe": {"executable_path": str(self.exe)},
            "target": {"device": None, "voltage_v": None},
        }), encoding="utf-8")
        self.backend = Ipecmd(self.config)

    async def test_tool_names_and_annotations(self):
        with patch("ipecmd.subprocess.Popen") as popen:
            server = create_server(self.backend)
            tools = {tool.name: tool for tool in await server.list_tools()}
            popen.assert_not_called()
        self.assertEqual(set(tools), {
            "get_environment", "get_configuration", "list_programmers", "preview_operation",
            "program_device", "verify_device", "erase_device", "blank_check", "read_device",
        })
        self.assertTrue(tools["get_environment"].annotations.readOnlyHint)
        self.assertTrue(tools["erase_device"].annotations.destructiveHint)
        self.assertFalse(tools["read_device"].annotations.readOnlyHint)

    async def test_operation_error_retains_raw_structured_result(self):
        backend_result = {"status": "failed", "exit_code": 7, "stdout": "Verify Target Failed.",
                          "stderr": "", "log_file": "test.json"}
        server = create_server(self.backend)
        with patch.object(self.backend, "execute", return_value=backend_result) as execute:
            result = await server.call_tool("verify_device", {})
        self.assertIsInstance(result, CallToolResult)
        self.assertTrue(result.isError)
        self.assertEqual(result.structuredContent, backend_result)
        execute.assert_called_once()

    async def test_corrupt_jar_environment_is_tool_error(self):
        self.exe.with_name("ipecmd.jar").write_bytes(b"not a ZIP archive")
        result = await create_server(self.backend).call_tool("get_environment", {})
        self.assertTrue(result.isError)
        self.assertIn("error", result.structuredContent)

    async def test_stdio_initialization_listing_and_validation(self):
        script = Path(__file__).resolve().parents[1] / "server.py"
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[str(script), "--config", str(self.config)],
            cwd=str(self.root),
        )
        with anyio.fail_after(30):
            async with stdio_client(parameters) as (reader, writer):
                async with ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=15)) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    self.assertEqual(len(tools.tools), 9)
                    config = await session.call_tool("get_configuration", {})
                    self.assertFalse(config.isError)
                    self.assertEqual(config.structuredContent["config_path"], str(self.config))
                    environment = await session.call_tool("get_environment", {})
                    self.assertFalse(environment.isError)
                    self.assertFalse(environment.structuredContent["hardware_accessed"])
                    # 缺少型号，必须在启动任何 IPECMD 前返回工具错误。
                    invalid = await session.call_tool("preview_operation", {"operation": "program"})
                    self.assertTrue(invalid.isError)
                    wrong_enum = await session.call_tool("preview_operation", {"operation": "raw_command"})
                    self.assertTrue(wrong_enum.isError)
        self.assertFalse((self.root / "logs").exists())


if __name__ == "__main__":
    unittest.main()
