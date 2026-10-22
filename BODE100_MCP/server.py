"""Bode 100 stdio MCP；启动时不连接设备、不启动厂商程序。"""

from __future__ import annotations

import argparse
import json
import logging
from contextlib import asynccontextmanager
from functools import partial
from typing import Annotated, Literal

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field

from bode100 import Bode100, BodeError, CalibrationRange, Measurement, ResultFormat, Standard, ZMethod, ZParameter

Finite = Annotated[float, Field(allow_inf_nan=False)]
Timeout = Annotated[float, Field(ge=0.1, le=3600, allow_inf_nan=False)]
Port = Annotated[int, Field(ge=1, le=65535, strict=True)]
Points = Annotated[int, Field(ge=1, strict=True)]
NonnegativeInt = Annotated[int, Field(ge=0, strict=True)]
PageSize = Annotated[int, Field(ge=1, le=10000, strict=True)]

REFERENCE = "https://documentation.omicron-lab.com/BodeAutomationInterface/3.50/"


def create_server(backend: Bode100 | None = None) -> FastMCP:
    bode = backend if backend is not None else Bode100()

    @asynccontextmanager
    async def lifespan(_server):
        try:
            yield
        finally:
            try:
                await anyio.to_thread.run_sync(bode.disconnect)
            except BodeError:
                logging.getLogger(__name__).warning("退出时未能确认释放设备锁，TCP 连接已关闭")

    mcp = FastMCP(
        "Bode 100",
        instructions=(
            "本服务仅封装 Bode 100 官方 SCPI 接口，不分析环路或决定实验流程。"
            "先 connect 获取设备锁；不要与 BAS 或其他控制客户端同时操作。"
            "配置不隐含启动测量。模型负责选择测量模式、配置、触发和校准顺序。"
            "BUS 单次测量需配置 source=BUS/continuous=false，再 initiate_measurement、send_trigger、"
            "wait_for_completion，最后 read_measurement。外部接线与校准标准必须由用户实际完成。"
            "read_measurement 返回的是两个连续数组，不是交错复数；含义取决于格式。"
            "原始查询也可能有副作用，不能一律视为只读。"
            "超时会关闭连接但不保证仪器已停止；不得盲目重试测量或校准，先重新连接并查询状态。"
        ),
        lifespan=lifespan,
    )
    readonly = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
    local_readonly = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
    diagnostic = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)
    hardware = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True)

    async def execute(function, *args, **kwargs) -> CallToolResult:
        try:
            # 取消 MCP 请求时等待该次 I/O 结束，避免留下仍然运行的隐藏线程。
            result = await anyio.to_thread.run_sync(partial(function, *args, **kwargs))
            encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
        except BodeError as exc:
            result = exc.as_dict()
            encoded = json.dumps(result, ensure_ascii=False)
        except ValueError as exc:
            result = {"error": {"code": "invalid_argument", "message": str(exc)}}
            encoded = json.dumps(result, ensure_ascii=False)
        return CallToolResult(
            content=[TextContent(type="text", text=encoded)], structuredContent=result,
            isError="error" in result,
        )

    @mcp.tool(annotations=hardware)
    async def connect(host: str | None = None, port: Port | None = None) -> CallToolResult:
        """连接已运行的官方 SCPI Server，核对 Bode 100 身份并请求设备锁。

        空参数使用服务启动配置（默认 127.0.0.1:5025）。只设 ASCII 传输，不复位、
        不改测量设置、不启动测量。请求锁可能使厂商软件执行设备初始化/内部校准。
        设备按官方 SCPI Server 启动参数 -s 序列号选择，不在本工具中扫描 USB。
        """
        return await execute(bode.connect, host, port)

    @mcp.tool(annotations=hardware)
    async def disconnect() -> CallToolResult:
        """释放设备锁并关闭 TCP 会话。不自动中止测量，也不修改输出状态。"""
        return await execute(bode.disconnect)

    @mcp.tool(annotations=local_readonly)
    async def get_connection_status() -> CallToolResult:
        """返回本地连接信息，不访问硬件。connected 不代表设备刚通过了连通性检查。"""
        return await execute(bode.connection_status)

    @mcp.tool(annotations=diagnostic)
    async def get_device_limits() -> CallToolResult:
        """读取仪器提供的频率 Hz、功率 dBm 和最大点数限制，不硬编码 40/50 MHz。"""
        return await execute(bode.get_limits)

    @mcp.tool(annotations=readonly)
    async def get_status() -> CallToolResult:
        """读取操作条件寄存器及锁所有者，不清状态位或错误队列。

        返回校准中、扫频中、测量中、等待触发和设备锁状态。完成标记只跟踪本 MCP 会话，
        无法保证其他客户端没有改变仪器；外部触发测量需自行判断并显式读取未验证缓冲区。
        """
        return await execute(bode.get_status)

    @mcp.tool(annotations=diagnostic)
    async def get_configuration() -> CallToolResult:
        """读取模式、扫频、源和触发设置。读取并检查错误队列，可能消费此前遗留错误。"""
        return await execute(bode.get_configuration)

    @mcp.tool(annotations=hardware)
    async def configure_measurement(
        measurement: Measurement, z_method: ZMethod | None = None, reset_defaults: bool = False,
    ) -> CallToolResult:
        """设置官方测量模式；不启动测量。

        GAIN=增益相位；S11/S21=S 参数；CH1/CH2（别名 R/T）=绝对通道 VRMS；Z=阻抗。
        Z 方法：P1R=单端反射、TSH=并联穿通、TSER=串联穿通、ECO=外部耦合器、
        EBR=外部桥、IAD=阻抗适配器、VCG=电压电流增益。z_method 仅适用于 Z。
        reset_defaults=true 显式传 DEF，可能改变源功率、终端和衰减等设置；false 保留原设置。
        """
        return await execute(bode.configure_measurement, measurement, z_method, reset_defaults)

    @mcp.tool(annotations=hardware)
    async def configure_sweep(
        start_hz: Finite | None = None, stop_hz: Finite | None = None,
        points: Points | None = None, sweep_type: Literal["LIN", "LOG"] | None = None,
        bandwidth_hz: Finite | None = None, dwell_seconds: Finite | None = None,
    ) -> CallToolResult:
        """修改指定扫频参数，未传项保持不变；至少一项。不启动测量。

        频率/接收带宽单位 Hz；点数可为 1；dwell_seconds 为每频点 DUT 稳定等待，0–60 秒。
        仪器可能将带宽舍入到支持值，返回实读设置。设备拒绝时不会回滚已接受的参数。
        """
        return await execute(bode.configure_sweep, start_hz, stop_hz, points, sweep_type, bandwidth_hz, dwell_seconds)

    @mcp.tool(annotations=hardware)
    async def configure_source(power_dbm: Finite | None = None, auto_off: bool | None = None) -> CallToolResult:
        """设置源电平 dBm 和/或 sweep 结束后自动关源；至少一项。

        auto_off 对应 :OUTP:AOFF，不是立即打开/关闭输出；设置它不会立即改变源的输出状态。
        不提供官方未定义的通用 :OUTP ON/OFF。配置前确认待测电路允许该激励电平。
        """
        return await execute(bode.configure_source, power_dbm, auto_off)

    @mcp.tool(annotations=hardware)
    async def input_settings(
        channel: Literal["CH1", "CH2"], attenuation_db: Finite | None = None,
        impedance_ohm: Literal[50, 1000000] | None = None, gain_factor: Finite | None = None,
    ) -> CallToolResult:
        """读取或修改指定物理输入；省略所有设置值时只读。

        衰减单位 dB；终端只支持 50 Ω/1 MΩ；gain_factor 是线性外部增益因子，不是 dB。
        合法衰减档位及增益/阻抗可改性受测量模式限制，以设备错误和实读值为准。
        改变终端阻抗会影响被测电路负载，不能把 50 Ω 用作通用默认。
        """
        return await execute(bode.input_settings, channel, attenuation_db, impedance_ohm, gain_factor)

    @mcp.tool(annotations=hardware)
    async def configure_result(
        result_format: ResultFormat | None = None, z_parameter: ZParameter | None = None,
    ) -> CallToolResult:
        """配置结果表示，不重新测量；至少指定一项。

        SLIN=线性幅值+角度，SLOG=对数幅值+角度，SCOM=实部+虚部；相位单位度。
        MLIN/MLOG/PHAS/UPH/REAL/IMAG/GDEL/SWR 为单值表示；SMIT=阻抗，SADM=导纳。
        Z 派生参数 CP/CS/LP/LS/RP/RS/Q/QTG/D 需要 REAL 格式；不兼容组合由设备报错。
        CH1/CH2/R/T 总是 VRMS。不能把所有 primary 都解释为 dB。
        """
        return await execute(bode.configure_result, result_format, z_parameter)

    @mcp.tool(annotations=hardware)
    async def averaging_settings(
        enabled: bool | None = None, count: Annotated[int, Field(ge=2, le=1000000000, strict=True)] | None = None,
        clear: bool = False,
    ) -> CallToolResult:
        """读写官方平均设置；count=2–1e9，clear=true 清平均缓存。不自动执行多次扫频。"""
        return await execute(bode.configure_averaging, enabled, count, clear)

    @mcp.tool(annotations=hardware)
    async def configure_trigger(
        source: Literal["INT", "BUS", "EXT"] | None = None, continuous: bool | None = None,
    ) -> CallToolResult:
        """设置触发源和/或连续启动；至少一项。INT=内部、BUS=软件、EXT=外部（需硬件支持）。

        continuous=true 会使系统进入等待触发状态；配合 INT 可能立即反复测量。
        此工具不会擅自改成 BUS，也不会附加 :INIT 或 :TRIG:SING。
        """
        return await execute(bode.configure_trigger, source, continuous)

    @mcp.tool(annotations=hardware)
    async def initiate_measurement() -> CallToolResult:
        """发送 :INIT，按当前触发配置进入等待/测量状态，不改模式或触发源、不等待完成。

        INT 可能立即测量；BUS 还需 send_trigger。单次模式完成后再次测量需重新 INIT。
        """
        return await execute(bode.initiate)

    @mcp.tool(annotations=hardware)
    async def send_trigger() -> CallToolResult:
        """发送 :TRIG:SING（BUS 触发）；需要已启动且触发源匹配。不附加 INIT，不等待完成。"""
        return await execute(bode.trigger)

    @mcp.tool(annotations=hardware)
    async def abort_measurement() -> CallToolResult:
        """发送 :ABOR 中止测量并回到 IDLE；不修改 continuous。可用 get_status 查询后续状态。"""
        return await execute(bode.abort)

    @mcp.tool(annotations=diagnostic)
    async def estimate_measurement_time() -> CallToolResult:
        """读取官方 :SENS:SWE:TIME? 的预计测量秒数。应先配置参数，不代表实际完成时间保证。"""
        return await execute(bode.estimate_duration)

    @mcp.tool(annotations=hardware)
    async def wait_for_completion(timeout_seconds: Timeout = 30.0) -> CallToolResult:
        """通过 *OPC? 等待此前设备操作完成，返回 1 才确认；超时范围 0.1–3600 秒。

        同一连接的 I/O 串行，等待期间不能从另一工具调用抢占 abort。长测量宜调用 get_status
        检查进度再短等待；不要长时间占用 *OPC?。超时断开连接，不会自动停止仪器。
        完成校准/文件操作不等于完成了新测量；measurement_confirmed 会单独报告。
        """
        return await execute(bode.wait, timeout_seconds)

    @mcp.tool(annotations=diagnostic)
    async def read_measurement(
        offset: NonnegativeInt = 0, limit: PageSize = 1000, allow_unverified: bool = False,
    ) -> CallToolResult:
        """读取仪器现有结果，不启动测量；返回频率 Hz 和两个结果数组，支持分页。

        官方 SDAT 数据是 N 个 primary 后接 N 个 secondary，不是交错排列。返回格式和 Z 参数
        用于解释单位；NaN/Infinity 保留位置并转为 null。默认最多 1000 点，每页最多 10000。
        默认仅接受本会话触发并等待确认、且测量设置未改的结果；allow_unverified=true 可明确
        读取外部触发/已有缓存，但不保证与当前配置对应。设备无数据仍可能报错/超时。
        读数前设置 ASCII 传输并检查错误队列；不改变测量结果格式。连续 INT/EXT 模式拒绝读数。
        分页间不要重新测量或修改配置，否则各页可能来自不同缓冲区。
        """
        return await execute(bode.read_measurement, allow_unverified, offset, limit)

    @mcp.tool(annotations=hardware)
    async def read_errors() -> CallToolResult:
        """读取并清空官方 SCPI 错误队列，返回编号/文本；这是消费性读取，不是无副作用查询。"""
        return await execute(bode.read_errors)

    @mcp.tool(annotations=diagnostic)
    async def get_calibration_status(calibration_range: CalibrationRange) -> CallToolResult:
        """查询 FULL 全范围或 USER 用户范围校准：enabled=允许、active=实际使用、available=完整可用。"""
        return await execute(bode.calibration_status, calibration_range)

    @mcp.tool(annotations=hardware)
    async def enable_calibration(calibration_range: CalibrationRange, enabled: bool) -> CallToolResult:
        """启用/禁用指定范围的校准修正。启用不保证 active；不自动执行缺少的校准步骤。"""
        return await execute(bode.enable_calibration, calibration_range, enabled)

    @mcp.tool(annotations=hardware)
    async def execute_calibration(calibration_range: CalibrationRange, standard: Standard) -> CallToolResult:
        """执行 FULL/USER 范围的一项 OPEN/SHORT/LOAD/THRU 校准，不自动换接线、不等待完成。

        必须先由用户实际接好相应标准。模式必须兼容，例如 GAIN 用 THRU 而不是 OPEN/SHORT/LOAD。
        所需项齐全后仪器计算并自动启用校准。随后 wait_for_completion 和 get_calibration_status。
        """
        return await execute(bode.execute_calibration, calibration_range, standard)

    @mcp.tool(annotations=hardware)
    async def calibration_model(
        nominal_impedance_ohm: Finite | None = None, load_ohm: Finite | None = None,
        short_delay_seconds: Finite | None = None,
    ) -> CallToolResult:
        """读写官方校准标准参数；空参数只读，不执行校准。

        标称阻抗单位 Ω（上限由设备判断）；负载 1–1000 Ω；短路延迟 0–500e-6 秒。
        只在相应测量模式支持时可改；不是物理输入 50 Ω/1 MΩ 的设置。
        """
        return await execute(bode.calibration_model, nominal_impedance_ohm, load_ohm, short_delay_seconds)

    @mcp.tool(annotations=hardware)
    async def save_calibration(path: str) -> CallToolResult:
        """把校准保存为官方 .mcalx，必须指定 SCPI Server 电脑上的 ASCII 绝对路径。

        可能覆盖同名文件，先确认路径；路径不是 MCP 客户端/模型电脑上的路径。
        不创建目录，不读写本服务自己的文件，不等待完成；之后 wait_for_completion。
        """
        return await execute(bode.calibration_file, "save", path)

    @mcp.tool(annotations=hardware)
    async def load_calibration(path: str, calibration_range: CalibrationRange) -> CallToolResult:
        """从 SCPI Server 电脑的绝对 .mcalx 路径加载 FULL/USER 校准；不等待、不自动启用。

        加载后用 wait_for_completion、get_calibration_status 确认；需要时显式 enable_calibration。
        """
        return await execute(bode.calibration_file, "load", path, calibration_range)

    @mcp.tool(annotations=hardware)
    async def reset_device() -> CallToolResult:
        """显式执行 *RST，改变测量配置/触发状态；仅在用户需要复位时使用，不自动等待完成。"""
        return await execute(bode.reset)

    @mcp.tool(annotations=hardware)
    async def scpi_write(command: str) -> CallToolResult:
        """发送一条官方 ASCII SCPI 非查询命令，覆盖未提供专用工具的接口。

        只允许单条命令，无换行、分号拼接或 ?；保持 ASCII 传输。此接口不附加命令、
        不等待或检查错误队列，也不保证设备已接受；需要时调用 read_errors。
        不要用来发送会返回响应的无问号特殊命令，锁操作请用专用连接工具。
        不可假设写入成功等于操作完成。使用官方 3.50/Bode 100 命令，不要套用 Bode 500-only 接口。
        """
        return await execute(bode.raw_write, command)

    @mcp.tool(annotations=hardware)
    async def scpi_query(command: str, timeout_seconds: Timeout | None = None) -> CallToolResult:
        """发送单条官方 ASCII 查询并返回未经改写的文本；最多一个 ?，不能拼接命令。

        用于未封装的校准数组、状态寄存器、温度等接口。查询可能清状态、获取锁或启动自检，
        因此不标记只读。原始调用后本服务不再声称已有结果对应当前配置；必要时显式读取未验证缓存。
        不自动读取错误队列；不支持二进制传输。超时关闭连接，避免迟到响应串到下一次查询。
        """
        return await execute(bode.raw_query, command, timeout_seconds)

    @mcp.tool(annotations=local_readonly)
    async def get_reference() -> CallToolResult:
        """返回官方接口文档链接及本服务边界；不联网、不访问仪器。"""
        return await execute(lambda: {
            "documentation_version": "3.50", "device": "Bode 100", "transport": "SCPI ASCII TCP / MCP stdio",
            "links": {
                "overview": REFERENCE + "articlesScpi/general/SCPIOverview.html",
                "commands": REFERENCE + "articlesScpi/commandReference/general/abort.html",
                "measurement_modes": REFERENCE + "3_MeasurementModes.html",
                "python_examples": REFERENCE + "articlesScpi/general/PythonEx.html",
                "server_start": REFERENCE + "articlesScpi/serverStarting/consoleRunner.html",
            },
            "excluded_bode500_commands": [":SENS:SWE:PSST", ":MMEM:CAT:CORR?", ":MMEM:DEL:CORR"],
            "not_implemented": ["环路分析", "自动调参", "绘图", "电源/负载联动", "自动安装或启动官方软件"],
        })

    return mcp


def main():
    parser = argparse.ArgumentParser(description="Bode 100 stdio MCP（官方 SCPI Server 必须单独启动）")
    parser.add_argument("--host", default="127.0.0.1", help="SCPI Server 主机，默认本机")
    parser.add_argument("--port", type=int, default=5025)
    parser.add_argument("--timeout", type=float, default=10.0, help="默认单条 I/O 超时秒数")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port 必须在 1–65535 之间")
    try:
        backend = Bode100(args.host, args.port, args.timeout)
    except ValueError as exc:
        parser.error(str(exc))
    logging.basicConfig(level=logging.INFO)
    create_server(backend).run(transport="stdio")


if __name__ == "__main__":
    main()
