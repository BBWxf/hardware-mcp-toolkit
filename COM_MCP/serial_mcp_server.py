"""协议无关的串口收发 stdio MCP Server。"""

from __future__ import annotations

import base64
import binascii
import threading
import time
from dataclasses import dataclass
from typing import Any

import serial
from mcp.server.fastmcp import FastMCP
from serial.tools import list_ports

mcp = FastMCP("Serial MCP")


@dataclass
class SerialError:
    """MCP 返回给调用方的串口错误。"""

    code: str
    message: str
    details: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            result["details"] = self.details
        return result


def _success(**data: Any) -> dict[str, Any]:
    return {"ok": True, "error": None, **data}


def _failure(error: SerialError) -> dict[str, Any]:
    return {"ok": False, "error": error.as_dict()}


def _serial_error(exc: BaseException) -> SerialError:
    """将 pyserial/系统异常转换为可供调用方判断的错误类型。"""
    if isinstance(exc, serial.SerialTimeoutException):
        return SerialError("serial_timeout", "串口操作超时。", {"exception": str(exc)})

    message = str(exc)
    normalized = message.lower()
    if isinstance(exc, PermissionError) or any(
        marker in normalized
        for marker in ("access is denied", "permission denied", "resource busy", "already in use")
    ):
        return SerialError("port_busy", "串口被占用或没有访问权限。", {"exception": message})
    if any(
        marker in normalized
        for marker in (
            "could not open port",
            "no such file",
            "file not found",
            "device not configured",
            "device disconnected",
            "semaphore timeout",
        )
    ):
        return SerialError("device_disconnected", "串口设备不可用，可能已断开或端口不存在。", {"exception": message})
    if isinstance(exc, (serial.SerialException, OSError)):
        return SerialError("serial_io_error", "串口通信失败。", {"exception": message})
    return SerialError("unexpected_error", "发生未预期的串口服务错误。", {"exception": message})


def _decode_input(data: str, encoding: str) -> bytes:
    normalized = encoding.lower()
    if normalized == "utf8":
        return data.encode("utf-8")
    if normalized == "hex":
        compact = "".join(data.split())
        if compact.startswith(("0x", "0X")):
            compact = compact[2:]
        return bytes.fromhex(compact)
    if normalized == "base64":
        return base64.b64decode(data, validate=True)
    raise ValueError("encoding 仅支持 utf8、hex 或 base64。")


def _format_bytes(data: bytes, encoding: str) -> str:
    normalized = encoding.lower()
    if normalized == "utf8":
        return data.decode("utf-8", errors="replace")
    if normalized == "hex":
        return data.hex()
    if normalized == "base64":
        return base64.b64encode(data).decode("ascii")
    raise ValueError("encoding 仅支持 utf8、hex 或 base64。")


def _payload(data: bytes, encoding: str) -> dict[str, Any]:
    try:
        utf8: dict[str, Any] = {"ok": True, "text": data.decode("utf-8")}
    except UnicodeDecodeError as exc:
        utf8 = {"ok": False, "text": None, "error": str(exc)}

    return {
        "bytes_read": len(data),
        "hex": data.hex(),
        "utf8": utf8,
        "encoding": encoding.lower(),
        "data": _format_bytes(data, encoding),
    }


