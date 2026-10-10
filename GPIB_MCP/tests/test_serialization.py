from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from gpib_mcp.gpib import GpibService, IOSettings


class BlockingInstrument:
    def __init__(self, manager) -> None:
        self.manager = manager
        self.timeout = 0
        self.read_termination = None
        self.write_termination = None

    def query(self, command: str) -> str:
        with self.manager.counter_lock:
            self.manager.active += 1
            self.manager.max_active = max(self.manager.max_active, self.manager.active)
        self.manager.first_entered.set()
        self.manager.release_first.wait(timeout=2)
        with self.manager.counter_lock:
            self.manager.active -= 1
        return command

    def close(self) -> None:
        pass


class BlockingResourceManager:
    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0
        self.counter_lock = threading.Lock()
        self.first_entered = threading.Event()
        self.release_first = threading.Event()

    def open_resource(self, resource: str) -> BlockingInstrument:
        return BlockingInstrument(self)


def test_all_visa_operations_are_serialized() -> None:
    manager = BlockingResourceManager()
    service = GpibService(resource_manager=manager)
    settings = IOSettings()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            service.query, "GPIB0::5::INSTR", "FIRST?", settings
        )
        assert manager.first_entered.wait(timeout=1)
        second = executor.submit(
            service.query, "GPIB0::12::INSTR", "SECOND?", settings
        )
        manager.release_first.set()
        assert first.result(timeout=2) == "FIRST?"
        assert second.result(timeout=2) == "SECOND?"

    assert manager.max_active == 1
