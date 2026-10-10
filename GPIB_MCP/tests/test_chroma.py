from __future__ import annotations

import asyncio

import pytest
from mcp import Client

from gpib_mcp.chroma import ChromaError, ChromaService
from gpib_mcp.gpib import GpibService, IOSettings
from gpib_mcp.server import create_server


class ChromaInstrument:
    def __init__(self, manager: ChromaResourceManager, resource: str) -> None:
        self.manager = manager
        self.resource = resource
        self.timeout = 0
        self.read_termination: str | None = None
        self.write_termination: str | None = None

    def query(self, command: str) -> str:
        self.manager.commands.append((self.resource, command))
        responses = {
            "GPIB0::5::INSTR": {
                "*IDN?": "Chroma ATE,6530,65300001,2.01",
                "SYST:ERR?": "无错误",
            },
            "GPIB0::12::INSTR": {
                "*IDN?": "Chroma ATE,66205,66205A000066,1.00,1.00,1.00",
                "MEAS? V,I,W,PF,THDI": "230,1.5,345,0.95,3.25",
                "FETC? V,I,W,PF,THDI": "229.8,1.4,321.2,0.94,3.1",
            },
            "GPIB0::8::INSTR": {
                "*IDN?": "Chroma,63205A-150-500,63205A000001,1.00,1.00,1.00",
                "SYST:ERR?": self.manager.load_error_response,
                "FETC:VOLT?": "8.12",
                "FETC:CURR?": "3.15",
                "FETC:POW?": "25.6",
                "MEAS:VOLT?": "8.00",
                "MEAS:CURR?": "3.00",
                "MEAS:POW?": "24.0",
            },
        }
        return responses[self.resource].get(command, f"RESPONSE:{command}")

    def write(self, command: str) -> int:
        self.manager.commands.append((self.resource, command))
        return len(command)

    def close(self) -> None:
        pass


class ChromaResourceManager:
    def __init__(self) -> None:
        self.commands: list[tuple[str, str]] = []
        self.load_error_response = '0,"No Error"'

    def list_resources(self) -> tuple[str, ...]:
        return (
            "GPIB0::12::INSTR",
            "GPIB0::5::INSTR",
            "GPIB0::8::INSTR",
        )

    def open_resource(self, resource: str) -> ChromaInstrument:
        return ChromaInstrument(self, resource)


@pytest.fixture
def chroma_manager() -> ChromaResourceManager:
    return ChromaResourceManager()


@pytest.fixture
def chroma_service(chroma_manager: ChromaResourceManager) -> ChromaService:
    return ChromaService(GpibService(resource_manager=chroma_manager))


def test_discover_identifies_supported_chroma_devices(chroma_service: ChromaService) -> None:
    inventory = chroma_service.discover(IOSettings())

    assert inventory["errors"] == []
    assert [(item["resource"], item["device_type"]) for item in inventory["devices"]] == [
        ("GPIB0::12::INSTR", "66205"),
        ("GPIB0::5::INSTR", "6530"),
        ("GPIB0::8::INSTR", "63200A"),
    ]


def test_66205_tool_runs_through_mcp(chroma_manager: ChromaResourceManager) -> None:
    async def run() -> dict:
        service = GpibService(resource_manager=chroma_manager)
        async with Client(create_server(service)) as client:
            result = await client.call_tool(
                "chroma_66205_read_input",
                {"resource": "GPIB0::12::INSTR", "fresh": False},
            )
            assert result.is_error is False
            return result.structured_content

    result = asyncio.run(run())

    assert result["fresh"] is False
    assert result["raw_response"] == "229.8,1.4,321.2,0.94,3.1"
    assert result["real_power_w"]["value"] == 321.2


def test_6530_configures_coupled_voltage_range_and_output(
    chroma_service: ChromaService, chroma_manager: ChromaResourceManager
) -> None:
    result = chroma_service.configure_6530_output(
        resource=None,
        voltage_v=230,
        frequency_hz=50,
        output_enabled=True,
        settings=IOSettings(),
    )

    assert result["device"]["resource"] == "GPIB0::5::INSTR"
    assert result["command"] == "VOLT 230;RANG AUTO;FREQ 50;OUTP ON"
    assert chroma_manager.commands[-2:] == [
        ("GPIB0::5::INSTR", "VOLT 230;RANG AUTO;FREQ 50;OUTP ON"),
        ("GPIB0::5::INSTR", "SYST:ERR?"),
    ]


def test_6530_validates_specified_limits(chroma_service: ChromaService) -> None:
    with pytest.raises(ChromaError, match="0 到 300"):
        chroma_service.configure_6530_output(
            resource="GPIB0::5::INSTR",
            voltage_v=301,
            frequency_hz=None,
            output_enabled=None,
            settings=IOSettings(),
        )


def test_explicit_resource_must_match_expected_device(chroma_service: ChromaService) -> None:
    with pytest.raises(ChromaError, match="device_mismatch"):
        chroma_service.read_66205_input(
            resource="GPIB0::5::INSTR", fresh=True, settings=IOSettings()
        )


def test_66205_returns_named_batch_measurements(chroma_service: ChromaService) -> None:
    result = chroma_service.read_66205_input(
        resource=None, fresh=True, settings=IOSettings()
    )

    assert result["command"] == "MEAS? V,I,W,PF,THDI"
    assert result["voltage_rms_v"] == {"value": 230.0, "unit": "V", "status": "ok"}
    assert result["current_rms_a"] == {"value": 1.5, "unit": "A", "status": "ok"}
    assert result["real_power_w"] == {"value": 345.0, "unit": "W", "status": "ok"}
    assert result["power_factor"]["value"] == 0.95
    assert result["current_thd"] == {
        "value": 3.25,
        "unit": "instrument_reported",
        "status": "ok",
    }


def test_63200a_sets_cch_without_enabling_load(
    chroma_service: ChromaService, chroma_manager: ChromaResourceManager
) -> None:
    result = chroma_service.set_63200a_cc_current(
        resource="GPIB0::8::INSTR", current_a=10, settings=IOSettings()
    )

    assert result["mode"] == "CCH"
    assert result["command"] == "MODE CCH;CURR:STAT:L1 10"
    assert ("GPIB0::8::INSTR", "LOAD ON") not in chroma_manager.commands
    assert ("GPIB0::8::INSTR", "LOAD OFF") not in chroma_manager.commands


def test_63200a_reads_instantaneous_measurements(chroma_service: ChromaService) -> None:
    result = chroma_service.read_63200a_measurements(
        resource="GPIB0::8::INSTR", averaged=False, settings=IOSettings()
    )

    assert result["voltage_v"] == 8.12
    assert result["current_a"] == 3.15
    assert result["power_w"] == 25.6
    assert result["commands"] == {
        "voltage_v": "FETC:VOLT?",
        "current_a": "FETC:CURR?",
        "power_w": "FETC:POW?",
    }


def test_63200a_device_error_never_disables_load(
    chroma_service: ChromaService, chroma_manager: ChromaResourceManager
) -> None:
    chroma_manager.load_error_response = '2,"Data Range Error"'

    with pytest.raises(ChromaError, match="device_error"):
        chroma_service.set_63200a_cv_voltage(
            resource="GPIB0::8::INSTR", voltage_v=24, settings=IOSettings()
        )

    assert ("GPIB0::8::INSTR", "LOAD OFF") not in chroma_manager.commands
