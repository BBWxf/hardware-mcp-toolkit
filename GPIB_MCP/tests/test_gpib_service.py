from __future__ import annotations

import pytest

from gpib_mcp.gpib import GpibError, IOSettings


def test_scan_returns_only_sorted_gpib_resources(gpib_service) -> None:
    assert gpib_service.scan() == [
        "GPIB0::12::INSTR",
        "GPIB0::5::INSTR",
    ]


def test_identify_and_query(gpib_service) -> None:
    settings = IOSettings()

    assert (
        gpib_service.identify("GPIB0::5::INSTR", settings)
        == "ACME,MODEL-1000,SN12345,1.0"
    )
    assert gpib_service.query("GPIB0::5::INSTR", "MEAS:VOLT?", settings) == "1.2345"


def test_io_settings_are_applied_and_session_is_closed(
    gpib_service, fake_resource_manager
) -> None:
    settings = IOSettings(
        timeout_ms=12_345,
        read_termination=None,
        write_termination="\r\n",
    )

    gpib_service.write("GPIB0::5::INSTR", "VOLT 5", settings)

    instrument = fake_resource_manager.opened[-1]
    assert instrument.timeout == 12_345
    assert instrument.read_termination is None
    assert instrument.write_termination == "\r\n"
    assert instrument.closed is True


def test_query_raw_preserves_bytes(gpib_service) -> None:
    data = gpib_service.query_raw(
        "GPIB0::5::INSTR",
        "WAV:DATA?",
        IOSettings(read_termination=None),
    )

    assert data == b"\x00\x01DEMO-WAVEFORM\xff"


def test_invalid_resource_is_rejected(gpib_service) -> None:
    with pytest.raises(GpibError, match="invalid_resource"):
        gpib_service.query("TCPIP0::host::INSTR", "*IDN?", IOSettings())


def test_empty_command_is_rejected(gpib_service) -> None:
    with pytest.raises(GpibError, match="invalid_argument"):
        gpib_service.write("GPIB0::5::INSTR", "   ", IOSettings())


def test_timeout_is_mapped(gpib_service) -> None:
    with pytest.raises(GpibError) as error:
        gpib_service.query("GPIB0::5::INSTR", "TIMEOUT?", IOSettings())

    assert error.value.code == "timeout"
    assert error.value.operation == "query"


def test_resource_not_found_is_mapped(gpib_service) -> None:
    with pytest.raises(GpibError) as error:
        gpib_service.query("GPIB0::99::INSTR", "*IDN?", IOSettings())

    assert error.value.code == "resource_not_found"


def test_invalid_io_settings() -> None:
    with pytest.raises(ValueError, match="大于 0"):
        IOSettings(timeout_ms=0)
    with pytest.raises(ValueError, match="空字符串"):
        IOSettings(read_termination="")