class SerialConnectionManager:
    """维护 stdio MCP 进程生命周期内唯一的串口连接。"""

    def __init__(self) -> None:
        self._serial: serial.Serial | None = None
        self._lock = threading.RLock()

    def _get_open_serial(self) -> serial.Serial:
        if self._serial is None or not self._serial.is_open:
            raise RuntimeError("serial_not_open")
        return self._serial

    def connect(
        self,
        port: str,
        baud_rate: int,
        data_bits: int,
        parity: str,
        stop_bits: float,
        flow_control: str,
    ) -> dict[str, Any]:
        with self._lock:
            if self._serial is not None and self._serial.is_open:
                return _failure(
                    SerialError(
                        "connection_already_open",
                        "已有串口连接，请先调用 disconnect 后再连接其他端口。",
                        {"port": self._serial.port},
                    )
                )

            try:
                if baud_rate <= 0:
                    raise ValueError("baud_rate 必须大于 0。")
                if data_bits not in (5, 6, 7, 8):
                    raise ValueError("data_bits 仅支持 5、6、7 或 8。")
                normalized_parity = parity.upper()
                parity_values = {
                    "N": serial.PARITY_NONE,
                    "E": serial.PARITY_EVEN,
                    "O": serial.PARITY_ODD,
                    "M": serial.PARITY_MARK,
                    "S": serial.PARITY_SPACE,
                }
                if normalized_parity not in parity_values:
                    raise ValueError("parity 仅支持 N、E、O、M 或 S。")
                if stop_bits not in (1, 1.5, 2):
                    raise ValueError("stop_bits 仅支持 1、1.5 或 2。")
                normalized_flow_control = flow_control.lower()
                if normalized_flow_control not in ("none", "rtscts", "xonxoff"):
                    raise ValueError("flow_control 仅支持 none、rtscts 或 xonxoff。")

                self._serial = serial.Serial(
                    port=port,
                    baudrate=baud_rate,
                    bytesize=data_bits,
                    parity=parity_values[normalized_parity],
                    stopbits=stop_bits,
                    timeout=0,
                    write_timeout=5,
                    rtscts=normalized_flow_control == "rtscts",
                    xonxoff=normalized_flow_control == "xonxoff",
                )
                return _success(
                    connection={
                        "port": self._serial.port,
                        "baud_rate": self._serial.baudrate,
                        "data_bits": self._serial.bytesize,
                        "parity": normalized_parity,
                        "stop_bits": self._serial.stopbits,
                        "flow_control": normalized_flow_control,
                    }
                )
            except (ValueError, TypeError) as exc:
                self._serial = None
                return _failure(SerialError("validation_error", str(exc)))
            except BaseException as exc:
                self._serial = None
                return _failure(_serial_error(exc))

    def disconnect(self) -> dict[str, Any]:
        with self._lock:
            if self._serial is None or not self._serial.is_open:
                self._serial = None
                return _failure(SerialError("serial_not_open", "当前没有已打开的串口连接。"))
            try:
                port = self._serial.port
                self._serial.close()
                self._serial = None
                return _success(disconnected_port=port)
            except BaseException as exc:
                return _failure(_serial_error(exc))

    def send(self, data: str, encoding: str) -> dict[str, Any]:
        try:
            payload = _decode_input(data, encoding)
        except (ValueError, binascii.Error) as exc:
            return _failure(SerialError("validation_error", f"发送数据无效：{exc}"))

        with self._lock:
            try:
                connection = self._get_open_serial()
                written = connection.write(payload)
                connection.flush()
                return _success(bytes_sent=written, hex=payload.hex(), encoding=encoding.lower())
            except RuntimeError as exc:
                if str(exc) == "serial_not_open":
                    return _failure(SerialError("serial_not_open", "请先调用 connect 打开串口。"))
                return _failure(_serial_error(exc))
            except BaseException as exc:
                return _failure(_serial_error(exc))

    def read(self, timeout_ms: int, max_bytes: int, encoding: str) -> dict[str, Any]:
        if timeout_ms < 0 or max_bytes <= 0:
            return _failure(SerialError("validation_error", "timeout_ms 必须大于等于 0，max_bytes 必须大于 0。"))
        try:
            _format_bytes(b"", encoding)
        except ValueError as exc:
            return _failure(SerialError("validation_error", str(exc)))

        with self._lock:
            try:
                connection = self._get_open_serial()
                previous_timeout = connection.timeout
                connection.timeout = timeout_ms / 1000
                try:
                    data = connection.read(max_bytes)
                finally:
                    connection.timeout = previous_timeout
                return _success(**_payload(data, encoding))
            except RuntimeError as exc:
                if str(exc) == "serial_not_open":
                    return _failure(SerialError("serial_not_open", "请先调用 connect 打开串口。"))
                return _failure(_serial_error(exc))
            except BaseException as exc:
                return _failure(_serial_error(exc))

    def read_until(
        self, idle_timeout_ms: int, max_wait_ms: int, max_bytes: int, encoding: str
    ) -> dict[str, Any]:
        if idle_timeout_ms < 0 or max_wait_ms < 0 or max_bytes <= 0:
            return _failure(
                SerialError(
                    "validation_error",
                    "idle_timeout_ms、max_wait_ms 必须大于等于 0，max_bytes 必须大于 0。",
                )
            )
        try:
            _format_bytes(b"", encoding)
        except ValueError as exc:
            return _failure(SerialError("validation_error", str(exc)))

        with self._lock:
            try:
                connection = self._get_open_serial()
                started_at = time.monotonic()
                last_data_at: float | None = None
                received = bytearray()
                stop_reason = "max_wait"

                while len(received) < max_bytes:
                    now = time.monotonic()
                    if (now - started_at) * 1000 >= max_wait_ms:
                        break
                    if last_data_at is not None and (now - last_data_at) * 1000 >= idle_timeout_ms:
                        stop_reason = "idle_timeout"
                        break

                    remaining = max_bytes - len(received)
                    waiting = connection.in_waiting
                    if waiting:
                        chunk = connection.read(min(waiting, remaining))
                        if chunk:
                            received.extend(chunk)
                            last_data_at = time.monotonic()
                            continue

                    time.sleep(0.005)

                if len(received) == max_bytes:
                    stop_reason = "max_bytes"
                elif last_data_at is None:
                    stop_reason = "max_wait_no_data"

                return _success(
                    **_payload(bytes(received), encoding),
                    stop_reason=stop_reason,
                    elapsed_ms=round((time.monotonic() - started_at) * 1000, 1),
                )
            except RuntimeError as exc:
                if str(exc) == "serial_not_open":
                    return _failure(SerialError("serial_not_open", "请先调用 connect 打开串口。"))
                return _failure(_serial_error(exc))
            except BaseException as exc:
                return _failure(_serial_error(exc))

    def set_control_lines(self, rts: bool | None, dtr: bool | None) -> dict[str, Any]:
        if rts is None and dtr is None:
            return _failure(SerialError("validation_error", "rts 和 dtr 至少需要指定一个值。"))

        with self._lock:
            try:
                connection = self._get_open_serial()
                if rts is not None:
                    connection.rts = rts
                if dtr is not None:
                    connection.dtr = dtr
                return _success(rts=connection.rts, dtr=connection.dtr)
            except RuntimeError as exc:
                if str(exc) == "serial_not_open":
                    return _failure(SerialError("serial_not_open", "请先调用 connect 打开串口。"))
                return _failure(_serial_error(exc))
            except BaseException as exc:
                return _failure(_serial_error(exc))


