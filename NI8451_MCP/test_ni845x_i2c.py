"""用假 DLL 验证参数和资源管理，不访问真实硬件。"""

import ctypes
import unittest
from unittest.mock import Mock

from ni845x_i2c import NI845xError, NI845xI2C, NiHandle, NI845X_ERROR_ADDRESS_NAK


def make_device():
    device = NI845xI2C.__new__(NI845xI2C)
    device._dll = Mock()
    for name in (
        "ni845xOpen", "ni845xClose", "ni845xCloseFindDeviceHandle",
        "ni845xI2cConfigurationOpen", "ni845xI2cConfigurationClose",
        "ni845xI2cConfigurationSetAddressSize", "ni845xI2cConfigurationSetAddress",
        "ni845xI2cConfigurationSetClockRate", "ni845xI2cScriptOpen",
        "ni845xI2cScriptClose", "ni845xI2cScriptReset", "ni845xI2cScriptClockRate",
        "ni845xI2cScriptAckPollTimeout", "ni845xI2cScriptIssueStart",
        "ni845xI2cScriptAddressWrite", "ni845xI2cScriptIssueStop", "ni845xI2cScriptRun",
    ):
        getattr(device._dll, name).return_value = 0
    device.device_handle = 123
    device.device_name = "mock-device"
    device._i2c_configs = {}
    device._status_to_string = lambda code: f"mock status {code}"
    return device


