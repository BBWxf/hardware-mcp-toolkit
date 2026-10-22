"""真实 MCP stdio 握手和工具测试；仅连接回环地址的假 SCPI 服务。"""

import json
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from bode100 import Bode100
from fake_scpi import FakeServer
from server import create_server


EXPECTED_TOOLS = {
    "connect", "disconnect", "get_connection_status", "get_device_limits", "get_status",
    "get_configuration", "configure_measurement", "configure_sweep", "configure_source",
    "input_settings", "configure_result", "averaging_settings", "configure_trigger",
    "initiate_measurement", "send_trigger", "abort_measurement", "estimate_measurement_time",
    "wait_for_completion", "read_measurement", "read_errors", "get_calibration_status",
    "enable_calibration", "execute_calibration", "calibration_model", "save_calibration",
    "load_calibration", "reset_device", "scpi_write", "scpi_query", "get_reference",
}


class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_schema_annotations_and_startup_side_effects(self):
        with patch("socket.create_connection") as connect:
            service = create_server()
            tools = {tool.name: tool for tool in await service.list_tools()}
            connect.assert_not_called()
        self.assertEqual(set(tools), EXPECTED_TOOLS)
        self.assertTrue(tools["get_status"].annotations.readOnlyHint)
        self.assertFalse(tools["scpi_query"].annotations.readOnlyHint)
        self.assertFalse(tools["get_configuration"].annotations.readOnlyHint)
        self.assertFalse(tools["read_measurement"].annotations.readOnlyHint)
        self.assertFalse(tools["read_measurement"].annotations.destructiveHint)
        self.assertTrue(tools["reset_device"].annotations.destructiveHint)
        self.assertEqual(tools["configure_measurement"].inputSchema["required"], ["measurement"])
        schema = tools["read_measurement"].inputSchema["properties"]
        self.assertEqual(schema["limit"]["maximum"], 10000)

    async def test_disconnected_error_is_mcp_error_with_structured_content(self):
        service = create_server(Bode100())
        result = await service.call_tool("get_status", {})
        self.assertTrue(result.isError)
        self.assertEqual(result.structuredContent["error"]["code"], "not_connected")
        self.assertEqual(json.loads(result.content[0].text), result.structuredContent)

    async def test_argument_error_is_mcp_error(self):
        service = create_server()
        result = await service.call_tool("scpi_write", {"command": "*RST;*RST"})
        self.assertTrue(result.isError)
        self.assertEqual(result.structuredContent["error"]["code"], "invalid_argument")

    async def test_hardware_error_and_recovery(self):
        with FakeServer() as fake:
            backend = Bode100("127.0.0.1", fake.server_address[1])
            backend.connect()
            try:
                service = create_server(backend)
                fake.instrument.reject.add(":SOUR:POW 0")
                result = await service.call_tool("configure_source", {"power_dbm": 0})
                self.assertTrue(result.isError)
                self.assertEqual(result.structuredContent["error"]["code"], "scpi_error")
                result = await service.call_tool("get_status", {})
                self.assertFalse(result.isError)
            finally:
                backend.disconnect()

    async def test_stdio_full_single_measurement_and_errors(self):
        with FakeServer() as fake:
            script = Path(__file__).resolve().parents[1] / "server.py"
            parameters = StdioServerParameters(
                command=sys.executable,
                args=[str(script), "--host", "127.0.0.1", "--port", str(fake.server_address[1])],
                cwd=str(Path(__file__).resolve().parent),
                env={"PYTHONUTF8": "1"},
            )
            with anyio.fail_after(45):
                async with stdio_client(parameters) as (reader, writer):
                    async with ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=20)) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        self.assertEqual({tool.name for tool in tools.tools}, EXPECTED_TOOLS)
                        self.assertEqual(fake.instrument.commands, [])
                        result = await session.call_tool("get_connection_status", {})
                        self.assertFalse(result.structuredContent["connected"])
                        for name, arguments in (
                            ("connect", {}),
                            ("configure_measurement", {"measurement": "GAIN"}),
                            ("configure_trigger", {"source": "BUS", "continuous": False}),
                            ("initiate_measurement", {}),
                            ("send_trigger", {}),
                            ("wait_for_completion", {"timeout_seconds": 2}),
                        ):
                            result = await session.call_tool(name, arguments)
                            self.assertFalse(result.isError, f"{name}: {result}")
                        result = await session.call_tool("read_measurement", {"limit": 2})
                        self.assertFalse(result.isError)
                        self.assertEqual(result.structuredContent["primary"], [1, 2])
                        self.assertEqual(result.structuredContent["secondary"], [-10, -20])
                        self.assertTrue(result.structuredContent["has_more"])
                        invalid = await session.call_tool("configure_measurement", {"measurement": "UNSUPPORTED"})
                        self.assertTrue(invalid.isError)
                        invalid = await session.call_tool("read_measurement", {"limit": 10001})
                        self.assertTrue(invalid.isError)
                        invalid = await session.call_tool("configure_sweep", {"points": 1.5})
                        self.assertTrue(invalid.isError)
                        invalid = await session.call_tool("scpi_query", {"command": "*IDN?\n*RST"})
                        self.assertTrue(invalid.isError)
                        result = await session.call_tool("get_status", {})
                        self.assertFalse(result.isError)
            # lifespan 必须在进程退出时释放锁；未隐式复位/中止或更改输出。
            self.assertEqual(fake.instrument.commands[-1], ":SYST:LOCK:REL?")
            self.assertNotIn("*RST", fake.instrument.commands)
            self.assertNotIn(":ABOR", fake.instrument.commands)


if __name__ == "__main__":
    unittest.main()
