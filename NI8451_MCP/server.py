"""
server.py
---------
把 NI USB-8451 的 I2C 功能包装成一个 MCP 服务。

只能运行在 Windows 上（因为依赖 Ni845x.dll），且需要：
  1. 已安装 NI-845x 驱动
  2. USB-8451 已连接到本机

用法：
    pip install "mcp[cli]"
    mcp dev server.py        # 用 MCP Inspector 本地调试
    mcp install server.py    # 安装进 Claude Desktop
或者直接：
    python server.py
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Optional

from mcp.server.fastmcp import FastMCP

from ni845x_i2c import NI845xI2C, NI845xError

# 进程内的单例设备连接（一个 MCP 服务进程对应一个物理设备连接）
_i2c: Optional[NI845xI2C] = None


@asynccontextmanager
async def lifespan(app: FastMCP):
    global _i2c
    try:
        yield
    finally:
        if _i2c is not None:
            _i2c.close()
            _i2c = None


mcp = FastMCP("NI USB-8451 I2C", lifespan=lifespan)


def _require_connection() -> NI845xI2C:
    if _i2c is None or _i2c.device_handle is None:
        raise RuntimeError("尚未连接设备，请先调用 connect 工具。")
    return _i2c


def _hex_to_bytes(data_hex: str) -> bytes:
    """接受 '00 10 A5' / '0010a5' / '0x00,0x10' 等宽松格式的十六进制字符串。"""
    normalized = (
        data_hex.replace("0x", "")
        .replace("0X", "")
        .replace(",", " ")
        .replace("-", " ")
        .strip()
    )
    if not normalized:
        return b""

    if any(char.isspace() for char in normalized):
        # 有分隔符：按单个字节逐个解析，例如 "00 10 A5"
        return bytes(int(tok, 16) for tok in normalized.split())

    # 没有分隔符：按连续两位一组的十六进制解析，例如 "0010A5"
    if len(normalized) % 2 != 0:
        raise ValueError(f"十六进制字符串长度必须是偶数：{data_hex!r}")
    return bytes.fromhex(normalized)


def _bytes_to_hex(data: bytes) -> str:
    return " ".join(f"0x{b:02X}" for b in data)


@mcp.tool()
def list_devices() -> list[str]:
    """列出当前通过 USB 连接的所有 NI USB-845x（8451/8452）设备资源名。"""
    probe = _i2c if _i2c is not None else NI845xI2C()
    return probe.find_devices()


@mcp.tool()
def connect(device_name: Optional[str] = None, io_voltage: Optional[float] = None) -> str:
    """连接一个 NI USB-845x 设备，不执行 I2C 传输。

    Args:
        device_name: 设备资源名，不填时仅在恰好发现一台设备时自动选择。
        io_voltage: 默认不设置。USB-8451 必须留空，I2C 电平由外部上拉决定。
            仅支持可编程参考电压的设备（如 USB-8452）可指定 1.2/1.5/1.8/2.5/3.3V，不支持 5V。
    """
    global _i2c
    if _i2c is not None and _i2c.device_handle is not None:
        if device_name not in (None, _i2c.device_name) or io_voltage is not None:
            raise RuntimeError("已有设备连接；切换设备或设置电压前请先 disconnect。")
        return f"已经连接到设备 {_i2c.device_name}，无需重复连接。"

    device = NI845xI2C()
    device.open(device_name=device_name, io_voltage=io_voltage)
    _i2c = device
    voltage_info = "未设置 I/O 电压" if io_voltage is None else f"I/O 参考电压 {io_voltage}V"
    return f"已连接设备：{_i2c.device_name}（{voltage_info}）"


@mcp.tool()
def disconnect() -> str:
    """断开当前连接的设备，释放所有 I2C 配置句柄。"""
    global _i2c
    if _i2c is None or _i2c.device_handle is None:
        return "当前没有已连接的设备。"
    name = _i2c.device_name
    _i2c.close()
    return f"已断开设备：{name}"


@mcp.tool()
def status() -> dict:
    """查看当前连接状态。"""
    if _i2c is None or _i2c.device_handle is None:
        return {"connected": False}
    return {"connected": True, "device_name": _i2c.device_name}


@mcp.tool()
def i2c_scan(clock_rate_khz: int = 100) -> list[str]:
    """扫描 I2C 总线（地址范围 0x03~0x77），返回所有有应答的从机地址。

    Args:
        clock_rate_khz: I2C 时钟频率，单位 kHz，默认 100（标准模式）。
    """
    dev = _require_connection()
    try:
        addrs = dev.scan(clock_rate_khz=clock_rate_khz)
    except NI845xError as e:
        raise RuntimeError(f"扫描失败：{e}") from e
    return [f"0x{a:02X}" for a in addrs]


@mcp.tool()
def i2c_write(
    address: int, data_hex: str, clock_rate_khz: int = 100, address_size: int = 7
) -> str:
    """向指定 I2C 地址写入数据。

    Args:
        address: 7 位（或 10 位）从机地址，例如 0x50。
        data_hex: 要写入的数据，十六进制字符串，例如 "00 10 A5" 或 "0010A5"。
        clock_rate_khz: I2C 时钟频率（kHz），默认 100。
        address_size: 地址位数，7 或 10，默认 7。
    """
    dev = _require_connection()
    data = _hex_to_bytes(data_hex)
    try:
        dev.write(address, data, clock_rate_khz=clock_rate_khz, address_size=address_size)
    except NI845xError as e:
        raise RuntimeError(f"写入 0x{address:02X} 失败：{e}") from e
    return f"已向地址 0x{address:02X} 写入 {len(data)} 字节：{_bytes_to_hex(data)}"


@mcp.tool()
def i2c_read(
    address: int, num_bytes: int, clock_rate_khz: int = 100, address_size: int = 7
) -> str:
    """从指定 I2C 地址读取数据。

    Args:
        address: 7 位（或 10 位）从机地址，例如 0x50。
        num_bytes: 要读取的字节数。
        clock_rate_khz: I2C 时钟频率（kHz），默认 100。
        address_size: 地址位数，7 或 10，默认 7。
    """
    dev = _require_connection()
    try:
        data = dev.read(
            address, num_bytes, clock_rate_khz=clock_rate_khz, address_size=address_size
        )
    except NI845xError as e:
        raise RuntimeError(f"读取 0x{address:02X} 失败：{e}") from e
    return _bytes_to_hex(data)


@mcp.tool()
def i2c_write_read(
    address: int,
    data_hex: str,
    num_bytes_to_read: int,
    clock_rate_khz: int = 100,
    address_size: int = 7,
) -> str:
    """向指定 I2C 地址写入数据，然后立即读取返回数据（常用于"写寄存器地址再读值"）。

    Args:
        address: 7 位（或 10 位）从机地址，例如 0x50。
        data_hex: 先写入的数据（例如寄存器地址），十六进制字符串。
        num_bytes_to_read: 写入之后要读取的字节数。
        clock_rate_khz: I2C 时钟频率（kHz），默认 100。
        address_size: 地址位数，7 或 10，默认 7。
    """
    dev = _require_connection()
    write_data = _hex_to_bytes(data_hex)
    try:
        data = dev.write_read(
            address, write_data, num_bytes_to_read,
            clock_rate_khz=clock_rate_khz, address_size=address_size,
        )
    except NI845xError as e:
        raise RuntimeError(f"写读 0x{address:02X} 失败：{e}") from e
    return _bytes_to_hex(data)


if __name__ == "__main__":
    mcp.run(transport="stdio")
