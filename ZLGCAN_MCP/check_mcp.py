"""验证真实 stdio 握手。默认不加载驱动；--connect 只打开/关闭卡，不启动 CAN 通道。"""

import argparse
import asyncio
import json
import os
import sys
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

EXPECTED_TOOLS = {
    "driver_info", "status", "connect", "disconnect", "init_channel", "reset_channel",
    "send_frame", "receive_frames", "get_receive_count", "read_error",
}


async def check(connect: bool, device_index: int) -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).with_name("server.py"))],
        env={"ZLGCAN_DLL_PATH": os.environ["ZLGCAN_DLL_PATH"]} if "ZLGCAN_DLL_PATH" in os.environ else None,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as session:
            initialized = await session.initialize()
            print(f"MCP: {initialized.serverInfo.name}")
            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            if names != EXPECTED_TOOLS:
                raise RuntimeError(f"工具列表不符：{names}")
            print(f"Tools ({len(names)}): {', '.join(sorted(names))}")

            async def call(name: str, arguments: dict) -> None:
                result = await session.call_tool(name, arguments)
                if result.isError:
                    raise RuntimeError(f"{name}: {result.content}")
                print(name + ": " + json.dumps(result.model_dump(mode="json"), ensure_ascii=False))

            await call("status", {})
            await call("driver_info", {})
            if connect:
                try:
                    await call("connect", {"device_index": device_index})
                    await call("status", {})
                finally:
                    await call("disconnect", {})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connect", action="store_true", help="打开并关闭卡；不启动 CAN 通道、不发帧")
    parser.add_argument("--device-index", type=int, default=0, help="USB-CAN2 设备索引（默认 0）")
    args = parser.parse_args()
    asyncio.run(check(args.connect, args.device_index))
