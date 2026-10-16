"""
server.py
==========
MCP server 入口。用 FastMCP 把 scope_commands.py 里的功能注册成
Claude 可以调用的"工具"。

启动方式（供 Claude Code / Claude Desktop 配置调用）：
    python server.py

配置文件：默认读取同目录下的 scopes.json，可以用环境变量
TEK_SCOPE_CONFIG 指定别的路径。
"""

import json
import os

from mcp.server.fastmcp import FastMCP

import scope_commands
from scope_client import ScopeConnectionError, ScopeCommandError

mcp = FastMCP("tek-oscilloscope")

CONFIG_PATH = os.environ.get(
    "TEK_SCOPE_CONFIG",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "scopes.json"),
)

# 截图保存目录，默认放在当前目录下的 screenshots 文件夹
SCREENSHOT_DIR = os.environ.get(
    "TEK_SCREENSHOT_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "screenshots"),
)


def _load_scope_ips():
    if not os.path.exists(CONFIG_PATH):
        return []
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"配置文件 {CONFIG_PATH} 无法读取: {e}") from e

    if not isinstance(data, dict) or not isinstance(data.get("scopes", []), list):
        raise ValueError(f"配置文件 {CONFIG_PATH} 格式错误，scopes 必须是数组")
    scopes = data.get("scopes", [])
    return [str(ip).strip() for ip in scopes if str(ip).strip()]


def _wrap_errors(fn, *args, **kwargs):
    """统一把底层异常转成对 Claude 友好的文本，而不是让整个调用崩掉"""
    try:
        return fn(*args, **kwargs)
    except (ScopeConnectionError, ScopeCommandError, ValueError, TypeError) as e:
        return {"error": str(e)}


# ---------------------------------------------------------
# 配置 / 状态查询
# ---------------------------------------------------------

@mcp.tool()
def list_scopes() -> list:
    """列出配置文件(scopes.json)里登记的所有示波器 IP 地址"""
    try:
        ips = _load_scope_ips()
    except ValueError as e:
        return [{"error": str(e)}]
    if not ips:
        return [f"配置文件 {CONFIG_PATH} 为空或不存在，请添加示波器 IP"]
    return ips


@mcp.tool()
def get_idn(ip: str) -> str:
    """连接测试：查询指定IP示波器的型号/序列号/固件版本，用于验证网络是否连通"""
    return _wrap_errors(scope_commands.get_idn, ip)


# ---------------------------------------------------------
# 只读：截图 / 波形 / 测量值
# ---------------------------------------------------------

@mcp.tool()
def get_screenshot(ip: str) -> str:
    """截取示波器当前屏幕画面，保存为PNG文件，返回本地文件路径供查看"""
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    safe_ip = ip.replace(".", "_")
    filename = f"{safe_ip}_screenshot.png"
    local_path = os.path.join(SCREENSHOT_DIR, filename)
    result = _wrap_errors(scope_commands.get_screenshot, ip, local_path)
    if isinstance(result, dict) and "error" in result:
        return f"截图失败：{result['error']}"
    return result


@mcp.tool()
def get_waveform(ip: str, channel: str = "CH1", max_points: int = 100_000) -> dict:
    """
    读取指定通道的原始波形采样数据（时间数组+电压数组，已换算成秒/伏特）。
    channel: CH1/CH2/CH3/CH4
    max_points: 最多返回的采样点数，默认10万点；调大会更慢
    """
    return _wrap_errors(scope_commands.get_waveform, ip, channel, max_points)


@mcp.tool()
def get_measurement(ip: str, channel: str = "CH1", meas_type: str = "FREQUENCY") -> dict:
    """
    读取自动测量值；自动识别型号，MDO3000 使用即时测量，不占用面板测量槽位。
    meas_type 常见取值: FREQUENCY, PERIOD, PK2Pk, AMPlitude, MEAN, RMS,
    RISetime, FALltime, PWIDth, NWIDth, MAXimum, MINImum, HIGH, LOW, OVERshoot
    """
    return _wrap_errors(scope_commands.get_measurement, ip, channel, meas_type)


# ---------------------------------------------------------
# 控制：通道 / 触发 / 采集 / 时基
# ---------------------------------------------------------

@mcp.tool()
def set_channel(ip: str, channel: str, enabled: bool = None, scale: float = None,
                 coupling: str = None, position: float = None) -> dict:
    """
    设置通道参数（只改传入的字段，其余保持不变）。
    channel: CH1/CH2/CH3/CH4
    enabled: 是否显示该通道
    scale: 垂直挡位 (V/div)
    coupling: AC / DC / DCREJect（DCREJect 需要探头支持）
    position: 垂直位置 (格)
    """
    return _wrap_errors(scope_commands.set_channel, ip, channel, enabled, scale, coupling, position)


@mcp.tool()
def set_trigger(ip: str, source: str = None, level: float = None,
                 mode: str = None, slope: str = None) -> dict:
    """
    设置边沿触发参数（只改传入的字段，其余保持不变）。
    source: CH1/CH2/CH3/CH4
    level: 触发电平 (伏特)
    mode: AUTO / NORMal
    slope: RISe / FALl / EITher
    """
    return _wrap_errors(scope_commands.set_trigger, ip, source, level, mode, slope)


@mcp.tool()
def run_acquisition(ip: str, action: str) -> dict:
    """采集控制。action: run(连续运行) / stop(停止) / single(单次触发)"""
    return _wrap_errors(scope_commands.run_acquisition, ip, action)


@mcp.tool()
def set_horizontal(ip: str, scale: float = None, position: float = None) -> dict:
    """
    设置时基参数（只改传入的字段，其余保持不变）。
    scale: 时基 (秒/格)
    position: 水平位置 (百分比)
    """
    return _wrap_errors(scope_commands.set_horizontal, ip, scale, position)


if __name__ == "__main__":
    mcp.run()
