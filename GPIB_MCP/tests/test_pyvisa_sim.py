from __future__ import annotations

from pathlib import Path

import pyvisa

from gpib_mcp.gpib import GpibService, IOSettings


def test_pyvisa_sim_definition() -> None:
    definition = Path(__file__).with_name("gpib_sim.yaml")
    manager = pyvisa.ResourceManager(f"{definition}@sim")
    service = GpibService(resource_manager=manager)
    try:
        assert service.scan() == ["GPIB0::5::INSTR"]
        assert service.identify("GPIB0::5::INSTR", IOSettings()).startswith(
            "ACME,MODEL-1000"
        )
        assert service.query("GPIB0::5::INSTR", "MEAS:VOLT?", IOSettings()) == "1.2345"
    finally:
        manager.close()
