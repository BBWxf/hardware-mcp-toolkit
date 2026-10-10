"""验证 stdio 入口可以被 MCP 客户端发现。"""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class McpProtocolTests(unittest.TestCase):
    def test_stdio_server_exposes_required_tools(self) -> None:
        async def list_tool_names() -> set[str]:
            server_file = Path(__file__).with_name("serial_mcp_server.py")
            parameters = StdioServerParameters(command=sys.executable, args=[str(server_file)])
            async with stdio_client(parameters) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    result = await session.list_tools()
                    return {tool.name for tool in result.tools}

        names = asyncio.run(list_tool_names())
        self.assertEqual(
            names,
            {"list_ports", "connect", "disconnect", "send", "read", "read_until", "set_control_lines"},
        )


if __name__ == "__main__":
    unittest.main()
