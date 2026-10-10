from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator, TypeVar

import pyvisa
from pyvisa import constants
from pyvisa.errors import InvalidSession, LibraryError, VisaIOError

T = TypeVar("T")


@dataclass(frozen=True)
class IOSettings:
    """一次仪器操作使用的 VISA I/O 参数。"""

    timeout_ms: int = 5_000
    read_termination: str | None = "\n"
    write_termination: str | None = "\n"

    def __post_init__(self) -> None:
        if isinstance(self.timeout_ms, bool) or self.timeout_ms <= 0:
            raise ValueError("timeout_ms 必须是大于 0 的整数")
        self._validate_termination("read_termination", self.read_termination)
        self._validate_termination("write_termination", self.write_termination)

    @staticmethod
    def _validate_termination(name: str, value: str | None) -> None:
        if value is not None and not isinstance(value, str):
            raise ValueError(f"{name} 必须是字符串或 null")
        if value == "":
            raise ValueError(f"{name} 不能是空字符串；请使用 null 禁用终止符")


class GpibError(Exception):
    """对 MCP 调用方稳定暴露的 GPIB/VISA 错误。"""

    def __init__(
        self,
        code: str,
        operation: str,
        resource: str | None,
        message: str,
        visa_status: str | None = None,
    ) -> None:
        self.code = code
        self.operation = operation
        self.resource = resource
        self.message = message
        self.visa_status = visa_status
        super().__init__(str(self))

    def __str__(self) -> str:
        context = f"operation={self.operation}"
        if self.resource is not None:
            context += f" resource={self.resource}"
        status = f" ({self.visa_status})" if self.visa_status else ""
        return f"[{self.code}] {context}: {self.message}{status}"


class GpibService:
    """使用一个共享 ResourceManager 串行访问 GPIB 仪器。"""

    def __init__(
        self,
        resource_manager: Any | None = None,
        resource_manager_factory: Callable[[], Any] | None = None,
    ) -> None:
        if resource_manager is not None and resource_manager_factory is not None:
            raise ValueError("resource_manager 和 resource_manager_factory 不能同时提供")

        self._resource_manager = resource_manager
        self._resource_manager_factory = resource_manager_factory or pyvisa.ResourceManager
        self._owns_resource_manager = resource_manager is None
        self._lock = threading.RLock()

    def scan(self) -> list[str]:
        def operation() -> list[str]:
            resources = self._manager("scan").list_resources()
            return sorted(
                resource
                for resource in resources
                if resource.upper().startswith("GPIB")
                and resource.upper().endswith("::INSTR")
            )

        return self._execute("scan", None, operation)

    def identify(self, resource: str, settings: IOSettings) -> str:
        return self.query(resource, "*IDN?", settings, operation="identify")

    def query(
        self,
        resource: str,
        command: str,
        settings: IOSettings,
        *,
        operation: str = "query",
    ) -> str:
        self._validate_resource(resource, operation)
        self._validate_command(command, operation, resource)

        def action() -> str:
            with self._open_instrument(resource, settings, operation) as instrument:
                return str(instrument.query(command))

        return self._execute(operation, resource, action)

    def write(self, resource: str, command: str, settings: IOSettings) -> int:
        operation = "write"
        self._validate_resource(resource, operation)
        self._validate_command(command, operation, resource)

        def action() -> int:
            with self._open_instrument(resource, settings, operation) as instrument:
                return int(instrument.write(command))

        return self._execute(operation, resource, action)

    def query_raw(
        self,
        resource: str,
        command: str,
        settings: IOSettings,
    ) -> bytes:
        operation = "query_raw"
        self._validate_resource(resource, operation)
        self._validate_command(command, operation, resource)

        def action() -> bytes:
            with self._open_instrument(resource, settings, operation) as instrument:
                instrument.write(command)
                return bytes(instrument.read_raw())

        return self._execute(operation, resource, action)

    def read_raw(self, resource: str, settings: IOSettings) -> bytes:
        operation = "read_raw"
        self._validate_resource(resource, operation)

        def action() -> bytes:
            with self._open_instrument(resource, settings, operation) as instrument:
                return bytes(instrument.read_raw())

        return self._execute(operation, resource, action)

    def close(self) -> None:
        with self._lock:
            if self._resource_manager is None or not self._owns_resource_manager:
                return
            try:
                self._resource_manager.close()
            finally:
                self._resource_manager = None

    def _manager(self, operation: str) -> Any:
        if self._resource_manager is not None:
            return self._resource_manager
        try:
            self._resource_manager = self._resource_manager_factory()
        except (LibraryError, OSError) as exc:
            raise GpibError(
                "visa_backend_error",
                operation,
                None,
                f"无法加载 VISA 后端：{exc}",
            ) from None
        return self._resource_manager

    @contextmanager
    def _open_instrument(
        self,
        resource: str,
        settings: IOSettings,
        operation: str,
    ) -> Iterator[Any]:
        instrument = self._manager(operation).open_resource(resource)
        try:
            instrument.timeout = settings.timeout_ms
            instrument.read_termination = settings.read_termination
            instrument.write_termination = settings.write_termination
            yield instrument
        finally:
            try:
                instrument.close()
            except Exception:
                # 关闭失败不应覆盖主要 I/O 异常；进程退出时 VISA 仍会释放会话。
                pass

    def _execute(
        self,
        operation: str,
        resource: str | None,
        action: Callable[[], T],
    ) -> T:
        with self._lock:
            try:
                return action()
            except GpibError:
                raise
            except VisaIOError as exc:
                raise self._map_visa_error(exc, operation, resource) from None
            except InvalidSession as exc:
                raise GpibError(
                    "session_error",
                    operation,
                    resource,
                    str(exc),
                ) from None
            except LibraryError as exc:
                raise GpibError(
                    "visa_backend_error",
                    operation,
                    resource,
                    str(exc),
                ) from None

    @staticmethod
    def _validate_resource(resource: str, operation: str) -> None:
        if not isinstance(resource, str) or not resource.strip():
            raise GpibError(
                "invalid_resource",
                operation,
                None,
                "resource 必须是非空的 VISA GPIB 资源字符串",
            )
        normalized = resource.strip().upper()
        if not normalized.startswith("GPIB") or not normalized.endswith("::INSTR"):
            raise GpibError(
                "invalid_resource",
                operation,
                resource,
                "仅支持形如 GPIB0::5::INSTR 的 GPIB INSTR 资源",
            )

    @staticmethod
    def _validate_command(command: str, operation: str, resource: str) -> None:
        if not isinstance(command, str) or not command.strip():
            raise GpibError(
                "invalid_argument",
                operation,
                resource,
                "command 必须是非空字符串",
            )

    @staticmethod
    def _map_visa_error(
        exc: VisaIOError,
        operation: str,
        resource: str | None,
    ) -> GpibError:
        status = exc.error_code
        status_text = getattr(status, "name", str(status))

        if status == constants.StatusCode.error_timeout:
            return GpibError(
                "timeout",
                operation,
                resource,
                "VISA 操作超时",
                status_text,
            )

        not_found_codes = {
            constants.StatusCode.error_resource_not_found,
            constants.StatusCode.error_invalid_resource_name,
        }
        if status in not_found_codes:
            return GpibError(
                "resource_not_found",
                operation,
                resource,
                "VISA 资源不存在或资源名称无效",
                status_text,
            )

        return GpibError(
            "visa_io_error",
            operation,
            resource,
            str(exc),
            status_text,
        )
