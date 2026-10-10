"""J-Link 固件烧录 MCP Server（stdio transport）。"""

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from config import (
    CONFIG_PATH,
    JLinkMcpError,
    ensure_config_file,
    list_registered_projects,
    load_config,
    resolve_flash_request,
    validate_jlink_paths,
)
from flasher import flash_firmware as execute_flash

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
LOGGER = logging.getLogger("jlink_mcp")

# 服务启动即生成配置模板；日志输出到 stderr，不会破坏 MCP 的 stdout 协议流。
CONFIG_CREATED_ON_STARTUP = ensure_config_file()
if CONFIG_CREATED_ON_STARTUP:
    LOGGER.warning("已生成 J-Link MCP 配置模板，请编辑：%s", CONFIG_PATH)

mcp = FastMCP("J-Link Firmware Flash")


def _consume_first_config_creation() -> bool:
    """将首次创建信息附带到首个工具返回值，之后仍允许热更新配置。"""
    global CONFIG_CREATED_ON_STARTUP
    created = CONFIG_CREATED_ON_STARTUP
    CONFIG_CREATED_ON_STARTUP = False
    return created


@mcp.tool()
def list_projects() -> dict[str, Any]:
    """列出 J-Link MCP 配置文件中登记的所有工程别名及完整配置。"""
    try:
        result = list_registered_projects()
        if _consume_first_config_creation():
            result.update(
                {
                    "config_created": True,
                    "configuration_required": True,
                    "message": f"已生成配置模板，请编辑 {CONFIG_PATH} 后填入真实 J-Link 和工程路径。",
                }
            )
        return result
    except JLinkMcpError as exc:
        return exc.to_result()
    except Exception as exc:
        LOGGER.exception("列出工程时发生未预期错误")
        return JLinkMcpError(
            "internal_error", "读取工程配置时发生未预期错误。", stage="read_config", details={"raw_error": str(exc)}
        ).to_result()


@mcp.tool()
def flash_firmware(
    project_name: str | None = None,
    firmware_path: str | None = None,
    device: str | None = None,
    interface: str | None = None,
    speed: int | None = None,
    base_address: str | None = None,
    erase_before_flash: bool = True,
    reset_after_flash: bool = True,
) -> dict[str, Any]:
    """烧录 HEX、BIN 或 ELF 固件到 J-Link 连接的目标芯片。

    project_name 指向用户级配置文件中的工程别名。若同时传 firmware_path、device、
    interface 或 speed，它们会覆盖该工程的对应默认值。BIN 必须提供 base_address。
    """
    try:
        config, config_created = load_config()
        if config_created or _consume_first_config_creation():
            return JLinkMcpError(
                "configuration_required",
                "已生成配置模板。请填写真实 J-Link 路径和工程配置后再烧录。",
                stage="read_config",
                details={"config_path": str(CONFIG_PATH)},
            ).to_result()

        # 按需求先验证 J-Link 路径，再解析实际烧录参数与固件。
        exe_path, dll_path = validate_jlink_paths(config)
        request = resolve_flash_request(
            config,
            project_name=project_name,
            firmware_path=firmware_path,
            device=device,
            interface=interface,
            speed=speed,
            base_address=base_address,
            erase_before_flash=erase_before_flash,
            reset_after_flash=reset_after_flash,
        )
        return execute_flash(request, exe_path, dll_path)
    except JLinkMcpError as exc:
        return exc.to_result()
    except Exception as exc:
        LOGGER.exception("烧录过程中发生未预期错误")
        return JLinkMcpError(
            "internal_error", "烧录过程中发生未预期错误。", stage="internal", details={"raw_error": str(exc)}
        ).to_result()


if __name__ == "__main__":
    mcp.run(transport="stdio")
