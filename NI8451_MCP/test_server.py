"""无硬件单元测试：.venv/Scripts/python.exe -m unittest -v。"""

import unittest
from unittest.mock import Mock, patch

import server


class ServerTests(unittest.TestCase):
    def test_hex_formats(self):
        for text in ("00 10 A5", "0010A5", "0x00,0x10,0xA5", "00-10-A5", "00\t10\nA5"):
            with self.subTest(text=text):
                self.assertEqual(server._hex_to_bytes(text), b"\x00\x10\xa5")

    def test_bad_hex(self):
        for text in ("ABC", "GG", "100 00", "-1"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    server._hex_to_bytes(text)

    def test_status_without_loading_driver(self):
        with patch.object(server, "_i2c", None):
            self.assertEqual(server.status(), {"connected": False})
            with self.assertRaisesRegex(RuntimeError, "connect"):
                server.i2c_read(0x50, 1)

    def test_failed_connect_does_not_publish_connection(self):
        device = Mock()
        device.open.side_effect = RuntimeError("open failed")
        with patch.object(server, "_i2c", None), patch.object(server, "NI845xI2C", return_value=device):
            with self.assertRaisesRegex(RuntimeError, "open failed"):
                server.connect()
            self.assertIsNone(server._i2c)

    def test_read_response(self):
        device = Mock()
        device.read.return_value = b"\x01\xa5"
        with patch.object(server, "_i2c", device):
            self.assertEqual(server.i2c_read(0x50, 2), "0x01 0xA5")
            device.read.assert_called_once_with(0x50, 2, clock_rate_khz=100, address_size=7)


if __name__ == "__main__":
    unittest.main()
