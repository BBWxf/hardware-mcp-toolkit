from __future__ import annotations

from typing import Any

import pytest
from pyvisa import constants
from pyvisa.errors import VisaIOError

from gpib_mcp.gpib import GpibService


class FakeInstrument:
    def __init__(self, resource: str) -> None:
        self.resource = resource
        self.timeout = 2_000
        self.read_termination: str | None = None
        self.write_termination: str | None = None
        self.closed = False
        self.commands: list[str] = []
        self.pending_raw = b"RAW-DATA"

    def query(self, command: str) -> str:
        self.commands.append(command)
        responses = {
            "*IDN?": "ACME,MODEL-1000,SN12345,1.0",
            "MEAS:VOLT?": "1.2345",
        }
        if command == "TIMEOUT?":
            raise VisaIOError(constants.StatusCode.error_timeout)
        return responses.get(command, f"RESPONSE:{command}")

    def write(self, command: str) -> int:
        self.commands.append(command)
        if command == "WAV:DATA?":
            self.pending_raw = b"\x00\x01DEMO-WAVEFORM\xff"
        return len(command + (self.write_termination or ""))

    def read_raw(self) -> bytes:
        return self.pending_raw

    def close(self) -> None:
        self.closed = True


class FakeResourceManager:
    def __init__(self) -> None:
        self.opened: list[FakeInstrument] = []
        self.closed = False

    def list_resources(self, query: str = "?*::INSTR") -> tuple[str, ...]:
        return (
            "ASRL1::INSTR",
            "GPIB0::12::INSTR",
            "GPIB0::5::INSTR",
        )

    def open_resource(self, resource: str) -> FakeInstrument:
        if resource not in {"GPIB0::5::INSTR", "GPIB0::12::INSTR"}:
            raise VisaIOError(constants.StatusCode.error_resource_not_found)
        instrument = FakeInstrument(resource)
        self.opened.append(instrument)
        return instrument

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_resource_manager() -> FakeResourceManager:
    return FakeResourceManager()


@pytest.fixture
def gpib_service(fake_resource_manager: FakeResourceManager) -> GpibService:
    return GpibService(resource_manager=fake_resource_manager)
