"""
scope_commands.py
===================
具体功能实现层：每个函数对应一个"能力"，内部调用 scope_client.TekScope
发送对应的 SCPI 命令。server.py 里的 MCP 工具只是薄薄一层，直接调用这里的函数。

这样分层的好处：以后想在其他地方复用这些功能（比如写个命令行小工具、
或者接入别的自动化系统），不用碰 MCP 相关代码，直接 import 这个文件就行。

*** 重要说明 ***
下面的 SCPI 命令是根据泰克官方 4/5/6 Series MSO Programmer Manual
（https://download.tek.com/manual/4-5-6Series-MSO-Programmer-Interface-Manual_EN-US_077130508.pdf）
以及泰克社区/第三方实现交叉核实过的。其中：
  - *IDN?、截图 (SAVE:IMAGE + FILESystem:READFile)、波形 (CURVE? + WFMOutpre)
    这几组命令已经过搜索资料确认，可信度高。
  - 测量 (MEASUrement:MEAS<x>:...) 这组命令的具体路径在泰克不同世代产品线上
    变动较大，4/5/6 系列用的是较新的"测量槽位"架构，我按最常见的命名规律实现，
    但没有找到 100% 逐字确认的例子。如果调用报错，请看本文件末尾的
    "排查测量命令" 章节，两分钟就能自己定位并修正。
"""

import re
import time

import pyvisa

from scope_client import ScopeCommandError, ScopeConnectionError, TekScope


_SUPPORTED_CHANNELS = {"CH1", "CH2", "CH3", "CH4"}
_TOKEN_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")


def _normalize_channel(channel: str) -> str:
    """校验并标准化模拟通道名，避免把非法 SCPI 片段直接拼进命令。"""
    if not isinstance(channel, str):
        raise ValueError(f"channel 必须是字符串，收到: {channel!r}")
    normalized = channel.strip().upper()
    if normalized not in _SUPPORTED_CHANNELS:
        raise ValueError(
            f"不支持的通道: {channel!r}，只能是 CH1/CH2/CH3/CH4"
        )
    return normalized


def _normalize_token(value: str, field_name: str) -> str:
    """校验只允许作为单个 SCPI token 传入的字符串。"""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} 必须是字符串，收到: {value!r}")
    normalized = value.strip()
    if not normalized or not _TOKEN_PATTERN.fullmatch(normalized):
        raise ValueError(f"{field_name} 包含无效字符: {value!r}")
    return normalized


# =========================================================
# 连接测试 / 设备信息
# =========================================================

def get_idn(ip: str) -> str:
    """查询示波器型号、序列号、固件版本，用来验证网络连接是否通畅"""
    with TekScope(ip) as scope:
        return scope.query("*IDN?")


# =========================================================
# 截图
# =========================================================

_REMOTE_SCREENSHOT_FILENAME = "mcp_screenshot.png"
_PNG_HEADER = b"\x89PNG\r\n\x1a\n"
_PNG_END = b"\x00\x00\x00\x00IEND\xaeB`\x82"


def get_screenshot(ip: str, local_path: str) -> str:
    """通过 VXI-11 截取当前屏幕并保存为本地 PNG 文件。"""
    resource_manager = pyvisa.ResourceManager("@py")
    instrument = None
    original_filename = None
    data = b""

    try:
        instrument = resource_manager.open_resource(f"TCPIP0::{ip}::inst0::INSTR")
        instrument.timeout = 60_000  # PyVISA 的超时单位是毫秒

        # 该型号通过 FILEName 配置保存目标，SAVE:IMAGE 本身不接受文件名参数。
        original_filename = instrument.query("SAVE:IMAGe:FILEName?").strip()
        instrument.write(f'SAVE:IMAGe:FILEName "{_REMOTE_SCREENSHOT_FILENAME}"')
        instrument.write("SAVE:IMAGe")
        if instrument.query("*OPC?").strip() != "1":
            raise ScopeCommandError("示波器未确认截图文件已保存完成")

        instrument.write(f'FILESystem:READFile "{_REMOTE_SCREENSHOT_FILENAME}"')
        data = instrument.read_raw()
    except pyvisa.errors.VisaIOError as e:
        raise ScopeConnectionError(f"通过 VXI-11 读取示波器截图失败: {e}") from e
    finally:
        if instrument is not None:
            try:
                instrument.write(f'FILESystem:DELEte "{_REMOTE_SCREENSHOT_FILENAME}"')
            except pyvisa.errors.VisaIOError:
                pass
            if original_filename is not None:
                try:
                    instrument.write(f"SAVE:IMAGe:FILEName {original_filename}")
                except pyvisa.errors.VisaIOError:
                    pass
            instrument.close()
        resource_manager.close()

    end_index = data.find(_PNG_END)
    if not data.startswith(_PNG_HEADER) or end_index < 0:
        raise ScopeCommandError("示波器返回的截图不是完整的 PNG 数据")

    with open(local_path, "wb") as f:
        f.write(data[:end_index + len(_PNG_END)])

    return local_path


