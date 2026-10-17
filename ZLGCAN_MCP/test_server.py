"""MCP 工具注册和分发测试，不加载厂商驱动。"""

import unittest
from unittest.mock import MagicMock

from check_mcp import EXPECTED_TOOLS
from server import create_server


class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.device = MagicMock()
        self.server = create_server(self.device)

    async def test_tools_and_schema(self):
        tools = {tool.name: tool for tool in await self.server.list_tools()}
        self.assertEqual(set(tools), EXPECTED_TOOLS)
        init = tools["init_channel"].inputSchema["properties"]
        self.assertEqual(init["mode"]["default"], "listen_only")
        self.assertEqual(init["bitrate"]["default"], 500000)
        self.assertEqual(tools["send_frame"].inputSchema["required"], ["channel", "can_id"])
        self.device.assert_not_called()
        self.assertEqual(self.device.mock_calls, [])

    async def test_status_does_not_connect(self):
        self.device.status.return_value = {"connected": False}
        await self.server.call_tool("status", {})
        self.device.status.assert_called_once_with()
        self.device.connect.assert_not_called()

    async def test_send_dispatch(self):
        self.device.send_frame.return_value = {"sent": 1}
        await self.server.call_tool("send_frame", {"channel": 1, "can_id": "0x123", "data_hex": "AA"})
        self.device.send_frame.assert_called_once_with(1, "0x123", "AA", False, False, None)

    async def test_receive_defaults(self):
        self.device.receive_frames.return_value = {"frames": []}
        await self.server.call_tool("receive_frames", {"channel": 0})
        self.device.receive_frames.assert_called_once_with(0, 100, 0)

    async def test_lifespan_closes_device(self):
        async with self.server.settings.lifespan(self.server):
            self.device.disconnect.assert_not_called()
        self.device.disconnect.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