manager = SerialConnectionManager()


def list_available_ports() -> dict[str, Any]:
    """列出当前系统识别到的可用串口，不会打开任何端口。"""
    try:
        ports = [
            {
                "device": port.device,
                "name": port.name,
                "description": port.description,
                "hwid": port.hwid,
                "vid": port.vid,
                "pid": port.pid,
                "manufacturer": port.manufacturer,
                "product": port.product,
                "serial_number": port.serial_number,
            }
            for port in list_ports.comports()
        ]
        return _success(ports=ports)
    except BaseException as exc:
        return _failure(_serial_error(exc))


@mcp.tool(name="list_ports")
def list_ports_tool() -> dict[str, Any]:
    """列出当前系统识别到的可用串口，不会打开任何端口。"""
    return list_available_ports()


@mcp.tool()
def connect(
    port: str,
    baud_rate: int = 9600,
    data_bits: int = 8,
    parity: str = "N",
    stop_bits: float = 1,
    flow_control: str = "none",
) -> dict[str, Any]:
    """打开一个串口。flow_control 支持 none、rtscts、xonxoff。"""
    return manager.connect(port, baud_rate, data_bits, parity, stop_bits, flow_control)


@mcp.tool()
def disconnect() -> dict[str, Any]:
    """关闭当前已打开的串口。"""
    return manager.disconnect()


@mcp.tool()
def send(data: str, encoding: str = "utf8") -> dict[str, Any]:
    """发送串口数据；encoding 支持 utf8、hex、base64，发送时不会自动追加换行。"""
    return manager.send(data, encoding)


@mcp.tool()
def read(timeout_ms: int = 1000, max_bytes: int = 4096, encoding: str = "hex") -> dict[str, Any]:
    """单次超时读取。结果总是包含 hex 和 UTF-8 解码尝试。"""
    return manager.read(timeout_ms, max_bytes, encoding)


@mcp.tool()
def read_until(
    idle_timeout_ms: int = 100,
    max_wait_ms: int = 1000,
    max_bytes: int = 4096,
    encoding: str = "hex",
) -> dict[str, Any]:
    """读取到收到数据后的连续空闲期，或绝对最大等待时间；结果总是包含 hex 和 UTF-8 解码尝试。"""
    return manager.read_until(idle_timeout_ms, max_wait_ms, max_bytes, encoding)


@mcp.tool()
def set_control_lines(rts: bool | None = None, dtr: bool | None = None) -> dict[str, Any]:
    """设置 RTS/DTR。可能触发设备复位或烧录模式，仅在协议明确要求或已获得用户确认时调用。"""
    return manager.set_control_lines(rts, dtr)


if __name__ == "__main__":
    mcp.run(transport="stdio")