# =========================================================
# 波形原始数据
# =========================================================

def get_waveform(ip: str, channel: str = "CH1", max_points: int = 100_000) -> dict:
    """
    读取指定通道的原始采样数据，换算成真实的 时间(秒) / 电压(伏特) 数组。

    max_points: 最多读取的采样点数。MSO44 单通道记录长度可以到几千万点，
    全部用 ASCII 传输会非常慢，所以默认限制读取窗口内最多10万点
    （这对于"看波形形状"来说綽綽有余）。如果需要更多点数，调大这个参数即可，
    但会明显变慢。

    返回: {"channel": ..., "n_points": ..., "time": [...], "voltage": [...]}
    """
    channel = _normalize_channel(channel)
    if isinstance(max_points, bool) or not isinstance(max_points, int) or max_points <= 0:
        raise ValueError("max_points 必须是大于 0 的整数")

    with TekScope(ip) as scope:
        scope.write(f"DATa:SOUrce {channel}")
        # 用 ASCII 编码传输波形数据：比二进制慢，但不用处理字节序/编码格式，
        # 出错概率低很多，对于"看形状、做分析"这个用途完全够用。
        scope.write("DATa:ENCdg ASCIi")

        record_length = int(scope.query("HORizontal:RECOrdlength?"))
        n_points = min(record_length, max_points)

        scope.write("DATa:STARt 1")
        scope.write(f"DATa:STOP {n_points}")

        # 波形换算所需的比例参数：
        # 电压 = (原始值 - YOFF) * YMULT + YZERO
        # 时间 = XZERO + index * XINCR
        x_incr = float(scope.query("WFMOutpre:XINcr?"))
        x_zero = float(scope.query("WFMOutpre:XZEro?"))
        y_mult = float(scope.query("WFMOutpre:YMUlt?"))
        y_zero = float(scope.query("WFMOutpre:YZEro?"))
        y_off = float(scope.query("WFMOutpre:YOFf?"))

        raw = scope.query("CURVE?")  # ASCII 模式下直接是逗号分隔的数值字符串

    raw_values = [float(v) for v in raw.split(",") if v.strip() != ""]
    times = [x_zero + i * x_incr for i in range(len(raw_values))]
    voltages = [(v - y_off) * y_mult + y_zero for v in raw_values]

    return {
        "channel": channel,
        "n_points": len(raw_values),
        "time": times,
        "voltage": voltages,
    }


# =========================================================
# 自动测量值
# =========================================================

# MEASU 槽位与类型的一个简单缓存，避免每次都重新配置同一个测量槽位
# 目前用 MEAS1 固定作为"临时测量"槽位（如果人也在用示波器面板上的 MEAS1，
# 会互相影响，这是本方案的已知限制，见开发文档）
_TEMP_MEAS_SLOT = "MEAS1"

def get_measurement(ip: str, channel: str = "CH1", meas_type: str = "FREQUENCY") -> dict:
    """
    读取自动测量值。

    meas_type 常见取值（泰克标准测量类型名，全部大写）：
      FREQUENCY, PERIOD, PK2Pk, AMPlitude, MEAN, RMS,
      RISetime, FALltime, PWIDth（正脉宽）, NWIDth（负脉宽）,
      MAXimum, MINImum, HIGH, LOW, OVERshoot, BURst 等

    返回: {"channel", "type", "value"}
    """
    channel = _normalize_channel(channel)
    meas_type = _normalize_token(meas_type, "meas_type")

    with TekScope(ip) as scope:
        scope.write(f"MEASUrement:{_TEMP_MEAS_SLOT}:SOUrce1 {channel}")
        scope.write(f"MEASUrement:{_TEMP_MEAS_SLOT}:TYPe {meas_type}")
        scope.write(f"MEASUrement:{_TEMP_MEAS_SLOT}:STATE ON")
        # 给示波器一点时间完成一次采集+测量
        time.sleep(0.3)
        value_str = scope.query(f"MEASUrement:{_TEMP_MEAS_SLOT}:VALue?")

    try:
        value = float(value_str)
    except ValueError:
        value = value_str  # 有些异常情况示波器返回文本(如 "***"表示无法测量)，原样返回

    return {"channel": channel, "type": meas_type, "value": value}


# =========================================================
# 通道设置
# =========================================================

