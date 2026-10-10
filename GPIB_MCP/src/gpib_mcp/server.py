from __future__ import annotations

import base64
import logging
import os
import sys
from typing import Any, Callable, TypeVar

from mcp.server import MCPServer

from .chroma import ChromaError, ChromaService
from .gpib import GpibError, GpibService, IOSettings

T = TypeVar("T")
LOGGER = logging.getLogger("gpib_mcp")


def configure_logging() -> None:
    level_name = os.getenv("GPIB_MCP_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    LOGGER.handlers.clear()
    LOGGER.addHandler(handler)
    LOGGER.setLevel(level)
    LOGGER.propagate = False


def _settings(
    timeout_ms: int,
    read_termination: str | None,
    write_termination: str | None,
) -> IOSettings:
    try:
        return IOSettings(
            timeout_ms=timeout_ms,
            read_termination=read_termination,
            write_termination=write_termination,
        )
    except ValueError as exc:
        raise RuntimeError(f"[invalid_argument] {exc}") from None


def _call(operation: str, resource: str | None, function: Callable[[], T]) -> T:
    try:
        result = function()
    except (GpibError, ChromaError) as exc:
        LOGGER.warning("%s failed resource=%s code=%s", operation, resource, exc.code)
        raise RuntimeError(str(exc)) from None
    LOGGER.info("%s succeeded resource=%s", operation, resource)
    return result


def _raw_result(resource: str, data: bytes) -> dict[str, Any]:
    return {
        "resource": resource,
        "encoding": "base64",
        "byte_count": len(data),
        "data_base64": base64.b64encode(data).decode("ascii"),
    }


def create_server(service: GpibService | None = None) -> MCPServer:
    gpib = service or GpibService()
    chroma = ChromaService(gpib)
    server = MCPServer("GPIB Controller")

    @server.tool(
        name="gpib_scan",
        title="扫描 GPIB 仪器",
        description="列出当前 VISA 后端发现的全部 GPIB INSTR 资源，不会向仪器发送命令。",
    )
    def gpib_scan() -> dict[str, Any]:
        resources = _call("scan", None, gpib.scan)
        return {"count": len(resources), "resources": resources}

    @server.tool(
        name="gpib_identify",
        title="识别 GPIB 仪器",
        description="向指定 GPIB 仪器发送 *IDN? 并返回文本响应。并非所有旧式 GPIB 仪器都支持该命令。",
    )
    def gpib_identify(
        resource: str,
        timeout_ms: int = 5_000,
        read_termination: str | None = "\n",
        write_termination: str | None = "\n",
    ) -> dict[str, Any]:
        io = _settings(timeout_ms, read_termination, write_termination)
        response = _call(
            "identify", resource, lambda: gpib.identify(resource, io)
        )
        return {"resource": resource, "response": response}

    @server.tool(
        name="gpib_query",
        title="执行 GPIB 文本查询",
        description=(
            "向指定 GPIB 仪器发送任意文本命令并读取文本响应。服务不会检查命令是否以 ? 结尾，"
            "也不会过滤可能改变仪器状态的命令。"
        ),
    )
    def gpib_query(
        resource: str,
        command: str,
        timeout_ms: int = 5_000,
        read_termination: str | None = "\n",
        write_termination: str | None = "\n",
    ) -> dict[str, Any]:
        io = _settings(timeout_ms, read_termination, write_termination)
        LOGGER.debug("query resource=%s command=%r", resource, command)
        response = _call(
            "query", resource, lambda: gpib.query(resource, command, io)
        )
        return {"resource": resource, "response": response}

    @server.tool(
        name="gpib_write",
        title="执行 GPIB 写命令",
        description=(
            "向指定 GPIB 仪器发送任意 SCPI/设备命令。该工具没有命令白名单，可能改变输出、"
            "电压、量程、触发、校准或存储状态；调用方必须确认命令及物理设备安全。"
        ),
    )
    def gpib_write(
        resource: str,
        command: str,
        timeout_ms: int = 5_000,
        read_termination: str | None = "\n",
        write_termination: str | None = "\n",
    ) -> dict[str, Any]:
        io = _settings(timeout_ms, read_termination, write_termination)
        LOGGER.debug("write resource=%s command=%r", resource, command)
        bytes_written = _call(
            "write", resource, lambda: gpib.write(resource, command, io)
        )
        return {"resource": resource, "bytes_written": bytes_written}

    @server.tool(
        name="gpib_query_raw",
        title="执行 GPIB 原始数据查询",
        description=(
            "向指定仪器写入任意命令并原子地读取原始字节。结果使用 Base64 返回，适合波形等二进制响应。"
        ),
    )
    def gpib_query_raw(
        resource: str,
        command: str,
        timeout_ms: int = 10_000,
        read_termination: str | None = None,
        write_termination: str | None = "\n",
    ) -> dict[str, Any]:
        io = _settings(timeout_ms, read_termination, write_termination)
        LOGGER.debug("query_raw resource=%s command=%r", resource, command)
        data = _call(
            "query_raw", resource, lambda: gpib.query_raw(resource, command, io)
        )
        return _raw_result(resource, data)

    @server.tool(
        name="gpib_read_raw",
        title="读取 GPIB 原始数据",
        description=(
            "从指定仪器读取已经等待返回的原始字节，并使用 Base64 返回。通常优先使用 gpib_query_raw。"
        ),
    )
    def gpib_read_raw(
        resource: str,
        timeout_ms: int = 10_000,
        read_termination: str | None = None,
        write_termination: str | None = "\n",
    ) -> dict[str, Any]:
        io = _settings(timeout_ms, read_termination, write_termination)
        data = _call("read_raw", resource, lambda: gpib.read_raw(resource, io))
        return _raw_result(resource, data)

    @server.tool(
        name="chroma_discover",
        title="扫描并识别 Chroma 仪器",
        description=(
            "扫描全部 GPIB INSTR 资源，逐一执行 *IDN?，识别受支持的 Chroma 6530、"
            "66205 和 63200A 系列模块。识别失败的资源会列在 errors 中。"
        ),
    )
    def chroma_discover(
        timeout_ms: int = 5_000,
        read_termination: str | None = "\n",
        write_termination: str | None = "\n",
    ) -> dict[str, Any]:
        io = _settings(timeout_ms, read_termination, write_termination)
        return _call("chroma_discover", None, lambda: chroma.discover(io))

    @server.tool(
        name="chroma_6530_configure_output",
        title="配置 Chroma 6530 交流输出",
        description=(
            "动态识别 Chroma 6530 后设置电压、频率和/或输出开关。未提供 resource 时仅在"
            "恰好发现一台 6530 时执行；发现多台时必须先用 chroma_discover 确认并提供 resource。"
            "不提供的设定项保持不变。设置电压时会在同一程序讯息中执行 RANG AUTO。"
        ),
    )
    def chroma_6530_configure_output(
        voltage_v: float | None = None,
        frequency_hz: float | None = None,
        output_enabled: bool | None = None,
        resource: str | None = None,
        timeout_ms: int = 5_000,
        read_termination: str | None = "\n",
        write_termination: str | None = "\n",
    ) -> dict[str, Any]:
        io = _settings(timeout_ms, read_termination, write_termination)
        return _call(
            "chroma_6530_configure_output",
            resource,
            lambda: chroma.configure_6530_output(
                resource=resource,
                voltage_v=voltage_v,
                frequency_hz=frequency_hz,
                output_enabled=output_enabled,
                settings=io,
            ),
        )

    @server.tool(
        name="chroma_66205_read_input",
        title="读取 Chroma 66205 输入测量",
        description=(
            "动态识别 Chroma 66205 后读取 RMS 电压、电流、有功功率、功率因数和输入电流 THD。"
            "fresh=true 使用 MEAS? 触发新测量；false 使用 FETC? 读取既有缓冲值。"
            "THD 保持仪器原始数值，不擅自换算百分比。"
        ),
    )
    def chroma_66205_read_input(
        fresh: bool = True,
        resource: str | None = None,
        timeout_ms: int = 5_000,
        read_termination: str | None = "\n",
        write_termination: str | None = "\n",
    ) -> dict[str, Any]:
        io = _settings(timeout_ms, read_termination, write_termination)
        return _call(
            "chroma_66205_read_input",
            resource,
            lambda: chroma.read_66205_input(
                resource=resource,
                fresh=fresh,
                settings=io,
            ),
        )

    @server.tool(
        name="chroma_63200a_set_cc_current",
        title="设置 Chroma 63200A 恒流高档位",
        description=(
            "动态识别 Chroma 63200A 模块后执行 MODE CCH 和 CURR:STAT:L1，设置恒流高档位"
            "电流设定值。此工具不会自动开启或关闭电子负载输入。模块最大设定范围由实际识别到的"
            "模块规格决定，超范围时仪器错误会被报告且不会自动关闭负载。"
        ),
    )
    def chroma_63200a_set_cc_current(
        current_a: float,
        resource: str | None = None,
        timeout_ms: int = 5_000,
        read_termination: str | None = "\n",
        write_termination: str | None = "\n",
    ) -> dict[str, Any]:
        io = _settings(timeout_ms, read_termination, write_termination)
        return _call(
            "chroma_63200a_set_cc_current",
            resource,
            lambda: chroma.set_63200a_cc_current(
                resource=resource,
                current_a=current_a,
                settings=io,
            ),
        )

    @server.tool(
        name="chroma_63200a_set_cv_voltage",
        title="设置 Chroma 63200A 恒压高档位",
        description=(
            "动态识别 Chroma 63200A 模块后执行 MODE CVH 和 VOLT:STAT:L1，设置恒压高档位"
            "电压设定值。此工具不会自动开启或关闭电子负载输入。模块最大设定范围由实际识别到的"
            "模块规格决定，超范围时仪器错误会被报告且不会自动关闭负载。"
        ),
    )
    def chroma_63200a_set_cv_voltage(
        voltage_v: float,
        resource: str | None = None,
        timeout_ms: int = 5_000,
        read_termination: str | None = "\n",
        write_termination: str | None = "\n",
    ) -> dict[str, Any]:
        io = _settings(timeout_ms, read_termination, write_termination)
        return _call(
            "chroma_63200a_set_cv_voltage",
            resource,
            lambda: chroma.set_63200a_cv_voltage(
                resource=resource,
                voltage_v=voltage_v,
                settings=io,
            ),
        )

    @server.tool(
        name="chroma_63200a_set_load_enabled",
        title="开关 Chroma 63200A 电子负载",
        description=(
            "动态识别 Chroma 63200A 模块后执行 LOAD ON 或 LOAD OFF。此工具会改变电子负载"
            "输入状态；发生通讯或仪器错误时只报告错误，不会自动发送 LOAD OFF。"
        ),
    )
    def chroma_63200a_set_load_enabled(
        enabled: bool,
        resource: str | None = None,
        timeout_ms: int = 5_000,
        read_termination: str | None = "\n",
        write_termination: str | None = "\n",
    ) -> dict[str, Any]:
        io = _settings(timeout_ms, read_termination, write_termination)
        return _call(
            "chroma_63200a_set_load_enabled",
            resource,
            lambda: chroma.set_63200a_load_enabled(
                resource=resource,
                enabled=enabled,
                settings=io,
            ),
        )

    @server.tool(
        name="chroma_63200a_read_measurements",
        title="读取 Chroma 63200A 电压电流功率",
        description=(
            "动态识别 Chroma 63200A 模块后读取实际输入电压、电流与功率。averaged=false 使用"
            "FETC 查询即时值；true 使用 MEAS 查询 Window time 内平均值。"
        ),
    )
    def chroma_63200a_read_measurements(
        averaged: bool = False,
        resource: str | None = None,
        timeout_ms: int = 5_000,
        read_termination: str | None = "\n",
        write_termination: str | None = "\n",
    ) -> dict[str, Any]:
        io = _settings(timeout_ms, read_termination, write_termination)
        return _call(
            "chroma_63200a_read_measurements",
            resource,
            lambda: chroma.read_63200a_measurements(
                resource=resource,
                averaged=averaged,
                settings=io,
            ),
        )

    return server


configure_logging()
mcp = create_server()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
