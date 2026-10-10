"""通过 PyLink 或 J-Link Commander 执行固件烧录。"""

from __future__ import annotations

import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from config import FlashRequest, JLinkMcpError

# Commander 出现这些文本时，即使进程返回码为 0 也不应当报告成功。
COMMANDER_ERROR_PATTERN = re.compile(
    r"(?:\bERROR\b|\bFAILED\b|Could not|Cannot connect|Unknown device|No J-Link|"
    r"Cannot find a J-Link|Cannot open|Target.*not.*connected)",
    re.IGNORECASE,
)


class FlashBackendError(Exception):
    """后端执行错误，保存发生阶段，以便转换为 MCP 的结构化结果。"""

    def __init__(self, stage: str, message: str, *, raw_error: str | None = None):
        super().__init__(message)
        self.stage = stage
        self.message = message
        self.raw_error = raw_error or message


def _classify_backend_error(stage: str, raw_error: str) -> FlashBackendError:
    """把 J-Link 的常见底层错误转为面向用户的明确诊断。

    Commander 的正常日志也会包含 "Emulator" 和 "USB"，因此这里只匹配明确的
    “未找到探针”错误，不能因为成功识别到了探针反而误报为未检测到探针。
    """
    lowered = raw_error.lower()
    if "winerror 193" in lowered or "not a valid win32 application" in lowered:
        message = "JLinkARM.dll 与当前 Python 进程架构不匹配，请使用相同位数的 Python 与 DLL。"
        stage = "initialize"
    elif any(
        token in lowered
        for token in (
            "no j-link found",
            "no jlink found",
            "no emulators connected",
            "cannot find a j-link",
            "could not find a j-link",
            "failed to open usb",
        )
    ):
        message = "未检测到 J-Link 探针。请检查 USB 连接、J-Link 驱动和探针供电。"
    elif any(token in lowered for token in ("unknown device", "invalid device", "device name")):
        message = "目标芯片型号不受支持或名称拼写错误，请检查 device 配置。"
    elif any(
        token in lowered
        for token in ("cannot connect", "failed to connect", "could not connect", "cannot identify", "target not")
    ):
        message = "无法连接目标芯片。请检查目标板供电、SWD/JTAG 接线、接口类型和速度。"
    elif stage == "verify" or any(
        token in lowered for token in ("verification failed", "verify failed", "compare failed", "data mismatch")
    ):
        message = "烧录后的校验失败，目标存储器内容与固件不一致。"
        stage = "verify"
    elif stage == "flash":
        message = "J-Link 已连接且擦除完成，但写入 Flash 时失败。请检查芯片读写保护、固件目标地址和 Flash 编程设置。"
    elif stage == "erase":
        message = "J-Link 已连接目标芯片，但擦除 Flash 时失败。"
    else:
        message = f"J-Link 在“{stage}”阶段执行失败。"
    return FlashBackendError(stage, message, raw_error=raw_error)


def _infer_commander_stage(output: str) -> str:
    """从 Commander 的失败文本优先还原失败阶段，而非匹配成功耗时统计。"""
    lowered = output.lower()
    if any(token in lowered for token in ("verification failed", "verify failed", "compare failed", "data mismatch")):
        return "verify"
    if any(token in lowered for token in ("programming failed", "error while programming", "downloading file")):
        return "flash"
    if any(token in lowered for token in ("erase failed", "error while erasing")):
        return "erase"
    if any(token in lowered for token in ("cannot connect", "failed to connect", "could not connect")):
        return "connect"
    return "connect"


def _progress_callback(actions: list[str]) -> Callable[[str, str, int], None]:
    """记录 PyLink 进度事件，供完成后的 verify 状态判断使用。"""

    def callback(action: str, _progress: str, _percentage: int) -> None:
        actions.append(str(action))

    return callback


def _jflash_executable_path(jlink_exe_path: Path) -> Path:
    """J-Flash 与 J-Link Commander 安装在同一 SEGGER 目录。"""
    return jlink_exe_path.with_name("JFlash.exe")


def _jflash_chip_name(device: str) -> str:
    """将 J-Link Commander 的 STM32 设备名转换为 J-Flash 工程名。"""
    if device.upper().startswith("STM32"):
        return f"ST {device}"
    return device


