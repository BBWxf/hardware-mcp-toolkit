"""IPECMD 配置、命令生成与执行；导入及读取配置均不访问硬件。"""

from __future__ import annotations

import codecs
import hashlib
import json
import locale
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Operation = Literal["program", "verify", "erase", "blank_check", "read"]
MemoryRegion = Literal["all", "P", "E", "I", "C", "A", "B"]
DEFAULT_CONFIG = Path(__file__).with_name("config.json")
OUTPUT_LIMIT = 32000


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class DfpSettings(Settings):
    name: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    version: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    vendor: str = Field(default="Microchip", pattern=r"^[A-Za-z0-9_.-]+$")


class IpeSettings(Settings):
    executable_path: str
    timeout_seconds: int = Field(default=180, ge=1, le=3600)
    output_encoding: str | None = None
    logs_directory: str = "logs"
    dfp: DfpSettings | None = None

    @field_validator("output_encoding")
    @classmethod
    def check_encoding(cls, value: str | None) -> str | None:
        if value is not None:
            codecs.lookup(value)
        return value


class ProgrammerSettings(Settings):
    type: Literal["PK4"] = "PK4"
    serial_number: str | None = None

    @field_validator("serial_number")
    @classmethod
    def check_serial(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[A-Za-z0-9]+", value):
            raise ValueError("序列号只能包含英文字母和数字")
        return value


class TargetSettings(Settings):
    device: str | None = None
    power_from_programmer: bool = True
    voltage_v: float | None = None
    release_from_reset: bool = False
    interface: Literal["JTAG", "SWD", "UPDI", "ISP", "TPI", "PDI", "HVPP", "HVSP"] | None = None
    speed_mhz: float | None = Field(default=None, gt=0, allow_inf_nan=False)

    @field_validator("device")
    @classmethod
    def check_device(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value):
            raise ValueError("芯片型号不能包含空格、路径或命令行选项")
        return value

    @field_validator("voltage_v")
    @classmethod
    def check_voltage(cls, value: float | None) -> float | None:
        if value is not None and value not in (3.3, 5.0):
            raise ValueError("本项目供电电压只允许 3.3V 或 5V")
        return value


class ProgrammingSettings(Settings):
    hex_file: str | None = None
    memory_region: MemoryRegion = "all"
    verify_after_program: bool | None = None


class ReadbackSettings(Settings):
    hex_file: str | None = None


class Configuration(Settings):
    ipe: IpeSettings
    programmer: ProgrammerSettings = Field(default_factory=ProgrammerSettings)
    target: TargetSettings = Field(default_factory=TargetSettings)
    programming: ProgrammingSettings = Field(default_factory=ProgrammingSettings)
    readback: ReadbackSettings = Field(default_factory=ReadbackSettings)


def _path(value: str, base: Path) -> Path:
    if not value or any(char in value for char in ('\x00', '\r', '\n', '"')):
        raise ValueError("路径不能为空或包含引号、换行、NUL")
    path = Path(value).expanduser()
    return (path if path.is_absolute() else base / path).resolve()


def _device_argument(device: str) -> str:
    if device.upper().startswith("DSPIC"):
        return device[5:]
    if device.upper().startswith("PIC"):
        return device[3:]
    return device


@contextmanager
def _programmer_lock(serial: str):
    """跨本机服务进程防止同一 PK4 被重复占用，不与 IPE GUI 协调。"""
    import msvcrt

    key = hashlib.sha256(serial.upper().encode("ascii")).hexdigest()[:24]
    lock_path = Path(tempfile.gettempdir()) / f"microchip-mcp-{key}.lock"
    with lock_path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"\x00")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise RuntimeError("另一 MCP 进程正在使用这台 PK4；未启动本次操作") from exc
        try:
            recovery_file = lock_path.with_suffix(".blocked.json")
            if recovery_file.exists():
                raise RuntimeError(
                    f"这台 PK4 上次操作超时，跨进程阻断仍生效。检查目标、残留进程后手动删除 {recovery_file}"
                )
            yield recovery_file
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


