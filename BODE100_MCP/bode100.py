"""Bode 100 官方 SCPI 文本接口；不实现测量分析或实验流程。"""

from __future__ import annotations

import csv
import math
import socket
import threading
import time
from contextlib import contextmanager
from typing import Literal

Measurement = Literal["S11", "S21", "GAIN", "CH1", "CH2", "Z", "R", "T"]
ZMethod = Literal["P1R", "TSH", "TSER", "ECO", "EBR", "IAD", "VCG"]
ResultFormat = Literal[
    "GDEL", "IMAG", "MLIN", "MLOG", "PHAS", "REAL", "UPH", "SWR",
    "SMIT", "SLIN", "SLOG", "SCOM", "SADM",
]
ZParameter = Literal["Z", "Y", "REFL", "CP", "CS", "LP", "LS", "RP", "RS", "Q", "QTG", "D"]
CalibrationRange = Literal["FULL", "USER"]
Standard = Literal["OPEN", "SHORT", "LOAD", "THRU"]


class BodeError(Exception):
    def __init__(self, code: str, message: str, **details):
        super().__init__(message)
        self.code = code
        self.details = details

    def as_dict(self) -> dict:
        return {"error": {"code": self.code, "message": str(self), **self.details}}


def number(value: float, name: str, minimum=None, maximum=None) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} 必须是有限数值")
    if minimum is not None and value < minimum or maximum is not None and value > maximum:
        raise ValueError(f"{name} 超出范围 [{minimum}, {maximum}]")
    return format(value, ".17g")


def choice(value: str, allowed, name: str) -> str:
    if value not in allowed:
        raise ValueError(f"{name} 必须是 {', '.join(map(str, allowed))}")
    return value


def quoted(value: str) -> str:
    if not value or len(value) > 4096 or any(ord(c) < 32 or ord(c) > 126 for c in value):
        raise ValueError("SCPI 文件名必须是非空 ASCII 文本，不能包含控制字符")
    return '"' + value.replace('"', '""') + '"'


def validate_command(command: str, *, query: bool) -> str:
    if not command or len(command) > 65536 or any(ord(c) < 32 or ord(c) > 126 for c in command):
        raise ValueError("SCPI 命令必须是单行 ASCII 文本，不能包含控制字符")
    outside = []
    quote = None
    index = 0
    while index < len(command):
        char = command[index]
        if quote:
            if char == quote:
                if index + 1 < len(command) and command[index + 1] == quote:
                    index += 1
                else:
                    quote = None
        elif char in ('"', "'"):
            quote = char
        else:
            outside.append(char)
        index += 1
    text = "".join(outside)
    if quote or ";" in text or text.count("?") != int(query):
        raise ValueError("每次只允许一条完整 SCPI 命令；query 必须有一个 ?，write 不能有 ?")
    header = command.strip().split(maxsplit=1)[0].upper().lstrip(":")
    if not query and header.split(":")[0] in ("FORM", "FORMAT"):
        if command.strip().split()[-1].upper() not in ("ASC", "ASCII"):
            raise ValueError("本服务仅支持 ASCII 数据传输，不能切换到二进制格式")
    return command.strip()


def floats(reply: str) -> list[float | None]:
    try:
        # JSON 不支持 NaN/Infinity，保留位置并以 null 表示，不伪造为 0。
        return [value if math.isfinite(value) else None for value in map(float, reply.split(","))]
    except ValueError as exc:
        raise BodeError("protocol_error", "设备返回了无效数值数组", reply=reply[:500]) from exc


def error_entries(reply: str) -> list[dict]:
    try:
        values = next(csv.reader([reply], skipinitialspace=True))
        if len(values) % 2:
            raise ValueError("错误队列不是编号/文本对")
        return [{"code": int(values[i]), "message": values[i + 1]} for i in range(0, len(values), 2)
                if int(values[i]) != 0]
    except (ValueError, csv.Error) as exc:
        raise BodeError("protocol_error", "无法解析 SCPI 错误队列", reply=reply[:500]) from exc


