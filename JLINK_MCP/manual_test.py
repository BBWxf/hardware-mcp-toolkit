"""不经 MCP 直接验证配置读取和 J-Link 烧录后端的手动测试脚本。"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from config import JLinkMcpError, list_registered_projects, load_config, resolve_flash_request, validate_jlink_paths
from flasher import flash_firmware


def print_result(result: dict[str, Any]) -> None:
    print(json.dumps(result, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="J-Link MCP 底层逻辑手动测试")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="读取并输出已登记工程")

    flash = subparsers.add_parser("flash", help="直接调用底层烧录函数")
    flash.add_argument("--project-name", help="配置文件中的工程别名")
    flash.add_argument("--firmware-path", help="固件绝对路径，会覆盖工程默认路径")
    flash.add_argument("--device", help="目标芯片型号，会覆盖工程默认值")
    flash.add_argument("--interface", choices=("SWD", "JTAG"), help="调试接口")
    flash.add_argument("--speed", type=int, help="接口速度（kHz）")
    flash.add_argument("--base-address", help="BIN 固件的加载地址，例如 0x08000000")
    flash.add_argument("--no-erase", action="store_true", help="不在烧录前擦除全片")
    flash.add_argument("--no-reset", action="store_true", help="完成后不复位运行")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "list":
            print_result(list_registered_projects())
            return 0

        config, created = load_config()
        if created:
            print_result(
                {
                    "success": False,
                    "stage": "read_config",
                    "error": {
                        "code": "configuration_required",
                        "message": "已生成配置模板，请填写真实路径后再测试。",
                    },
                }
            )
            return 2

        exe_path, dll_path = validate_jlink_paths(config)
        request = resolve_flash_request(
            config,
            project_name=args.project_name,
            firmware_path=args.firmware_path,
            device=args.device,
            interface=args.interface,
            speed=args.speed,
            base_address=args.base_address,
            erase_before_flash=not args.no_erase,
            reset_after_flash=not args.no_reset,
        )
        print_result(flash_firmware(request, exe_path, dll_path))
        return 0
    except JLinkMcpError as exc:
        print_result(exc.to_result())
        return 2


if __name__ == "__main__":
    sys.exit(main())
