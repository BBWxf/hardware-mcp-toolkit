from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .gpib import GpibError, GpibService, IOSettings


class ChromaError(Exception):
    """对 MCP 调用方稳定暴露的 Chroma 仪器控制错误。"""

    def __init__(self, code: str, operation: str, message: str) -> None:
        self.code = code
        self.operation = operation
        self.message = message
        super().__init__(f"[{code}] operation={operation}: {message}")


@dataclass(frozen=True)
class ChromaDevice:
    resource: str
    idn: str
    manufacturer: str
    model: str
    serial_number: str | None
    firmware: str | None
    device_type: str | None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "resource": self.resource,
            "idn": self.idn,
            "manufacturer": self.manufacturer,
            "model": self.model,
            "serial_number": self.serial_number,
            "firmware": self.firmware,
            "device_type": self.device_type,
        }


class ChromaService:
    """根据 *IDN? 动态识别并控制受支持的 Chroma GPIB 仪器。"""

    def __init__(self, gpib: GpibService) -> None:
        self._gpib = gpib

    def discover(self, settings: IOSettings) -> dict[str, list[dict[str, str | None]]]:
        devices: list[dict[str, str | None]] = []
        errors: list[dict[str, str | None]] = []
        for resource in self._gpib.scan():
            try:
                devices.append(self._identify(resource, settings).as_dict())
            except GpibError as exc:
                errors.append({"resource": resource, "error": str(exc)})
        return {"devices": devices, "errors": errors}

    def configure_6530_output(
        self,
        *,
        resource: str | None,
        voltage_v: float | None,
        frequency_hz: float | None,
        output_enabled: bool | None,
        settings: IOSettings,
    ) -> dict[str, Any]:
        operation = "chroma_6530_configure_output"
        if voltage_v is None and frequency_hz is None and output_enabled is None:
            raise ChromaError(
                "invalid_argument",
                operation,
                "至少要提供 voltage_v、frequency_hz 或 output_enabled 中的一项",
            )
        if voltage_v is not None:
            self._validate_real_in_range(operation, "voltage_v", voltage_v, 0, 300)
        if frequency_hz is not None:
            self._validate_real_in_range(operation, "frequency_hz", frequency_hz, 15, 2_000)

        device = self._resolve("6530", resource, settings, operation)
        commands: list[str] = []
        if output_enabled is False:
            commands.append("OUTP OFF")
        if voltage_v is not None:
            # 6530 的 VOLT/RANG 是耦合命令，必须放入同一程序讯息。
            commands.extend((f"VOLT {self._format_number(voltage_v)}", "RANG AUTO"))
        if frequency_hz is not None:
            commands.append(f"FREQ {self._format_number(frequency_hz)}")
        if output_enabled is True:
            commands.append("OUTP ON")

        command = ";".join(commands)
        self._gpib.write(device.resource, command, settings)
        self._raise_if_device_error(device, settings, operation)

        return {
            "device": device.as_dict(),
            "command": command,
            "voltage_v": voltage_v,
            "frequency_hz": frequency_hz,
            "output_enabled": output_enabled,
        }

    def read_66205_input(
        self,
        *,
        resource: str | None,
        fresh: bool,
        settings: IOSettings,
    ) -> dict[str, Any]:
        operation = "chroma_66205_read_input"
        device = self._resolve("66205", resource, settings, operation)
        command = f"{'MEAS' if fresh else 'FETC'}? V,I,W,PF,THDI"
        response = self._gpib.query(device.resource, command, settings, operation=operation)
        values = self._parse_measurements(response, 5, operation)

        voltage, current, power, power_factor, ithd = values
        return {
            "device": device.as_dict(),
            "fresh": fresh,
            "command": command,
            "raw_response": response,
            "voltage_rms_v": self._measurement(voltage, "V", {-1, -2, -3}),
            "current_rms_a": self._measurement(current, "A", {-1, -2, -3}),
            "real_power_w": self._measurement(power, "W", {-1, -2, -3}),
            # PF 可为负值，不能将 -1 一概视为无效值；手册仅明确 -3 为无效。
            "power_factor": self._measurement(power_factor, None, {-3}),
            # 手册没有明确 THDI 正常值的单位语义，因此保持仪器原始数值，不换算百分比。
            "current_thd": self._measurement(ithd, "instrument_reported", {-3}),
        }

    def set_63200a_cc_current(
        self,
        *,
        resource: str | None,
        current_a: float,
        settings: IOSettings,
    ) -> dict[str, Any]:
        operation = "chroma_63200a_set_cc_current"
        self._validate_nonnegative_real(operation, "current_a", current_a)
        device = self._resolve("63200A", resource, settings, operation)
        command = f"MODE CCH;CURR:STAT:L1 {self._format_number(current_a)}"
        self._gpib.write(device.resource, command, settings)
        self._raise_if_device_error(device, settings, operation)
        return {
            "device": device.as_dict(),
            "command": command,
            "mode": "CCH",
            "current_a": current_a,
        }

    def set_63200a_cv_voltage(
        self,
        *,
        resource: str | None,
        voltage_v: float,
        settings: IOSettings,
    ) -> dict[str, Any]:
        operation = "chroma_63200a_set_cv_voltage"
        self._validate_nonnegative_real(operation, "voltage_v", voltage_v)
        device = self._resolve("63200A", resource, settings, operation)
        command = f"MODE CVH;VOLT:STAT:L1 {self._format_number(voltage_v)}"
        self._gpib.write(device.resource, command, settings)
        self._raise_if_device_error(device, settings, operation)
        return {
            "device": device.as_dict(),
            "command": command,
            "mode": "CVH",
            "voltage_v": voltage_v,
        }

    def set_63200a_load_enabled(
        self,
        *,
        resource: str | None,
        enabled: bool,
        settings: IOSettings,
    ) -> dict[str, Any]:
        operation = "chroma_63200a_set_load_enabled"
        device = self._resolve("63200A", resource, settings, operation)
        command = f"LOAD {'ON' if enabled else 'OFF'}"
        self._gpib.write(device.resource, command, settings)
        self._raise_if_device_error(device, settings, operation)
        return {
            "device": device.as_dict(),
            "command": command,
            "enabled": enabled,
        }

    def read_63200a_measurements(
        self,
        *,
        resource: str | None,
        averaged: bool,
        settings: IOSettings,
    ) -> dict[str, Any]:
        operation = "chroma_63200a_read_measurements"
        device = self._resolve("63200A", resource, settings, operation)
        prefix = "MEAS" if averaged else "FETC"
        commands = {
            "voltage_v": f"{prefix}:VOLT?",
            "current_a": f"{prefix}:CURR?",
            "power_w": f"{prefix}:POW?",
        }
        responses = {
            name: self._gpib.query(device.resource, command, settings, operation=operation)
            for name, command in commands.items()
        }
        return {
            "device": device.as_dict(),
            "averaged": averaged,
            "commands": commands,
            "voltage_v": self._parse_number(responses["voltage_v"], operation),
            "current_a": self._parse_number(responses["current_a"], operation),
            "power_w": self._parse_number(responses["power_w"], operation),
            "raw_responses": responses,
        }

    def _resolve(
        self,
        expected_type: str,
        resource: str | None,
        settings: IOSettings,
        operation: str,
    ) -> ChromaDevice:
        if resource is not None:
            device = self._identify(resource, settings)
            if device.device_type != expected_type:
                raise ChromaError(
                    "device_mismatch",
                    operation,
                    f"{resource} 识别为 {device.model or '未知型号'}，不是 {expected_type}",
                )
            return device

        inventory = self.discover(settings)
        matches = [
            ChromaDevice(
                resource=item["resource"] or "",
                idn=item["idn"] or "",
                manufacturer=item["manufacturer"] or "",
                model=item["model"] or "",
                serial_number=item["serial_number"],
                firmware=item["firmware"],
                device_type=item["device_type"],
            )
            for item in inventory["devices"]
            if item["device_type"] == expected_type
        ]
        if not matches:
            details = "; ".join(
                f"{item['resource']}={item['model'] or item['idn']}"
                for item in inventory["devices"]
            ) or "未发现可识别的 GPIB 仪器"
            raise ChromaError(
                "device_not_found",
                operation,
                f"未发现 {expected_type}；已识别设备：{details}",
            )
        if len(matches) > 1:
            resources = ", ".join(device.resource for device in matches)
            raise ChromaError(
                "ambiguous_device",
                operation,
                f"发现多个 {expected_type}：{resources}；请显式提供 resource",
            )
        return matches[0]

    def _identify(self, resource: str, settings: IOSettings) -> ChromaDevice:
        response = self._gpib.identify(resource, settings).strip()
        parts = [part.strip() for part in response.split(",")]
        manufacturer = parts[0] if parts else ""
        model = parts[1] if len(parts) > 1 else ""
        return ChromaDevice(
            resource=resource,
            idn=response,
            manufacturer=manufacturer,
            model=model,
            serial_number=parts[2] if len(parts) > 2 else None,
            firmware=parts[3] if len(parts) > 3 else None,
            device_type=self._device_type(manufacturer, model),
        )

    @staticmethod
    def _device_type(manufacturer: str, model: str) -> str | None:
        if "CHROMA" not in manufacturer.upper():
            return None
        normalized = model.upper().replace(" ", "")
        if normalized.startswith("6530"):
            return "6530"
        if normalized.startswith("66205"):
            return "66205"
        if normalized.startswith("632") and "A" in normalized:
            return "63200A"
        return None

    def _raise_if_device_error(
        self,
        device: ChromaDevice,
        settings: IOSettings,
        operation: str,
    ) -> None:
        response = self._gpib.query(
            device.resource,
            "SYST:ERR?",
            settings,
            operation=operation,
        ).strip()
        if self._is_no_error(response):
            return
        raise ChromaError("device_error", operation, f"仪器错误队列返回：{response}")

    @staticmethod
    def _is_no_error(response: str) -> bool:
        normalized = response.lower().replace('"', "").strip()
        return normalized.startswith("0") or "no error" in normalized or "无错误" in normalized

    @staticmethod
    def _validate_real_in_range(
        operation: str,
        name: str,
        value: float,
        minimum: float,
        maximum: float,
    ) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ChromaError("invalid_argument", operation, f"{name} 必须是数字")
        if not math.isfinite(value) or not minimum <= value <= maximum:
            raise ChromaError(
                "invalid_argument",
                operation,
                f"{name} 必须在 {minimum:g} 到 {maximum:g} 之间",
            )

    @classmethod
    def _validate_nonnegative_real(
        cls, operation: str, name: str, value: float
    ) -> None:
        cls._validate_real_in_range(operation, name, value, 0, math.inf)

    @staticmethod
    def _format_number(value: float) -> str:
        text = format(value, ".12f").rstrip("0").rstrip(".")
        return text or "0"

    @staticmethod
    def _parse_measurements(response: str, expected_count: int, operation: str) -> list[float]:
        values = response.strip().replace(";", ",").split(",")
        if len(values) != expected_count:
            raise ChromaError(
                "invalid_response",
                operation,
                f"预期 {expected_count} 个测量值，实际收到 {len(values)} 个：{response}",
            )
        return [ChromaService._parse_number(value, operation) for value in values]

    @staticmethod
    def _parse_number(response: str, operation: str) -> float:
        try:
            value = float(response.strip())
        except ValueError:
            raise ChromaError(
                "invalid_response", operation, f"仪器返回的不是数值：{response}"
            ) from None
        if not math.isfinite(value):
            raise ChromaError(
                "invalid_response", operation, f"仪器返回了非有限数值：{response}"
            )
        return value

    @staticmethod
    def _measurement(
        value: float, unit: str | None, invalid_values: set[int]
    ) -> dict[str, float | str | None]:
        result: dict[str, float | str | None] = {"value": value, "unit": unit}
        if value in invalid_values:
            result["status"] = {
                -1: "integration_not_complete",
                -2: "range_change_error",
                -3: "overload_or_invalid",
            }[int(value)]
        else:
            result["status"] = "ok"
        return result