class Ipecmd:
    def __init__(self, config_path: Path | str = DEFAULT_CONFIG):
        self.config_path = Path(config_path).expanduser().resolve()
        self._lock = threading.Lock()
        self._blocked = False

    def load_config(self) -> Configuration:
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"无法读取配置 {self.config_path}: {exc}") from exc
        return Configuration.model_validate(data)

    def configuration(self) -> dict:
        config = self.load_config()
        return {"config_path": str(self.config_path), "configuration": config.model_dump()}

    def environment(self) -> dict:
        config = self.load_config()
        exe = _path(config.ipe.executable_path, self.config_path.parent)
        jar = exe.with_name("ipecmd.jar")
        version = None
        if jar.is_file():
            try:
                with zipfile.ZipFile(jar) as archive:
                    text = archive.read(
                        "com/microchip/mplab/ipecmd/common/ipeproperty.properties"
                    ).decode("utf-8", errors="replace")
                match = re.search(r"(?m)^\s*IPECMD\.VERSION\s*=\s*(\S+)", text)
                if match:
                    version = match.group(1)
            except (OSError, KeyError, zipfile.BadZipFile) as exc:
                return {"config_path": str(self.config_path), "error": str(exc)}
        return {
            "config_path": str(self.config_path),
            "executable_path": str(exe),
            "executable_exists": exe.is_file(),
            "jar_exists": jar.is_file(),
            "ipecmd_version": version,
            "platform_supported": os.name == "nt",
            "hardware_accessed": False,
            "blocked_after_timeout": self._blocked,
            "note": "仅检查安装文件，不代表 PK4 在线、接线正确或目标型号受支持",
        }

    def _prepare(self, operation: Operation, overrides: dict) -> tuple[Configuration, list[str], Path | None]:
        if operation not in ("program", "verify", "erase", "blank_check", "read"):
            raise ValueError("未知操作")
        data = self.load_config().model_dump()
        mapping = {
            "device": ("target", "device"),
            "serial_number": ("programmer", "serial_number"),
            "voltage_v": ("target", "voltage_v"),
            "power_from_programmer": ("target", "power_from_programmer"),
            "release_from_reset": ("target", "release_from_reset"),
            "hex_file": ("programming", "hex_file"),
            "memory_region": ("programming", "memory_region"),
            "verify_after_program": ("programming", "verify_after_program"),
            "output_file": ("readback", "hex_file"),
            "timeout_seconds": ("ipe", "timeout_seconds"),
        }
        for name, value in overrides.items():
            if name not in mapping:
                raise ValueError(f"未知覆盖参数: {name}")
            if value is not None:
                section, field = mapping[name]
                data[section][field] = value
        config = Configuration.model_validate(data)
        target = config.target
        if not target.device:
            raise ValueError("请在配置或调用参数中填写 device 芯片型号")
        device = _device_argument(target.device)
        if not device:
            raise ValueError("芯片型号缺少具体型号部分")
        if not config.programmer.serial_number:
            raise ValueError("请填写 PK4 serial_number（可从 IPE 查看），本服务不自动选择烧录器")
        if target.power_from_programmer and target.voltage_v is None:
            raise ValueError("PK4 供电时必须指定 voltage_v 为 3.3 或 5.0")

        exe = _path(config.ipe.executable_path, self.config_path.parent)
        if exe.name.lower() != "ipecmd.exe" or not exe.is_file():
            raise ValueError(f"找不到有效的 ipecmd.exe: {exe}")
        args = [str(exe), f"-P{device}", "-TPPK4", f"-TS{config.programmer.serial_number}"]
        if target.power_from_programmer:
            args.append(f"-W{target.voltage_v:g}")
        if target.release_from_reset:
            args.append("-OL")
        if target.interface is not None:
            args.append(f"-ORI{target.interface}")
        if target.speed_mhz is not None:
            args.append(f"-ORS{target.speed_mhz:g}")
        if config.ipe.dfp is not None:
            dfp = config.ipe.dfp
            args.append(f"-OWD{dfp.name},{dfp.version},{dfp.vendor}")
        region = "" if config.programming.memory_region == "all" else config.programming.memory_region
        if operation in ("program", "verify"):
            if not config.programming.hex_file:
                raise ValueError("烧录/校验必须指定 hex_file")
            firmware = _path(config.programming.hex_file, self.config_path.parent)
            if firmware.suffix.lower() != ".hex" or not firmware.is_file():
                raise ValueError(f"HEX 文件不存在或扩展名不是 .hex: {firmware}")
            args.append(f"-F{firmware}")
        output = None
        if operation == "program":
            args.append(f"-M{region}")
            if config.programming.verify_after_program is True:
                args.append(f"-Y{region}")
        elif operation == "verify":
            args.append(f"-Y{region}")
        elif operation == "erase":
            args.append("-E")
        elif operation == "blank_check":
            args.append("-C")
        else:
            if not config.readback.hex_file:
                raise ValueError("读回必须指定 output_file 或 readback.hex_file")
            output = _path(config.readback.hex_file, self.config_path.parent)
            if output.suffix.lower() != ".hex" or not output.parent.is_dir():
                raise ValueError("读回文件必须为 .hex，且其父目录必须已存在")
            args.append(f"-GF{output}")
        return config, args, output

    def preview(self, operation: Operation, **overrides) -> dict:
        config, args, output = self._prepare(operation, overrides)
        return {
            "status": "preview",
            "operation": operation,
            "command": args,
            "working_directory": str(Path(args[0]).parent),
            "timeout_seconds": config.ipe.timeout_seconds,
            "effective_configuration": config.model_dump(),
            "output_exists": output.exists() if output else None,
            "hardware_accessed": False,
            "note": "仅生成命令；读回实际先写临时文件。预览不证明器件或电压受支持。",
        }

    def list_programmers(self) -> dict:
        """执行文档化的 -T，只列工具，不指定目标器件。"""
        if os.name != "nt":
            raise RuntimeError("目前仅支持 Windows IPECMD")
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("本服务正在执行另一项操作；没有启动枚举")
        try:
            if self._blocked:
                raise RuntimeError("上次操作超时，请检查工具及残留进程后重启服务")
            config = self.load_config()
            exe = _path(config.ipe.executable_path, self.config_path.parent)
            if exe.name.lower() != "ipecmd.exe" or not exe.is_file():
                raise ValueError(f"找不到有效的 ipecmd.exe: {exe}")
            logs = _path(config.ipe.logs_directory, self.config_path.parent)
            logs.mkdir(parents=True, exist_ok=True)
            log_path = logs / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex}.json"
            with log_path.open("x", encoding="utf-8") as log:
                result = {"operation": "list_programmers", "command": [str(exe), "-T"],
                          **self._run([str(exe), "-T"], config)}
                result["status"] = (
                    "timeout" if result["timed_out"] else
                    "completed" if result["exit_code"] == 0 else "failed"
                )
                result["programmers"] = [
                    {"type": name.strip(), "serial_number": serial}
                    for name, serial in re.findall(
                        r"(?im)^[ \t]*\d+(?:\)[ \t]*|[ \t]+)(.+?)[ \t]+"
                        r"S\.No[ \t]*:[ \t]*([A-Za-z0-9]+)[ \t]*\r?$",
                        result["stdout"],
                    )
                ]
                result["log_file"] = str(log_path)
                result["note"] = "解析列表仅供参考；以原始输出及 IPE 序列号为准，不自动选择第一台"
                json.dump(result, log, ensure_ascii=False, indent=2)
            for name in ("stdout", "stderr"):
                result[name] = result[name][:OUTPUT_LIMIT]
            return result
        finally:
            self._lock.release()

    @staticmethod
    def _terminate(process: subprocess.Popen) -> None:
        # ipecmd.exe 可能启动 Java 子进程，超时不能只杀外层 EXE。
        if os.name == "nt":
            try:
                subprocess.run(
                    [str(Path(os.environ["SystemRoot"]) / "System32" / "taskkill.exe"),
                     "/PID", str(process.pid), "/T", "/F"],
                    stdin=subprocess.DEVNULL, capture_output=True, timeout=10, check=False,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
        if process.poll() is None:
            process.kill()

    def _run(self, args: list[str], config: Configuration) -> dict:
        encoding = config.ipe.output_encoding or locale.getpreferredencoding(False)
        started = time.monotonic()
        try:
            process = subprocess.Popen(
                args, cwd=Path(args[0]).parent, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except OSError as exc:
            return {"exit_code": None, "stdout": "", "stderr": f"无法启动 IPECMD: {exc}",
                    "timed_out": False, "duration_seconds": round(time.monotonic() - started, 3)}
        timed_out = False
        try:
            stdout, stderr = process.communicate(timeout=config.ipe.timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            self._blocked = True
            try:
                self._terminate(process)
            except OSError:
                pass
            try:
                stdout, stderr = process.communicate(timeout=10)
            except subprocess.TimeoutExpired as exc:
                stdout, stderr = exc.output or b"", exc.stderr or b""
                if process.stdout:
                    process.stdout.close()
                if process.stderr:
                    process.stderr.close()
        return {
            "exit_code": process.returncode,
            "stdout": stdout.decode(encoding, errors="replace"),
            "stderr": stderr.decode(encoding, errors="replace"),
            "timed_out": timed_out,
            "duration_seconds": round(time.monotonic() - started, 3),
        }

    @staticmethod
    def _status(operation: Operation, result: dict, verify_after_program: bool = False) -> str:
        if result["timed_out"]:
            return "timeout"
        if result["exit_code"] != 0:
            return "failed"
        text = result["stdout"] + "\n" + result["stderr"]
        if re.search(
            r"(?im)\b(failed|failure|unable|not found|not connected|no tools connected|"
            r"invalid serial no|not blank)\b|^\s*(?:\[.*?\]\s*)?error\b", text,
        ):
            return "failed"
        markers = {
            "program": r"Programming(?:/Verify)? complete|Program Succeeded",
            "verify": r"Verify (?:complete|Succeeded)|Verification successful",
            "erase": r"Erase (?:complete|successful|Succeeded)",
            "blank_check": r"Blank check complete, device is blank|Device is blank",
            "read": r"Read (?:complete|successfully)|Hex file exported to\s*:",
        }
        if not re.search(markers[operation], text, re.IGNORECASE):
            return "unconfirmed"
        if operation == "program" and verify_after_program:
            # Programming/Verify complete 属于 -M 的内部流程，不证明额外 -Y 已完成。
            if not re.search(r"(?im)^\s*(?:Verify (?:complete|Succeeded)|Verification successful)", text):
                return "unconfirmed"
        return "success"

    def execute(self, operation: Operation, *, overwrite: bool = False, **overrides) -> dict:
        if os.name != "nt":
            raise RuntimeError("目前仅支持 Windows IPECMD")
        if type(overwrite) is not bool:
            raise ValueError("overwrite 必须是布尔值")
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("本服务正在执行另一项操作；没有启动本次请求")
        try:
            if self._blocked:
                raise RuntimeError("上次操作超时，硬件状态未知；检查 PK4/目标及残留进程后重启服务")
            config, args, output = self._prepare(operation, overrides)
            if output and output.exists() and not overwrite:
                raise ValueError(f"输出文件已存在；需要明确设置 overwrite=true: {output}")
            logs = _path(config.ipe.logs_directory, self.config_path.parent)
            logs.mkdir(parents=True, exist_ok=True)
            log_path = logs / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex}.json"
            # 先验证日志可写，防止硬件已操作后才发现无法记录结果。
            with log_path.open("x", encoding="utf-8") as log:
                with _programmer_lock(config.programmer.serial_number) as recovery_file:
                    with tempfile.TemporaryDirectory(prefix="ipe-read-", dir=output.parent if output else logs) as temp:
                        actual_args = list(args)
                        read_file = Path(temp) / "readback.hex"
                        if output:
                            actual_args[-1] = f"-GF{read_file}"
                        result = {
                            "operation": operation,
                            "command": actual_args,
                            "config_path": str(self.config_path),
                            "effective_configuration": config.model_dump(),
                            **self._run(actual_args, config),
                        }
                        if result["timed_out"]:
                            result["recovery_file"] = str(recovery_file)
                            recovery_file.write_text(
                                json.dumps({"serial_number": config.programmer.serial_number,
                                            "log_file": str(log_path), "reason": "IPECMD timeout"}),
                                encoding="utf-8",
                            )
                        result["status"] = self._status(
                            operation, result, config.programming.verify_after_program is True,
                        )
                        if output:
                            result["output_file"] = str(output)
                            if result["status"] == "success":
                                if not read_file.is_file() or read_file.stat().st_size == 0:
                                    result["status"] = "failed"
                                    result["output_error"] = "IPECMD 未生成非空 HEX，原有输出未改动"
                                else:
                                    try:
                                        if overwrite:
                                            os.replace(read_file, output)
                                        else:
                                            os.rename(read_file, output)
                                    except OSError as exc:
                                        result["status"] = "failed"
                                        result["output_error"] = str(exc)
                        result["log_file"] = str(log_path)
                        result["hardware_state"] = "unknown; no automatic retry or recovery"
                        if result["status"] == "unconfirmed":
                            result["note"] = "退出码为 0，但未识别到操作完成标记；不能确认成功，请检查原始日志"
                        elif result["status"] == "timeout":
                            result["note"] = "操作超时，已尝试终止进程树；不代表断电/复位恢复。检查工具、目标和进程后删除 recovery_file 并重启服务"
                        json.dump(result, log, ensure_ascii=False, indent=2)
                for name in ("stdout", "stderr"):
                    if len(result[name]) > OUTPUT_LIMIT:
                        result[name] = result[name][:OUTPUT_LIMIT] + "\n[已截断；完整输出见 log_file]"
                return result
        finally:
            self._lock.release()