class DriverTests(unittest.TestCase):
    def test_invalid_config_does_not_call_driver(self):
        for address, size, clock in (
            (-1, 7, 100), (128, 7, 100), (1024, 10, 100), (1, 8, 100),
            (1, 7, 0), (1, 7, 65536), (1, 7, 101),
        ):
            with self.subTest(address=address, size=size, clock=clock):
                device = make_device()
                with self.assertRaises(ValueError):
                    device._get_i2c_config(address, size, clock)
                self.assertEqual(device._dll.mock_calls, [])

    def test_cached_config(self):
        device = make_device()
        device._i2c_configs[(0x50, 7, 100)] = 456
        self.assertEqual(device._get_i2c_config(0x50), 456)
        self.assertEqual(device._dll.mock_calls, [])

    def test_disconnected_transfer_does_not_call_driver(self):
        device = make_device()
        device.device_handle = None
        for operation in (lambda: device.read(0x50, 1), lambda: device.write(0x50, b"\x00"), lambda: device.write_read(0x50, b"\x00", 1)):
            with self.assertRaises(RuntimeError):
                operation()
        self.assertEqual(device._dll.mock_calls, [])

    def test_handle_width_and_prototypes(self):
        device = make_device()
        device._declare_prototypes()
        self.assertEqual(ctypes.sizeof(NiHandle), ctypes.sizeof(ctypes.c_void_p))
        self.assertEqual(device._dll.ni845xOpen.argtypes[1], ctypes.POINTER(NiHandle))
        self.assertEqual(device._dll.ni845xFindDevice.argtypes[1], ctypes.POINTER(NiHandle))
        self.assertEqual(device._dll.ni845xI2cRead.argtypes[:2], [NiHandle, NiHandle])
        self.assertIsNone(device._dll.ni845xStatusToString.restype)

    def test_find_failure_is_not_empty_list(self):
        device = make_device()
        device._dll.ni845xFindDevice.return_value = -123
        with self.assertRaises(NI845xError):
            device.find_devices()

    def test_find_handle_closed_after_next_failure(self):
        device = make_device()
        def find(name, handle, count):
            name.value = b"first"
            ctypes.cast(handle, ctypes.POINTER(NiHandle))[0] = 123
            ctypes.cast(count, ctypes.POINTER(ctypes.c_uint32))[0] = 2
            return 0
        device._dll.ni845xFindDevice.side_effect = find
        device._dll.ni845xFindDeviceNext.return_value = -123
        with self.assertRaises(NI845xError):
            device.find_devices()
        device._dll.ni845xCloseFindDeviceHandle.assert_called_once()

    def test_default_open_preserves_voltage_and_full_handle(self):
        device = make_device()
        device.device_handle = None
        handle_value = (1 << 40) + 1 if ctypes.sizeof(NiHandle) == 8 else 123
        def open_device(name, handle):
            ctypes.cast(handle, ctypes.POINTER(NiHandle))[0] = handle_value
            return 0
        device._dll.ni845xOpen.side_effect = open_device
        device.open("mock-device")
        self.assertEqual(device.device_handle, handle_value)
        device._dll.ni845xSetIoVoltageLevel.assert_not_called()
        device.close()
        device._dll.ni845xClose.assert_called_once_with(handle_value)

    def test_failed_voltage_setup_closes_device(self):
        device = make_device()
        device.device_handle = None
        device._dll.ni845xSetIoVoltageLevel.return_value = -123
        with self.assertRaises(NI845xError):
            device.open("mock-device", io_voltage=3.3)
        self.assertIsNone(device.device_handle)
        device._dll.ni845xClose.assert_called_once()

    def test_reject_5v_before_open(self):
        device = make_device()
        device.device_handle = None
        with self.assertRaises(ValueError):
            device.open("mock-device", io_voltage=5.0)
        device._dll.ni845xOpen.assert_not_called()

    def test_multiple_devices_require_name(self):
        device = make_device()
        device.device_handle = None
        device.find_devices = Mock(return_value=["first", "second"])
        with self.assertRaisesRegex(RuntimeError, "device_name"):
            device.open()
        device._dll.ni845xOpen.assert_not_called()

    def test_invalid_transfer_lengths_do_not_call_driver(self):
        device = make_device()
        for size in (-1, 0, 65537, True, 1.5):
            with self.subTest(size=size), self.assertRaises(ValueError):
                device.read(0x50, size)
        with self.assertRaises(ValueError):
            device.write(0x50, b"")
        with self.assertRaises(ValueError):
            device.write_read(0x50, b"\x00", 0)
        self.assertEqual(device._dll.mock_calls, [])

    def test_config_error_closes_uncached_handle(self):
        device = make_device()
        device._dll.ni845xI2cConfigurationSetAddress.return_value = -123
        with self.assertRaises(NI845xError):
            device._get_i2c_config(0x50)
        self.assertEqual(device._i2c_configs, {})
        device._dll.ni845xI2cConfigurationClose.assert_called_once()

    def test_read_and_write_read_output_buffers(self):
        device = make_device()
        device._i2c_configs[(0x50, 7, 100)] = 456
        def read(handle, config, requested, size, buffer):
            self.assertEqual((handle, config, requested.value), (123, 456, 3))
            ctypes.cast(size, ctypes.POINTER(ctypes.c_uint32))[0] = 2
            buffer[0], buffer[1] = 0x12, 0x34
            return 0
        def write_read(handle, config, write_size, write_buffer, requested, size, buffer):
            self.assertEqual(write_size.value, 1)
            self.assertEqual(write_buffer[0], 0xAB)
            return read(handle, config, requested, size, buffer)
        device._dll.ni845xI2cRead.side_effect = read
        device._dll.ni845xI2cWriteRead.side_effect = write_read
        self.assertEqual(device.read(0x50, 3), b"\x12\x34")
        self.assertEqual(device.write_read(0x50, b"\xab", 3), b"\x12\x34")

    def test_scan_address_only_and_nak_cleanup(self):
        device = make_device()
        state = {"address": None}
        def reset(script):
            state["address"] = None
            return 0
        def address_write(script, address):
            state["address"] = address
            return 0
        def run(script, handle, port):
            self.assertEqual((handle, port), (123, 0))
            return 0 if state["address"] in (None, 0x50) else NI845X_ERROR_ADDRESS_NAK
        device._dll.ni845xI2cScriptReset.side_effect = reset
        device._dll.ni845xI2cScriptAddressWrite.side_effect = address_write
        device._dll.ni845xI2cScriptRun.side_effect = run
        self.assertEqual(device.scan(), [0x50])
        self.assertEqual(device._dll.ni845xI2cScriptAddressWrite.call_count, 117)
        self.assertEqual(device._dll.ni845xI2cScriptRun.call_count, 233)
        device._dll.ni845xI2cWrite.assert_not_called()
        device._dll.ni845xI2cRead.assert_not_called()
        device._dll.ni845xI2cScriptClose.assert_called_once()

    def test_scan_does_not_hide_bus_error(self):
        device = make_device()
        device._dll.ni845xI2cScriptRun.side_effect = [-123, 0]
        with self.assertRaises(NI845xError) as raised:
            device.scan()
        self.assertEqual(raised.exception.status_code, -123)
        device._dll.ni845xI2cScriptClose.assert_called_once()

    def test_scan_stop_failure_is_not_hidden(self):
        device = make_device()
        device._dll.ni845xI2cScriptRun.side_effect = [NI845X_ERROR_ADDRESS_NAK, -456]
        with self.assertRaises(NI845xError) as raised:
            device.scan()
        self.assertEqual(raised.exception.status_code, -456)
        device._dll.ni845xI2cScriptClose.assert_called_once()

    def test_warning_does_not_fail_operation(self):
        device = make_device()
        with self.assertLogs("ni845x_i2c", level="WARNING"):
            device._check(1)


if __name__ == "__main__":
    unittest.main()