class Bode100:
    def __init__(self, host="127.0.0.1", port=5025, timeout_seconds=10.0):
        self.host = host
        self.port = port
        self.timeout = float(number(timeout_seconds, "timeout_seconds", 0.1, 3600))
        self._socket: socket.socket | None = None
        self._buffer = b""
        self._lock = threading.RLock()
        self._identity: str | None = None
        self._limits: dict | None = None
        self._revision = 0
        self._pending_revision: int | None = None
        self._completed_revision: int | None = None
        self._owns_lock = False

    @contextmanager
    def _session(self):
        with self._lock:
            if self._socket is None:
                raise BodeError("not_connected", "尚未连接官方 SCPI Server，请先调用 connect")
            yield

    def _close(self):
        connection, self._socket = self._socket, None
        if connection is not None:
            connection.close()
        self._buffer = b""
        self._identity = None
        self._limits = None
        self._owns_lock = False
        self._pending_revision = self._completed_revision = None

    def _write(self, command: str):
        try:
            self._socket.settimeout(self.timeout)
            self._socket.sendall((command + "\n").encode("ascii"))
        except (OSError, UnicodeError) as exc:
            self._close()
            raise BodeError("connection_error", "SCPI 发送失败，连接已关闭；操作不自动重试") from exc

    def _query(self, command: str, timeout_seconds: float | None = None) -> str:
        timeout = self.timeout if timeout_seconds is None else float(
            number(timeout_seconds, "timeout_seconds", 0.1, 3600)
        )
        self._write(command)
        deadline = time.monotonic() + timeout
        try:
            while b"\n" not in self._buffer:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError()
                self._socket.settimeout(remaining)
                chunk = self._socket.recv(65536)
                if not chunk:
                    raise ConnectionError("服务端关闭了连接")
                self._buffer += chunk
                if self._buffer.startswith(b"#"):
                    raise BodeError("protocol_error", "不支持 SCPI 二进制响应，仅支持 ASCII")
                if len(self._buffer) > 32 * 1024 * 1024:
                    raise BodeError("protocol_error", "SCPI 响应超过 32 MiB 限制")
            line, self._buffer = self._buffer.split(b"\n", 1)
            if self._buffer:
                raise BodeError("protocol_error", "单条查询收到额外响应，无法保证请求与响应对应")
            return line.decode("ascii").strip()
        except TimeoutError as exc:
            self._close()
            raise BodeError(
                "timeout", "SCPI 查询超时，已关闭连接防止迟到响应被误读；仪器操作可能仍在进行",
                command=command, timeout_seconds=timeout,
            ) from exc
        except (OSError, UnicodeError) as exc:
            self._close()
            raise BodeError("connection_error", "SCPI 接收失败，连接已关闭", command=command) from exc
        except BodeError:
            self._close()
            raise

    def _changed(self):
        self._revision += 1
        self._pending_revision = self._completed_revision = None

    def _check_errors(self):
        errors = error_entries(self._query(":SYST:ERR:ALL?"))
        if errors:
            raise BodeError("scpi_error", "设备拒绝命令或报告错误；此前已接受的设置不会回滚", errors=errors)

    def _apply(self, commands: list[str]):
        if not commands:
            raise ValueError("至少提供一个需要修改的参数")
        self._changed()
        for command in commands:
            self._write(command)
            self._check_errors()

    def _get_limits(self) -> dict:
        if self._limits is None:
            self._limits = {
                "frequency_min_hz": float(self._query(":SERV:SWE:FREQ:MIN?")),
                "frequency_max_hz": float(self._query(":SERV:SWE:FREQ:MAX?")),
                "points_max": int(float(self._query(":SERV:SWE:POIN?"))),
                "power_min_dbm": float(self._query(":SERV:SWE:POW:MIN?")),
                "power_max_dbm": float(self._query(":SERV:SWE:POW:MAX?")),
            }
            self._check_errors()
        return dict(self._limits)

    def connect(self, host: str | None = None, port: int | None = None) -> dict:
        with self._lock:
            if self._socket is not None:
                raise BodeError("already_connected", "已经连接；切换端点前请先 disconnect")
            host = self.host if host is None else host
            port = self.port if port is None else port
            if not host or any(c.isspace() for c in host):
                raise ValueError("host 必须是非空主机名或 IP")
            if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
                raise ValueError("port 必须是 1–65535 的整数")
            try:
                self._socket = socket.create_connection((host, port), timeout=self.timeout)
                self._buffer = b""
                identity = self._query("*IDN?")
                fields = [part.strip() for part in identity.split(",")]
                if len(fields) < 2 or fields[1].replace(" ", "").upper() != "BODE100":
                    raise BodeError("wrong_device", "该端点不是 Bode 100", identity=identity)
                if self._query(":SYST:LOCK:REQ?") != "1":
                    raise BodeError("device_locked", "无法获得设备锁，请关闭占用仪器的 BAS 或其他客户端")
                self._owns_lock = True
                self._write(":FORM ASC")
                self._check_errors()
                self.host, self.port, self._identity = host, port, identity
                self._changed()
                return {"connected": True, "host": host, "port": port, "identity": identity}
            except OSError as exc:
                self._close()
                raise BodeError("connection_error", "无法连接官方 SCPI Server", host=host, port=port) from exc
            except Exception:
                if self._socket is not None and self._owns_lock:
                    try:
                        self._query(":SYST:LOCK:REL?")
                    except BodeError:
                        pass
                self._close()
                raise

    def disconnect(self) -> dict:
        with self._lock:
            if self._socket is None:
                return {"connected": False}
            try:
                if self._owns_lock:
                    if self._query(":SYST:LOCK:REL?") != "1":
                        raise BodeError("protocol_error", "设备未确认释放锁")
            finally:
                self._close()
            return {"connected": False}

    def connection_status(self) -> dict:
        with self._lock:
            return {
                "connected": self._socket is not None, "host": self.host, "port": self.port,
                "identity": self._identity, "timeout_seconds": self.timeout,
                "note": "connected 仅表示本地会话状态；get_status 才会查询设备",
            }

    def get_limits(self) -> dict:
        with self._session():
            return self._get_limits()

    def get_status(self) -> dict:
        with self._session():
            operation = int(self._query(":STAT:OPER:COND?"))
            return {
                "operation_register": operation,
                "calibrating": bool(operation & 1), "sweeping": bool(operation & 8),
                "measuring": bool(operation & 16), "waiting_for_trigger": bool(operation & 32),
                "lock_requested": bool(operation & 512), "device_locked": bool(operation & 1024),
                "lock_owner": self._query(":SYST:LOCK:OWN?"),
                "confirmed_result_for_current_configuration": self._completed_revision == self._revision,
            }

    def get_configuration(self) -> dict:
        with self._session():
            result = {
                "measurement": self._query(":CALC:PAR:DEF?"),
                "result_format": self._query(":CALC:FORM?"),
                "start_hz": float(self._query(":SENS:FREQ:STAR?")),
                "stop_hz": float(self._query(":SENS:FREQ:STOP?")),
                "points": int(float(self._query(":SENS:SWE:POIN?"))),
                "sweep_type": self._query(":SENS:SWE:TYPE?"),
                "bandwidth_hz": float(self._query(":SENS:BAND?")),
                "dwell_seconds": float(self._query(":SENS:SWE:DWEL?")),
                "power_dbm": float(self._query(":SOUR:POW?")),
                "output_auto_off": self._query(":OUTP:AOFF?") == "1",
                "trigger_source": self._query(":TRIG:SOUR?"),
                "continuous": self._query(":INIT:CONT?") == "1",
            }
            if result["measurement"].upper() == "Z":
                result["z_method"] = self._query(":SENS:Z:METH?")
                result["z_parameter"] = self._query(":CALC:ZPAR:DEF?")
            self._check_errors()
            return result

    def configure_measurement(self, measurement: Measurement, z_method: ZMethod | None = None,
                              reset_defaults: bool = False) -> dict:
        choice(measurement, ("S11", "S21", "GAIN", "CH1", "CH2", "Z", "R", "T"), "measurement")
        if z_method is not None:
            choice(z_method, ("P1R", "TSH", "TSER", "ECO", "EBR", "IAD", "VCG"), "z_method")
            if measurement != "Z":
                raise ValueError("只有 measurement=Z 才能指定 z_method")
        suffix = ",DEF" if reset_defaults else ""
        commands = [f":CALC:PAR:DEF {measurement}{suffix}"]
        if z_method is not None:
            commands.append(f":SENS:Z:METH {z_method}{suffix}")
        with self._session():
            self._apply(commands)
            return {"measurement": self._query(":CALC:PAR:DEF?"),
                    "defaults_reset": reset_defaults,
                    "z_method": self._query(":SENS:Z:METH?") if measurement == "Z" else None}

    def configure_sweep(self, start_hz=None, stop_hz=None, points=None, sweep_type=None,
                        bandwidth_hz=None, dwell_seconds=None) -> dict:
        with self._session():
            commands = []
            limits = self._get_limits() if any(v is not None for v in (start_hz, stop_hz, points)) else {}
            for value, name in ((start_hz, "start_hz"), (stop_hz, "stop_hz")):
                if value is not None:
                    number(value, name, limits["frequency_min_hz"], limits["frequency_max_hz"])
            if points is not None:
                if isinstance(points, bool) or not isinstance(points, int):
                    raise ValueError("points 必须是整数")
                number(points, "points", 1, limits["points_max"])
                commands.append(f":SENS:SWE:POIN {points}")
            if start_hz is not None or stop_hz is not None:
                old_start = float(self._query(":SENS:FREQ:STAR?"))
                old_stop = float(self._query(":SENS:FREQ:STOP?"))
                new_start = old_start if start_hz is None else start_hz
                new_stop = old_stop if stop_hz is None else stop_hz
                if new_start > new_stop:
                    raise ValueError("start_hz 不能大于 stop_hz")
                frequencies = []
                if start_hz is not None:
                    frequencies.append(f":SENS:FREQ:STAR {number(start_hz, 'start_hz')}")
                if stop_hz is not None:
                    stop_command = f":SENS:FREQ:STOP {number(stop_hz, 'stop_hz')}"
                    # 整个区间向上移动时先设置 STOP，避免中间状态 START > STOP。
                    frequencies.insert(0 if new_start > old_stop else len(frequencies), stop_command)
                commands.extend(frequencies)
            if sweep_type is not None:
                commands.append(f":SENS:SWE:TYPE {choice(sweep_type, ('LIN', 'LOG'), 'sweep_type')}")
            if bandwidth_hz is not None:
                commands.append(f":SENS:BAND {number(bandwidth_hz, 'bandwidth_hz')}")
            if dwell_seconds is not None:
                commands.append(f":SENS:SWE:DWEL {number(dwell_seconds, 'dwell_seconds', 0, 60)}")
            self._apply(commands)
            return self.get_configuration()

    def configure_source(self, power_dbm=None, auto_off=None) -> dict:
        with self._session():
            commands = []
            if power_dbm is not None:
                limits = self._get_limits()
                commands.append(":SOUR:POW " + number(
                    power_dbm, "power_dbm", limits["power_min_dbm"], limits["power_max_dbm"]
                ))
            if auto_off is not None:
                if not isinstance(auto_off, bool):
                    raise ValueError("auto_off 必须是布尔值")
                commands.append(f":OUTP:AOFF {int(auto_off)}")
            self._apply(commands)
            return {"power_dbm": float(self._query(":SOUR:POW?")),
                    "auto_off": self._query(":OUTP:AOFF?") == "1"}

    def input_settings(self, channel: str, attenuation_db=None, impedance_ohm=None, gain_factor=None) -> dict:
        choice(channel, ("CH1", "CH2"), "channel")
        commands = []
        if attenuation_db is not None:
            commands.append(f":INP:ATT:{channel} {number(attenuation_db, 'attenuation_db')}")
        if impedance_ohm is not None:
            if impedance_ohm not in (50, 1000000) or isinstance(impedance_ohm, bool):
                raise ValueError("impedance_ohm 只能是 50 或 1000000")
            commands.append(f":INP:IMP:{channel} {impedance_ohm}")
        if gain_factor is not None:
            commands.append(f":INP:GAIN:{channel} {number(gain_factor, 'gain_factor')}")
        with self._session():
            if commands:
                self._apply(commands)
            result = {"channel": channel,
                      "attenuation_db": float(self._query(f":INP:ATT:{channel}?")),
                      "impedance_ohm": float(self._query(f":INP:IMP:{channel}?")),
                      "gain_factor": float(self._query(f":INP:GAIN:{channel}?"))}
            self._check_errors()
            return result

    def configure_result(self, result_format: ResultFormat | None = None,
                         z_parameter: ZParameter | None = None) -> dict:
        commands = []
        if z_parameter is not None:
            choice(z_parameter, ("Z", "Y", "REFL", "CP", "CS", "LP", "LS", "RP", "RS", "Q", "QTG", "D"), "z_parameter")
            commands.append(f":CALC:ZPAR:DEF {z_parameter}")
        if result_format is not None:
            choice(result_format, ("GDEL", "IMAG", "MLIN", "MLOG", "PHAS", "REAL", "UPH", "SWR", "SMIT", "SLIN", "SLOG", "SCOM", "SADM"), "result_format")
            commands.append(f":CALC:FORM {result_format}")
        with self._session():
            # 结果表示变化不改变测量数据，不让服务端的格式转换变成重新扫频。
            if not commands:
                raise ValueError("至少指定 result_format 或 z_parameter")
            for command in commands:
                self._write(command)
                self._check_errors()
            result = {"result_format": self._query(":CALC:FORM?")}
            if z_parameter is not None:
                result["z_parameter"] = self._query(":CALC:ZPAR:DEF?")
            return result

    def configure_averaging(self, enabled=None, count=None, clear=False) -> dict:
        commands = []
        if enabled is not None:
            commands.append(f":SENS:AVER:STAT {int(enabled)}")
        if count is not None:
            if isinstance(count, bool) or not isinstance(count, int):
                raise ValueError("count 必须是整数")
            commands.append(f":SENS:AVER:COUN {number(count, 'count', 2, 1000000000)}")
        if clear:
            commands.append(":SENS:AVER:CLE")
        with self._session():
            if commands:
                self._apply(commands)
            return {"enabled": self._query(":SENS:AVER:STAT?") == "1",
                    "count": int(float(self._query(":SENS:AVER:COUN?")))}

    def configure_trigger(self, source=None, continuous=None) -> dict:
        commands = []
        if source is not None:
            commands.append(f":TRIG:SOUR {choice(source, ('INT', 'BUS', 'EXT'), 'source')}")
        if continuous is not None:
            commands.append(f":INIT:CONT {int(continuous)}")
        with self._session():
            self._apply(commands)
            return {"source": self._query(":TRIG:SOUR?"),
                    "continuous": self._query(":INIT:CONT?") == "1"}

    def initiate(self) -> dict:
        with self._session():
            source = self._query(":TRIG:SOUR?").upper()
            self._changed()
            self._write(":INIT")
            self._check_errors()
            if source == "INT":
                self._pending_revision = self._revision
            return {"initiated": True, "trigger_source": source, "completion_confirmed": False}

    def trigger(self) -> dict:
        with self._session():
            self._changed()
            self._write(":TRIG:SING")
            self._check_errors()
            self._pending_revision = self._revision
            return {"trigger_sent": True, "completion_confirmed": False}

    def abort(self) -> dict:
        with self._session():
            self._changed()
            self._write(":ABOR")
            self._check_errors()
            return {"abort_sent": True, "note": "不会自动修改 continuous 设置"}

    def wait(self, timeout_seconds=30.0) -> dict:
        with self._session():
            if self._query("*OPC?", timeout_seconds) != "1":
                raise BodeError("protocol_error", "*OPC? 未返回 1")
            self._check_errors()
            if self._pending_revision == self._revision:
                self._completed_revision = self._revision
                self._pending_revision = None
            return {"operations_completed": True,
                    "measurement_confirmed": self._completed_revision == self._revision}

    def estimate_duration(self) -> dict:
        with self._session():
            result = float(self._query(":SENS:SWE:TIME?"))
            self._check_errors()
            return {"estimated_seconds": result}

    def read_measurement(self, allow_unverified=False, offset=0, limit=1000) -> dict:
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset 必须是非负整数")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10000:
            raise ValueError("limit 必须是 1–10000 的整数")
        with self._session():
            confirmed = self._completed_revision == self._revision
            if not confirmed and not allow_unverified:
                raise BodeError("unverified_results", "尚未确认当前配置下的测量完成；先触发并 wait_for_completion。读取外部或旧缓冲区需显式 allow_unverified=true")
            status = self.get_status()
            if status["sweeping"] or status["measuring"] or status["calibrating"]:
                raise BodeError("device_busy", "设备仍在测量或校准，不读取可能不完整的数据")
            if self._query(":INIT:CONT?") == "1" and self._query(":TRIG:SOUR?") != "BUS":
                raise BodeError("device_busy", "连续自动/外部触发可能在读数期间更新缓冲区；请先关闭 continuous 并完成单次测量")
            self._write(":FORM ASC")
            self._check_errors()
            measurement = self._query(":CALC:PAR:DEF?")
            result_format = self._query(":CALC:FORM?")
            frequencies = floats(self._query(":SENS:FREQ:DATA?"))
            values = floats(self._query(":CALC:DATA:SDAT?"))
            self._check_errors()
            count = len(frequencies)
            if not count or len(values) != 2 * count:
                raise BodeError("protocol_error", "频率数组与结果数组长度不匹配",
                                frequency_count=count, result_count=len(values))
            result = {
                "measurement": measurement, "result_format": result_format,
                "frequency_hz": frequencies[offset:offset + limit],
                "primary": values[:count][offset:offset + limit],
                "secondary": values[count:][offset:offset + limit],
                "total_points": count, "offset": offset,
                "returned_points": len(frequencies[offset:offset + limit]),
                "has_more": offset + limit < count,
                "configuration_match_confirmed": confirmed,
                "layout": "primary 和 secondary 各 N 个值；不是实部/虚部交错排列。含义取决于 measurement、result_format 和 z_parameter；未使用的 secondary 为 0。",
                "nonfinite_values": "设备返回的 NaN/Infinity 用 null 表示",
            }
            if measurement.upper() == "Z":
                result["z_parameter"] = self._query(":CALC:ZPAR:DEF?")
            return result

    def read_errors(self) -> dict:
        with self._session():
            return {"errors": error_entries(self._query(":SYST:ERR:ALL?")), "queue_consumed": True}

    def reset(self) -> dict:
        with self._session():
            self._apply(["*RST"])
            return {"reset_sent": True, "completion_confirmed": False}

    def raw_write(self, command: str) -> dict:
        command = validate_command(command, query=False)
        with self._session():
            self._changed()
            self._limits = None
            self._write(command)
            # 原始接口不附加错误查询：保留 *CLS/*OPC/状态寄存器等命令原本的语义。
            return {"command_sent": command, "device_acceptance_checked": False,
                    "note": "不自动等待或读取错误队列；需要时调用 read_errors / wait_for_completion"}

    def raw_query(self, command: str, timeout_seconds=None) -> dict:
        command = validate_command(command, query=True)
        with self._session():
            # 查询也可能清队列、获取/释放锁、触发自检，不能假定原始查询无副作用。
            self._changed()
            self._limits = None
            return {"command": command, "response": self._query(command, timeout_seconds)}

    def calibration_status(self, calibration_range: CalibrationRange) -> dict:
        prefix = ":SENS:CORR:" + choice(calibration_range, ("FULL", "USER"), "calibration_range")
        with self._session():
            result = {"range": calibration_range,
                      "enabled": self._query(prefix + ":ENAB?") == "1",
                      "active": self._query(prefix + ":ACT?") == "1",
                      "available": self._query(prefix + ":AVAI?") == "1"}
            self._check_errors()
            return result

    def enable_calibration(self, calibration_range: CalibrationRange, enabled: bool) -> dict:
        prefix = ":SENS:CORR:" + choice(calibration_range, ("FULL", "USER"), "calibration_range")
        with self._session():
            self._apply([f"{prefix}:ENAB {int(enabled)}"])
            return self.calibration_status(calibration_range)

    def execute_calibration(self, calibration_range: CalibrationRange, standard: Standard) -> dict:
        prefix = ":SENS:CORR:" + choice(calibration_range, ("FULL", "USER"), "calibration_range")
        token = choice(standard, ("OPEN", "SHORT", "LOAD", "THRU"), "standard")
        with self._session():
            self._apply([f"{prefix}:{token}"])
            return {"calibration_started": True, "range": calibration_range, "standard": standard,
                    "completion_confirmed": False}

    def calibration_model(self, nominal_impedance_ohm=None, load_ohm=None, short_delay_seconds=None) -> dict:
        commands = []
        if nominal_impedance_ohm is not None:
            commands.append(":SENS:CORR:IMP " + number(nominal_impedance_ohm, "nominal_impedance_ohm", 1e-12))
        if load_ohm is not None:
            commands.append(":SENS:CORR:LOAD " + number(load_ohm, "load_ohm", 1, 1000))
        if short_delay_seconds is not None:
            commands.append(":SENS:CORR:SDEL " + number(short_delay_seconds, "short_delay_seconds", 0, 500e-6))
        with self._session():
            if commands:
                self._apply(commands)
            result = {"nominal_impedance_ohm": float(self._query(":SENS:CORR:IMP?")),
                      "load_ohm": float(self._query(":SENS:CORR:LOAD?")),
                      "short_delay_seconds": float(self._query(":SENS:CORR:SDEL?"))}
            self._check_errors()
            return result

    def calibration_file(self, operation: str, path: str, calibration_range=None) -> dict:
        # 官方对无路径文件名的说明互相矛盾，只接受明确的服务端绝对路径。
        import ntpath
        import posixpath

        if not (ntpath.isabs(path) and (ntpath.splitdrive(path)[0] or path.startswith("\\\\"))
                or posixpath.isabs(path)) or not path.lower().endswith(".mcalx"):
            raise ValueError("path 必须是 SCPI Server 所在电脑上的绝对 .mcalx 路径")
        if '"' in path or "'" in path:
            raise ValueError("校准文件路径不能包含引号")
        argument = quoted(path)
        if operation == "save":
            command = f":MMEM:STOR:CORR {argument}"
        elif operation == "load":
            prefix = choice(calibration_range, ("FULL", "USER"), "calibration_range")
            command = f":MMEM:LOAD:CORR:{prefix} {argument}"
        else:
            raise ValueError("operation 必须是 save 或 load")
        with self._session():
            self._apply([command])
            return {"operation_sent": operation, "server_path": path,
                    "range": calibration_range, "completion_confirmed": False}