def _build_jflash_project(request: FlashRequest) -> str:
    """生成与 JFlash Lite 相同连接参数的临时 J-Flash 工程。"""
    target_interface = 1 if request.interface == "SWD" else 0
    chip_name = _jflash_chip_name(request.device)
    return f'''  AppVersion = 72001
  FileVersion = 2
[GENERAL]
  ConnectMode = 0
  CurrentFile = ""
  DataFileSAddr = 0x00000000
  GUIMode = 0
  HostName = ""
  TargetIF = {target_interface}
  USBPort = 0
  USBSerialNo = 0x00000000
[JTAG]
  IRLen = 0
  MultipleTargets = 0
  NumDevices = 0
  Speed0 = {request.speed}
  Speed1 = {request.speed}
  TAP_Number = 0
  UseAdaptive0 = 0
  UseAdaptive1 = 0
  UseMaxSpeed0 = 0
  UseMaxSpeed1 = 0
[CPU]
  NumInitSteps = 1
  InitStep0_Action = "Reset"
  InitStep0_Value0 = 0x00000000
  InitStep0_Value1 = 0x00000000
  InitStep0_Comment = "Reset and halt target"
  NumExitSteps = 0
  UseScriptFile = 0
  ScriptFile = ""
  UseRAM = 1
  RAMAddr = 0x20000000
  RAMSize = 0x00020000
  CheckCoreID = 1
  CoreID = 0x2BA01477
  CoreIDMask = 0x0F000FFF
  UseAutoSpeed = 0
  ClockSpeed = 0x00000000
  EndianMode = 0
  ChipName = "{chip_name}"
[FLASH]
  AutoDetect = 1
  BaseAddr = 0x08000000
  NumBanks = 1
  OrgNumBits = 16
  OrgNumChips = 1
[PRODUCTION]
  AutoPerformsDisconnect = 0
  AutoPerformsErase = {int(request.erase_before_flash)}
  AutoPerformsProgram = 1
  AutoPerformsSecure = 0
  AutoPerformsStartApp = {int(request.reset_after_flash)}
  AutoPerformsUnsecure = 0
  AutoPerformsVerify = 1
  EnableFixedVTref = 0
  EnableTargetPower = 0
  EraseType = 1
  FixedVTref = 0x00000CE4
  MonitorVTref = 0
  MonitorVTrefMax = 0x0000157C
  MonitorVTrefMin = 0x000003E8
  OverrideTimeouts = 0
  ProgramSN = 0
  SerialFile = ""
  SNAddr = 0x00000000
  SNInc = 0x00000001
  SNLen = 0x00000004
  SNListFile = ""
  SNValue = 0x00000001
  StartAppType = 0
  TargetPowerDelay = 0x00000014
  TimeoutErase = 0x00003A98
  TimeoutProgram = 0x00002710
  TimeoutVerify = 0x00002710
  VerifyType = 1
[PERFORMANCE]
  DisableSkipBlankDataOnProgram = 0x00000000
  PerfromBlankCheckPriorEraseChip = 0x00000001
  PerfromBlankCheckPriorEraseSelectedSectors = 0x00000001
'''


def flash_with_pylink(request: FlashRequest, dll_path: Path) -> dict[str, Any]:
    """DLL 后备路径：直接使用 JLinkARM.dll，避免解析命令行输出。

    PyLink 的 flash_file 通过 DLL 执行下载，其进度回调会返回 Verify 阶段；
    因此仅在收到 Verify 事件时才将本次操作报告为已校验。
    """
    try:
        import pylink  # pylink-square 提供的包名
        from pylink import enums
        from pylink.library import Library
    except ImportError as exc:
        raise FlashBackendError(
            "initialize",
            "未安装 pylink-square，无法使用 DLL 烧录后端。",
            raw_error=str(exc),
        ) from exc

    jlink: Any | None = None
    stage = "initialize"
    actions: list[str] = []
    try:
        # 明确传入配置文件中的 DLL，不扫描系统目录也不依赖 PATH。
        library = Library(dllpath=str(dll_path))
        library.load()
        jlink = pylink.JLink(lib=library)

        stage = "connect"
        jlink.open()
        interface_value = enums.JLinkInterfaces.SWD if request.interface == "SWD" else enums.JLinkInterfaces.JTAG
        jlink.set_tif(interface_value)
        jlink.connect(request.device, speed=request.speed)

        # 与 J-Flash Lite 的下载流程对齐：在 Flash 操作前先通过正常 SYSRESETREQ
        # 复位目标并保持 halt，避免连接时遗留的 HardFault 状态影响 Flash 算法。
        stage = "reset"
        jlink.reset(halt=True)

        stage = "halt"
        if not jlink.halt():
            raise RuntimeError("目标芯片未能进入 halt 状态")

        if request.erase_before_flash:
            stage = "erase"
            jlink.erase()

            # 擦除后重新连接探针和目标，避免复用擦除阶段的 DLL 会话。
            jlink.close()
            jlink = pylink.JLink(lib=library)
            stage = "connect"
            jlink.open()
            jlink.set_tif(interface_value)
            jlink.connect(request.device, speed=request.speed)

        stage = "flash"
        # 对 HEX/ELF，地址由文件内容决定；PyLink 仍要求传入 addr，使用 0 即可。
        address = request.base_address if request.base_address is not None else 0
        jlink.flash_file(str(request.firmware_path), address, on_progress=_progress_callback(actions))

        stage = "verify"
        if not any(action.lower() == "verify" for action in actions):
            raise RuntimeError("PyLink 未报告 Verify 阶段，无法确认烧录校验结果")

        if request.reset_after_flash:
            stage = "reset"
            jlink.reset(halt=False)

        return {
            "backend": "pylink-square",
            "verification": "passed",
            "pylink_actions": actions,
        }
    except FlashBackendError:
        raise
    except Exception as exc:  # PyLink 将 DLL/探针/目标错误封装为不同异常类型。
        raise _classify_backend_error(stage, str(exc)) from exc
    finally:
        # 无论前面在哪个阶段失败，都应释放与探针、DLL 的连接。
        if jlink is not None:
            try:
                jlink.close()
            except Exception:
                pass


