"""启动真实 stdio 子进程进行 MCP 握手；硬件选项不访问 I2C 总线。"""

import argparse
import asyncio
import json
import sys
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def check(hardware: bool, connect: bool):
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).with_name("server.py"))],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=20)) as session:
            initialized = await session.initialize()
            print(f"MCP: {initialized.serverInfo.name}")
            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            expected = {"list_devices", "connect", "disconnect", "status", "i2c_scan", "i2c_write", "i2c_read", "i2c_write_read"}
            if names != expected:
                raise RuntimeError(f"工具列表不符：{names}")
            print(f"Tools ({len(names)}): {', '.join(sorted(names))}")
            for name in (["status", "list_devices"] if hardware else ["status"]):
                result = await session.call_tool(name, {})
                if result.isError:
                    raise RuntimeError(f"{name}: {result.content}")
                print(name + ": " + json.dumps(result.model_dump(mode="json"), ensure_ascii=False))
            if connect:
                result = await session.call_tool("connect", {})
                if result.isError:
                    raise RuntimeError(f"connect: {result.content}")
                print("connect: " + json.dumps(result.model_dump(mode="json"), ensure_ascii=False))
                result = await session.call_tool("disconnect", {})
                if result.isError:
                    raise RuntimeError(f"disconnect: {result.content}")
                print("disconnect: " + json.dumps(result.model_dump(mode="json"), ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hardware", action="store_true", help="枚举 USB 卡，不访问 I2C 总线")
    parser.add_argument("--connect", action="store_true", help="打开并关闭 USB 卡，不访问 I2C 总线")
    args = parser.parse_args()
    asyncio.run(check(args.hardware or args.connect, args.connect))
