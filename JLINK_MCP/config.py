"""J-Link MCP 的用户级配置读取与参数解析。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CONFIG_DIRECTORY = Path.home() / ".jlink_mcp"
CONFIG_PATH = CONFIG_DIRECTORY / "config.json"
SUPPORTED_FIRMWARE_EXTENSIONS = {".hex", ".bin", ".elf"}

CONFIG_TEMPLATE: dict[str, Any] = {
    "jlink_exe_path": r"C:\Program Files\SEGGER\JLink\JLink.exe",
    "jlink_dll_path": r"C:\Program Files\SEGGER\JLink\JLinkARM.dll",
    "projects": {
        "motor_control": {
            "firmware_path": r"D:\Projects\MotorControl\Debug\firmware.hex",
            "device": "STM32F103C8",
            "interface": "SWD",
            "speed": 4000,
        },
        "led_blink": {
            "firmware_path": r"D:\Projects\LedBlink\Debug\firmware.hex",
            "device": "STM32F407VE",
            "interface": "SWD",
            "speed": 4000,
        },
    },
}


class JLinkMcpError(Exception):
    """可安全返回给 MCP 调用方的结构化错误。"""

    def __init__(self, code: str, message: str, *, stage: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.stage = stage
        self.details = details or {}

    def to_result(self) -> dict[str, Any]:
        return {
            "success": False,
            "stage": self.stage,
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
            },
        }


@dataclass(frozen=True)
class FlashRequest:
    project_name: str | None
    firmware_path: Path
    device: str
    interface: str
    speed: int
    base_address: int | None
    jflash_project_path: Path | None
    erase_before_flash: bool
    reset_after_flash: bool


def ensure_config_file() -> bool:
    """首次使用时创建带示例的配置文件，返回本次是否新建。"""
    if CONFIG_PATH.exists():
        return False

    CONFIG_DIRECTORY.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(CONFIG_TEMPLATE, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return True


def load_config() -> tuple[dict[str, Any], bool]:
    """每次调用重新读取配置；不会缓存，便于用户修改后立即生效。"""
    created = ensure_config_file()
    try:
        raw_config = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise JLinkMcpError(
            "invalid_config_json",
            f"配置文件 JSON 格式错误：{exc.msg}（第 {exc.lineno} 行，第 {exc.colno} 列）。",
            stage="read_config",
            details={"config_path": str(CONFIG_PATH)},
        ) from exc
    except OSError as exc:
        raise JLinkMcpError(
            "config_read_failed",
            f"无法读取配置文件：{exc}",
            stage="read_config",
            details={"config_path": str(CONFIG_PATH)},
        ) from exc

    if not isinstance(raw_config, dict):
        raise JLinkMcpError(
            "invalid_config",
            "配置文件根节点必须是 JSON 对象。",
            stage="read_config",
            details={"config_path": str(CONFIG_PATH)},
        )
    if not isinstance(raw_config.get("projects", {}), dict):
        raise JLinkMcpError(
            "invalid_projects",
            "配置文件中的 projects 必须是 JSON 对象。",
            stage="read_config",
            details={"config_path": str(CONFIG_PATH)},
        )
    return raw_config, created


def list_registered_projects() -> dict[str, Any]:
    """返回配置中的项目，不检查其固件路径是否存在。"""
    config, created = load_config()
    projects = config.get("projects", {})
    return {
        "success": True,
        "config_path": str(CONFIG_PATH),
        "config_created": created,
        "configuration_required": created,
        "message": (
            f"已生成配置模板，请编辑 {CONFIG_PATH} 后填入真实 J-Link 和工程路径。"
            if created
            else None
        ),
        "projects": projects,
        "project_names": sorted(projects),
    }


def validate_jlink_paths(config: dict[str, Any]) -> tuple[Path, Path]:
    """强制两个 J-Link 文件均由用户在配置中填写且实际存在。"""
    missing: list[str] = []
    invalid: dict[str, str] = {}
    resolved: dict[str, Path] = {}

    for key in ("jlink_exe_path", "jlink_dll_path"):
        value = config.get(key)
        if not isinstance(value, str) or not value.strip():
            missing.append(key)
            continue
        candidate = Path(value)
        if not candidate.is_absolute():
            invalid[key] = "必须填写绝对路径"
        elif not candidate.is_file():
            invalid[key] = f"文件不存在：{candidate}"
        else:
            resolved[key] = candidate

    if missing or invalid:
        raise JLinkMcpError(
            "invalid_jlink_paths",
            "J-Link 可执行文件或 DLL 路径未正确配置，请编辑配置文件后重试。",
            stage="read_config",
            details={
                "config_path": str(CONFIG_PATH),
                "missing_fields": missing,
                "invalid_fields": invalid,
            },
        )

    return resolved["jlink_exe_path"], resolved["jlink_dll_path"]


def resolve_flash_request(
    config: dict[str, Any],
    *,
    project_name: str | None,
    firmware_path: str | None,
    device: str | None,
    interface: str | None,
    speed: int | None,
    base_address: str | None,
    erase_before_flash: bool,
    reset_after_flash: bool,
) -> FlashRequest:
    """合并工程别名默认值和显式参数，并执行所有与硬件无关的校验。"""
    if not project_name and not firmware_path:
        raise JLinkMcpError(
            "missing_firmware_source",
            "project_name 和 firmware_path 至少必须提供一个。",
            stage="validate_parameters",
        )

    projects = config.get("projects", {})
    project: dict[str, Any] = {}
    if project_name:
        registered = projects.get(project_name)
        if not isinstance(registered, dict):
            raise JLinkMcpError(
                "project_not_found",
                f"配置文件中找不到工程别名：{project_name}。",
                stage="validate_parameters",
                details={"available_projects": sorted(projects), "config_path": str(CONFIG_PATH)},
            )
        project = registered

    resolved_firmware = firmware_path or project.get("firmware_path")
    resolved_device = device or project.get("device")
    resolved_interface = (interface or project.get("interface") or "SWD").upper()
    resolved_speed = speed if speed is not None else project.get("speed", 4000)

    if not isinstance(resolved_firmware, str) or not resolved_firmware.strip():
        raise JLinkMcpError(
            "missing_firmware_path",
            "未提供有效 firmware_path，且工程别名中也没有 firmware_path。",
            stage="validate_parameters",
        )
    if not isinstance(resolved_device, str) or not resolved_device.strip():
        raise JLinkMcpError(
            "missing_device",
            "未提供目标芯片型号 device，且工程别名中也没有 device；不会自动猜测芯片型号。",
            stage="validate_parameters",
        )
    if resolved_interface not in {"SWD", "JTAG"}:
        raise JLinkMcpError(
            "invalid_interface",
            "interface 仅支持 SWD 或 JTAG。",
            stage="validate_parameters",
            details={"interface": resolved_interface},
        )
    if isinstance(resolved_speed, bool) or not isinstance(resolved_speed, int) or resolved_speed <= 0:
        raise JLinkMcpError(
            "invalid_speed",
            "speed 必须是大于 0 的整数，单位为 kHz。",
            stage="validate_parameters",
            details={"speed": resolved_speed},
        )

    firmware = Path(resolved_firmware)
    if not firmware.is_absolute():
        raise JLinkMcpError(
            "firmware_path_not_absolute",
            "firmware_path 必须使用绝对路径。",
            stage="validate_firmware",
            details={"firmware_path": str(firmware)},
        )
    if not firmware.is_file():
        raise JLinkMcpError(
            "firmware_not_found",
            f"固件文件不存在：{firmware}",
            stage="validate_firmware",
            details={"firmware_path": str(firmware)},
        )
    if firmware.suffix.lower() not in SUPPORTED_FIRMWARE_EXTENSIONS:
        raise JLinkMcpError(
            "unsupported_firmware_format",
            "仅支持 .hex、.bin、.elf 格式的固件文件。",
            stage="validate_firmware",
            details={"firmware_path": str(firmware), "extension": firmware.suffix},
        )

    resolved_jflash_project: Path | None = None
    jflash_project_value = project.get("jflash_project_path")
    if jflash_project_value is not None:
        if not isinstance(jflash_project_value, str) or not jflash_project_value.strip():
            raise JLinkMcpError(
                "invalid_jflash_project_path",
                "jflash_project_path 必须是有效的 .jflash 工程绝对路径。",
                stage="validate_parameters",
            )
        candidate = Path(jflash_project_value)
        if not candidate.is_absolute() or not candidate.is_file() or candidate.suffix.lower() != ".jflash":
            raise JLinkMcpError(
                "invalid_jflash_project_path",
                "jflash_project_path 必须指向存在的 .jflash 工程文件。",
                stage="validate_parameters",
                details={"jflash_project_path": str(candidate)},
            )
        resolved_jflash_project = candidate

    resolved_address: int | None = None
    if firmware.suffix.lower() == ".bin":
        if not base_address:
            raise JLinkMcpError(
                "missing_base_address",
                ".bin 固件必须提供 base_address，例如 0x08000000。",
                stage="validate_parameters",
            )
        try:
            resolved_address = int(base_address, 0)
        except (TypeError, ValueError) as exc:
            raise JLinkMcpError(
                "invalid_base_address",
                "base_address 必须是十六进制字符串，例如 0x08000000。",
                stage="validate_parameters",
                details={"base_address": base_address},
            ) from exc
        if resolved_address < 0:
            raise JLinkMcpError(
                "invalid_base_address",
                "base_address 不能为负数。",
                stage="validate_parameters",
                details={"base_address": base_address},
            )

    return FlashRequest(
        project_name=project_name,
        firmware_path=firmware,
        device=resolved_device.strip(),
        interface=resolved_interface,
        speed=resolved_speed,
        base_address=resolved_address,
        jflash_project_path=resolved_jflash_project,
        erase_before_flash=erase_before_flash,
        reset_after_flash=reset_after_flash,
    )