def flash_with_jflash(request: FlashRequest, jflash_path: Path) -> dict[str, Any]:
    """使用临时 J-Flash 工程执行擦除、写入和校验。

    J-Flash 支持通过工程配置进行无人值守 ``-auto`` 调用；配置了工程时优先使用
    此后端，以保持目标芯片、接口和 Flash 设置一致。
    """
    if not jflash_path.is_file():
        raise FlashBackendError("initialize", f"未找到 JFlash.exe：{jflash_path}")

    stage = "initialize"
    try:
        with tempfile.TemporaryDirectory(prefix="jlink_mcp_jflash_") as directory:
            working_directory = Path(directory)
            jflash_log = working_directory / "jflash.log"
            jlink_log = working_directory / "jlink.log"
            if request.jflash_project_path is not None:
                project_file = request.jflash_project_path
                project_source = "configured"
            else:
                project_file = working_directory / "flash.jflash"
                project_file.write_text(_build_jflash_project(request), encoding="utf-8")
                project_source = "generated"

            data_argument = str(request.firmware_path)
            if request.base_address is not None:
                data_argument = f"{data_argument},0x{request.base_address:08X}"

            stage = "connect"
            completed = subprocess.run(
                [
                    str(jflash_path),
                    f"-jflashlog{jflash_log}",
                    f"-jlinklog{jlink_log}",
                    f"-openprj{project_file}",
                    f"-open{data_argument}",
                    "-auto",
                    "-exit",
                    "-hide",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
                check=False,
            )
            log_parts = [completed.stdout or "", completed.stderr or ""]
            for log_file in (jflash_log, jlink_log):
                if log_file.is_file():
                    log_parts.append(log_file.read_text(encoding="utf-8", errors="replace"))
            output = "\n".join(part for part in log_parts if part).strip()
            stage = _infer_commander_stage(output)
            if completed.returncode != 0:
                raise RuntimeError(f"JFlash.exe 返回码 {completed.returncode}：\n{output}")
            if COMMANDER_ERROR_PATTERN.search(output):
                raise RuntimeError(output)

            return {
                "backend": "jflash",
                "verification": "passed",
                "jflash_output": output[-4000:],
                "jflash_project": project_source,
            }
    except subprocess.TimeoutExpired as exc:
        raise FlashBackendError(
            stage,
            "J-Flash 在 180 秒内未完成，已终止执行。",
            raw_error=str(exc),
        ) from exc
    except FlashBackendError:
        raise
    except Exception as exc:
        raise _classify_backend_error(stage, str(exc)) from exc


def _quote_commander_path(path: Path) -> str:
    """避免用户配置中的路径破坏临时 CommanderScript 语法。"""
    path_text = str(path)
    if any(character in path_text for character in ('"', "\r", "\n")):
        raise FlashBackendError("initialize", "文件路径包含 J-Link CommanderScript 不支持的字符。")
    return f'"{path_text}"'


def _build_commander_erase_script(request: FlashRequest) -> str:
    """生成独立擦除会话，严格对齐 J-Flash Lite 的第一阶段。"""
    lines = [
        f"si {request.interface}",
        f"speed {request.speed}",
        f"device {request.device}",
        "connect",
        "r",
        "h",
        "erase",
        "qc",
    ]
    return "\n".join(lines) + "\n"


def _build_commander_program_script(request: FlashRequest) -> str:
    """生成独立下载会话，避免复用擦除后的 J-Link Flash 状态。"""
    firmware = _quote_commander_path(request.firmware_path)
    lines = [
        f"si {request.interface}",
        f"speed {request.speed}",
        f"device {request.device}",
        "connect",
    ]
    if request.base_address is None:
        lines.append(f"loadfile {firmware}")
    else:
        address = f"0x{request.base_address:08X}"
        lines.append(f"loadfile {firmware}, {address}")
        lines.append(f"verifybin {firmware}, {address}")
    if request.reset_after_flash:
        lines.extend(("r", "g"))
    lines.append("qc")
    return "\n".join(lines) + "\n"


def _run_commander_script(exe_path: Path, script_content: str) -> str:
    """在新的 Commander 进程中运行一个阶段，并返回完整日志。"""
    script_file: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".jlink", prefix="jlink_mcp_", delete=False
        ) as file:
            file.write(script_content)
            script_file = Path(file.name)
        completed = subprocess.run(
            [str(exe_path), "-CommanderScript", str(script_file)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            check=False,
        )
        output = (completed.stdout or "") + ("\n" + completed.stderr if completed.stderr else "")
        if completed.returncode != 0:
            raise RuntimeError(f"JLink.exe 返回码 {completed.returncode}：\n{output.strip()}")
        if COMMANDER_ERROR_PATTERN.search(output):
            raise RuntimeError(output.strip())
        return output
    finally:
        if script_file is not None:
            try:
                script_file.unlink(missing_ok=True)
            except OSError:
                pass


def flash_with_commander(request: FlashRequest, exe_path: Path) -> dict[str, Any]:
    """用独立的擦除和下载会话复现 J-Flash Lite 的两段式流程。"""
    stage = "initialize"
    outputs: list[str] = []
    try:
        if request.erase_before_flash:
            stage = "erase"
            outputs.append("[erase]\n" + _run_commander_script(exe_path, _build_commander_erase_script(request)))

        stage = "flash"
        outputs.append("[program]\n" + _run_commander_script(exe_path, _build_commander_program_script(request)))
        return {
            "backend": "jlink-commander",
            "verification": "passed",
            "commander_output": "\n".join(outputs).strip()[-4000:],
        }
    except subprocess.TimeoutExpired as exc:
        raise FlashBackendError(
            stage,
            "J-Link Commander 在 180 秒内未完成，已终止执行。",
            raw_error=str(exc),
        ) from exc
    except FlashBackendError:
        raise
    except Exception as exc:
        raw_error = "\n".join(outputs + [str(exc)]).strip()
        raise _classify_backend_error(stage, raw_error) from exc


def _backend_error_details(error: FlashBackendError) -> dict[str, str]:
    """提取后端失败信息，保留供 MCP 调用方排障。"""
    return {"stage": error.stage, "message": error.message, "raw_error": error.raw_error}


def flash_firmware(request: FlashRequest, exe_path: Path, dll_path: Path) -> dict[str, Any]:
    """配置了工程时先尝试 J-Flash，再尝试两段式 Commander 和 PyLink。

    未配置 J-Flash 工程时直接使用 Commander；PyLink 是最后的 DLL 后备路径。
    """
    start = time.monotonic()
    failures: dict[str, dict[str, str]] = {}
    backends: list[tuple[str, Callable[[], dict[str, Any]]]] = []
    if request.jflash_project_path is not None:
        backends.append(("jflash", lambda: flash_with_jflash(request, _jflash_executable_path(exe_path))))
    backends.extend(
        (
            ("jlink-commander", lambda: flash_with_commander(request, exe_path)),
            ("pylink-square", lambda: flash_with_pylink(request, dll_path)),
        )
    )

    result: dict[str, Any] | None = None
    last_error: FlashBackendError | None = None
    for backend_name, execute in backends:
        try:
            result = execute()
            result["fallback_used"] = bool(failures)
            break
        except FlashBackendError as exc:
            last_error = exc
            failures[backend_name] = _backend_error_details(exc)

    if result is None:
        if last_error is None:
            raise JLinkMcpError("flash_failed", "没有可用的 J-Link 烧录后端。", stage="initialize")
        raise JLinkMcpError(
            "flash_failed",
            last_error.message,
            stage=last_error.stage,
            details={"raw_error": last_error.raw_error, "backend_failures": failures},
        )

    if failures:
        result["backend_failures"] = failures
    result.update(
        {
            "success": True,
            "duration_seconds": round(time.monotonic() - start, 3),
            "bytes_written": request.firmware_path.stat().st_size,
            "target_device": request.device,
            "project_name": request.project_name,
            "firmware_path": str(request.firmware_path),
            "interface": request.interface,
            "speed_khz": request.speed,
            "erase_before_flash": request.erase_before_flash,
            "reset_after_flash": request.reset_after_flash,
        }
    )
    return result
