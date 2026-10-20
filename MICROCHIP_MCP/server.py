"""MPLAB IPE / PICkit 4 stdio MCP 服务。启动时不运行 IPECMD。"""

from __future__ import annotations

import argparse
import json
from functools import partial
from pathlib import Path

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from ipecmd import DEFAULT_CONFIG, Ipecmd, MemoryRegion, Operation


def create_server(backend: Ipecmd | None = None) -> FastMCP:
    ipe = backend if backend is not None else Ipecmd()
    mcp = FastMCP("Microchip IPE / PICkit 4")
    readonly = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
    hardware = ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True,
    )

    def tool_result(result: dict) -> CallToolResult:
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(result, ensure_ascii=False))],
            structuredContent=result,
            isError=result.get("status") in ("failed", "timeout", "unconfirmed") or "error" in result,
        )

    async def execute(function, *args, **kwargs) -> CallToolResult:
        # 等待线程结束再响应取消，避免客户端取消请求后悄悄遗留硬件操作。
        result = await anyio.to_thread.run_sync(partial(function, *args, **kwargs))
        return tool_result(result)

    @mcp.tool(annotations=readonly)
    def get_environment() -> CallToolResult:
        """静态检查 IPECMD 路径、JAR 版本及服务状态；不枚举 USB、不访问目标硬件。"""
        return tool_result(ipe.environment())

    @mcp.tool(annotations=readonly)
    def get_configuration() -> CallToolResult:
        """读取当前 config.json 及其路径；每次调用重新加载，不修改配置、不访问硬件。"""
        return tool_result(ipe.configuration())

    @mcp.tool(annotations=hardware)
    async def list_programmers() -> CallToolResult:
        """执行 IPECMD -T 列出已连接编程器及序列号；不指定目标、不烧录。

        访问 USB 工具；首次访问可能涉及厂商工具初始化。应确认没有其他 IPE 会话占用工具。
        目标操作必须显式指定 PK4 序列号；列表不会自动选第一台。
        """
        return await execute(ipe.list_programmers)

    @mcp.tool(annotations=readonly)
    def preview_operation(
        operation: Operation,
        device: str | None = None,
        hex_file: str | None = None,
        serial_number: str | None = None,
        voltage_v: float | None = None,
        power_from_programmer: bool | None = None,
        release_from_reset: bool | None = None,
        memory_region: MemoryRegion | None = None,
        verify_after_program: bool | None = None,
        output_file: str | None = None,
        timeout_seconds: int | None = None,
    ) -> CallToolResult:
        """验证必需参数并预览命令，不启动 IPECMD、不操作硬件、不写日志。

        空参数使用配置值；电压只允许 3.3/5V。预览不证明具体器件、电压或接口受支持。
        interface、speed_mhz、DFP 在配置文件中设置。read 的实际执行会先输出到临时 HEX。
        """
        return tool_result(ipe.preview(
            operation, device=device, hex_file=hex_file, serial_number=serial_number,
            voltage_v=voltage_v, power_from_programmer=power_from_programmer,
            release_from_reset=release_from_reset, memory_region=memory_region,
            verify_after_program=verify_after_program, output_file=output_file,
            timeout_seconds=timeout_seconds,
        ))

    @mcp.tool(annotations=hardware)
    async def program_device(
        device: str | None = None,
        hex_file: str | None = None,
        serial_number: str | None = None,
        voltage_v: float | None = None,
        power_from_programmer: bool | None = None,
        release_from_reset: bool | None = None,
        memory_region: MemoryRegion | None = None,
        verify_after_program: bool | None = None,
        timeout_seconds: int | None = None,
    ) -> CallToolResult:
        """通过 PK4 烧录 HEX，会擦写芯片并按配置供电/复位；操作前确认目标、固件和电压。

        未传参数沿用配置。-M 使用 IPECMD 自身擦除/校验流程，不额外追加 -E。
        verify_after_program=true 才追加独立 -Y；false/null 不会关闭内部校验。
        仅支持已安装 IPE/DFP 与 PK4 支持的器件。可能自动更新编程器固件；失败不自动重试。
        """
        return await execute(
            ipe.execute, "program", device=device, hex_file=hex_file, serial_number=serial_number,
            voltage_v=voltage_v, power_from_programmer=power_from_programmer,
            release_from_reset=release_from_reset, memory_region=memory_region,
            verify_after_program=verify_after_program, timeout_seconds=timeout_seconds,
        )

    @mcp.tool(annotations=hardware)
    async def verify_device(
        device: str | None = None,
        hex_file: str | None = None,
        serial_number: str | None = None,
        voltage_v: float | None = None,
        power_from_programmer: bool | None = None,
        release_from_reset: bool | None = None,
        memory_region: MemoryRegion | None = None,
        timeout_seconds: int | None = None,
    ) -> CallToolResult:
        """用指定 HEX 独立校验目标；不追加烧录/擦除，但会连接、供电并影响复位状态。

        参数为空使用配置。可能更新编程器固件，不能视作完全无硬件副作用的查询。
        """
        return await execute(
            ipe.execute, "verify", device=device, hex_file=hex_file, serial_number=serial_number,
            voltage_v=voltage_v, power_from_programmer=power_from_programmer,
            release_from_reset=release_from_reset, memory_region=memory_region,
            timeout_seconds=timeout_seconds,
        )

    @mcp.tool(annotations=hardware)
    async def erase_device(
        device: str | None = None,
        serial_number: str | None = None,
        voltage_v: float | None = None,
        power_from_programmer: bool | None = None,
        release_from_reset: bool | None = None,
        timeout_seconds: int | None = None,
    ) -> CallToolResult:
        """执行 IPECMD -E 擦除目标，不可撤销；先获得用户对该目标的擦除授权。

        不需要 HEX 文件；擦除范围及保护位影响由具体器件和 IPECMD 定义，不提供区域擦除参数。
        """
        return await execute(
            ipe.execute, "erase", device=device, serial_number=serial_number,
            voltage_v=voltage_v, power_from_programmer=power_from_programmer,
            release_from_reset=release_from_reset, timeout_seconds=timeout_seconds,
        )

    @mcp.tool(annotations=hardware)
    async def blank_check(
        device: str | None = None,
        serial_number: str | None = None,
        voltage_v: float | None = None,
        power_from_programmer: bool | None = None,
        release_from_reset: bool | None = None,
        timeout_seconds: int | None = None,
    ) -> CallToolResult:
        """执行 IPECMD -C 空白检查；不需要 HEX、不擦除，但会连接目标并按配置供电/复位。"""
        return await execute(
            ipe.execute, "blank_check", device=device, serial_number=serial_number,
            voltage_v=voltage_v, power_from_programmer=power_from_programmer,
            release_from_reset=release_from_reset, timeout_seconds=timeout_seconds,
        )

    @mcp.tool(annotations=hardware)
    async def read_device(
        output_file: str | None = None,
        overwrite: bool = False,
        device: str | None = None,
        serial_number: str | None = None,
        voltage_v: float | None = None,
        power_from_programmer: bool | None = None,
        release_from_reset: bool | None = None,
        timeout_seconds: int | None = None,
    ) -> CallToolResult:
        """用 -GF 将目标读回为 HEX，会连接目标并按配置供电/复位；不绕过代码保护。

        output_file 为空使用 readback.hex_file。默认拒绝覆盖；只有明确授权后才设置 overwrite=true。
        先写临时文件，确认成功后再发布到输出路径；失败不覆盖旧文件。
        """
        return await execute(
            ipe.execute, "read", overwrite=overwrite, output_file=output_file,
            device=device, serial_number=serial_number, voltage_v=voltage_v,
            power_from_programmer=power_from_programmer, release_from_reset=release_from_reset,
            timeout_seconds=timeout_seconds,
        )

    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(description="Microchip IPE / PK4 stdio MCP Server")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="配置路径，默认使用脚本同目录 config.json")
    args = parser.parse_args()
    create_server(Ipecmd(args.config)).run(transport="stdio")


if __name__ == "__main__":
    main()