def set_channel(ip: str, channel: str, enabled: bool = None, scale: float = None,
                 coupling: str = None, position: float = None) -> dict:
    """
    设置通道参数，未传的参数保持原样不动。

    channel: "CH1" / "CH2" / "CH3" / "CH4"
    enabled: True=打开该通道显示, False=关闭
    scale: 垂直挡位，单位 V/div，比如 0.5 表示 0.5V/格
    coupling: "AC" / "DC" / "DCREJect"
    position: 垂直位置，单位"格"（div）
    """
    channel = _normalize_channel(channel)
    if enabled is not None and not isinstance(enabled, bool):
        raise ValueError("enabled 必须是布尔值")
    if coupling is not None:
        coupling = str(coupling).strip().upper()
        if coupling not in {"AC", "DC", "DCREJECT"}:
            raise ValueError("coupling 只能是 AC、DC 或 DCREJECT")

    applied = {}
    with TekScope(ip) as scope:
        if enabled is not None:
            state = "ON" if enabled else "OFF"
            scope.write(f"DISplay:GLObal:{channel}:STATE {state}")
            applied["enabled"] = enabled
        if scale is not None:
            scope.write(f"{channel}:SCAle {scale}")
            applied["scale"] = scale
        if coupling is not None:
            scope.write(f"{channel}:COUPling {coupling}")
            applied["coupling"] = coupling
        if position is not None:
            scope.write(f"{channel}:POSition {position}")
            applied["position"] = position
        errors = scope.check_error()

    return {"channel": channel, "applied": applied, "scope_errors": errors}


# =========================================================
# 触发设置（边沿触发）
# =========================================================

def set_trigger(ip: str, source: str = None, level: float = None,
                 mode: str = None, slope: str = None) -> dict:
    """
    设置边沿触发参数，未传的参数保持原样不动。

    source: "CH1" / "CH2" / ...
    level: 触发电平，单位伏特
    mode: "AUTO"（自动，无信号也会显示） / "NORMal"（等待触发才显示）
    slope: "RISe" / "FALl" / "EITher"
    """
    if source is not None:
        source = _normalize_channel(source)
    if mode is not None:
        mode = str(mode).strip().upper()
        if mode not in {"AUTO", "NORMAL"}:
            raise ValueError("mode 只能是 AUTO 或 NORMAL")
    if slope is not None:
        slope = str(slope).strip().upper()
        if slope not in {"RISE", "FALL", "EITHER"}:
            raise ValueError("slope 只能是 RISE、FALL 或 EITHER")

    applied = {}
    with TekScope(ip) as scope:
        scope.write("TRIGger:A:TYPe EDGE")
        if source is not None:
            scope.write(f"TRIGger:A:EDGE:SOUrce {source}")
            applied["source"] = source
        if slope is not None:
            scope.write(f"TRIGger:A:EDGE:SLOpe {slope}")
            applied["slope"] = slope
        if level is not None:
            scope.write(f"TRIGger:A:LEVel {level}")
            applied["level"] = level
        if mode is not None:
            scope.write(f"TRIGger:A:MODe {mode}")
            applied["mode"] = mode
        errors = scope.check_error()

    return {"applied": applied, "scope_errors": errors}


# =========================================================
# 采集控制
# =========================================================

def run_acquisition(ip: str, action: str) -> dict:
    """
    action: "run"（连续运行）/ "stop"（停止，冻结当前画面）/ "single"（单次触发采集一次）
    """
    if not isinstance(action, str):
        raise ValueError(f"action 必须是字符串，收到: {action!r}")
    action = action.strip().lower()
    if action not in {"run", "stop", "single"}:
        raise ValueError(f"未知的 action: {action}，只能是 run/stop/single")

    with TekScope(ip) as scope:
        if action == "run":
            scope.write("ACQuire:STOPAfter RUNSTop")
            scope.write("ACQuire:STATE ON")
        elif action == "stop":
            scope.write("ACQuire:STATE OFF")
        else:
            scope.write("ACQuire:STOPAfter SEQuence")
            scope.write("ACQuire:STATE ON")
        errors = scope.check_error()

    return {"action": action, "scope_errors": errors}


# =========================================================
# 时基设置
# =========================================================

def set_horizontal(ip: str, scale: float = None, position: float = None) -> dict:
    """
    scale: 时基，单位 秒/格 (s/div)，比如 0.001 表示 1ms/格
    position: 水平位置，单位百分比
    """
    applied = {}
    with TekScope(ip) as scope:
        if scale is not None:
            scope.write(f"HORizontal:SCAle {scale}")
            applied["scale"] = scale
        if position is not None:
            scope.write(f"HORizontal:POSition {position}")
            applied["position"] = position
        errors = scope.check_error()

    return {"applied": applied, "scope_errors": errors}
