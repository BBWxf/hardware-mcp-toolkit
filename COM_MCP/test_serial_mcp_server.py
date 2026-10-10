"""无硬件串口 mock 验证。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

import serial_mcp_server as server


class FakeSerial:
    """只实现本服务会使用的 pyserial 接口。"""

    def __init__(self, **kwargs: object) -> None:
        self.port = str(kwargs["port"])
        self.baudrate = int(kwargs["baudrate"])
        self.bytesize = int(kwargs["bytesize"])
        self.parity = str(kwargs["parity"])
        self.stopbits = float(kwargs["stopbits"])
        self.timeout = kwargs["timeout"]
        self.write_timeout = kwargs["write_timeout"]
        self.rtscts = bool(kwargs["rtscts"])
        self.xonxoff = bool(kwargs["xonxoff"])
        self.is_open = True
        self.rts = False
        self.dtr = False
        self.written = bytearray()
        self._incoming = bytearray()

    @property
    def in_waiting(self) -> int:
        return len(self._incoming)

    def feed(self, data: bytes) -> None:
        self._incoming.extend(data)

    def read(self, size: int) -> bytes:
        result = bytes(self._incoming[:size])
        del self._incoming[:size]
        return result

    def write(self, data: bytes) -> int:
        self.written.extend(data)
        return len(data)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        self.is_open = False


class SerialConnectionManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.serial_constructor = patch.object(server.serial, "Serial", FakeSerial)
        self.serial_constructor.start()
        self.manager = server.SerialConnectionManager()
        result = self.manager.connect("COM_MOCK", 115200, 8, "N", 1, "none")
        self.assertTrue(result["ok"])

    def tearDown(self) -> None:
        self.serial_constructor.stop()

    def test_send_and_read_hex_and_utf8(self) -> None:
        sent = self.manager.send("AA 55 0D", "hex")
        self.assertTrue(sent["ok"])
        self.assertEqual(sent["bytes_sent"], 3)
        self.assertEqual(self.manager._serial.written, b"\xaa\x55\r")

        self.manager._serial.feed("设备 OK".encode("utf-8"))
        received = self.manager.read(100, 64, "hex")
        self.assertTrue(received["ok"])
        self.assertEqual(received["hex"], "e8aebee5a487204f4b")
        self.assertTrue(received["utf8"]["ok"])
        self.assertEqual(received["utf8"]["text"], "设备 OK")

    def test_read_until_collects_all_pending_data(self) -> None:
        self.manager._serial.feed(b"first-response")
        received = self.manager.read_until(5, 100, 128, "base64")
        self.assertTrue(received["ok"])
        self.assertEqual(received["stop_reason"], "idle_timeout")
        self.assertEqual(received["hex"], b"first-response".hex())
        self.assertEqual(received["data"], "Zmlyc3QtcmVzcG9uc2U=")

    def test_reports_closed_port_and_invalid_encoding(self) -> None:
        self.assertTrue(self.manager.disconnect()["ok"])
        self.assertEqual(self.manager.read(1, 1, "hex")["error"]["code"], "serial_not_open")
        self.assertEqual(self.manager.send("payload", "ascii")["error"]["code"], "validation_error")

    def test_sets_control_lines(self) -> None:
        result = self.manager.set_control_lines(True, False)
        self.assertTrue(result["ok"])
        self.assertTrue(result["rts"])
        self.assertFalse(result["dtr"])


if __name__ == "__main__":
    unittest.main()
