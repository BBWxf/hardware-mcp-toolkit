"""假 DLL 测试：不加载驱动，不打开卡，也不发送物理 CAN 报文。"""

import ctypes as C
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from zlgcan_device import (
    BITRATES, CAN_EFF_FLAG, CAN_ERR_FLAG, CAN_RTR_FLAG,
    CanConfig, CanFdConfig, CanFrame, ChannelConfig, ChannelError,
    ReceiveData, TransmitData, ZlgCanDevice, ZlgCanError, _pe_bits,
)


class DeviceTests(unittest.TestCase):
    def setUp(self):
        self.dll = MagicMock()
        # 大于32位的句柄值用于检查 Python 层没有主动截断；不是实际 SDK 句柄。
        self.device_handle = 0x123456780
        self.channel_handle = 0x234567890
        self.dll.ZCAN_OpenDevice.return_value = self.device_handle
        self.dll.ZCAN_CloseDevice.return_value = 1
        self.dll.ZCAN_SetValue.return_value = 1
        self.dll.ZCAN_InitCAN.return_value = self.channel_handle
        self.dll.ZCAN_StartCAN.return_value = 1
        self.dll.ZCAN_ResetCAN.return_value = 1
        self.dll.ZCAN_Transmit.return_value = 1
        self.dll.ZCAN_Receive.return_value = 0
        self.dll.ZCAN_GetReceiveNum.return_value = 0
        self.dll.ZCAN_ReadChannelErrInfo.return_value = 1
        self.can = ZlgCanDevice()
        self.can._dll = self.dll

    def open_channel(self, channel=0, mode="normal"):
        self.can.connect()
        self.can.init_channel(channel, 500000, mode)

    def test_abi_sizes_and_offsets(self):
        for kind, size in ((CanConfig, 16), (CanFdConfig, 28), (ChannelConfig, 32),
                           (CanFrame, 16), (TransmitData, 20), (ReceiveData, 24), (ChannelError, 8)):
            with self.subTest(kind=kind.__name__):
                self.assertEqual(C.sizeof(kind), size)
        self.assertEqual(ChannelConfig.config.offset, 4)
        self.assertEqual(CanConfig.mode.offset, 15)
        self.assertEqual(CanFrame.data.offset, 8)
        self.assertEqual(TransmitData.transmit_type.offset, 16)
        self.assertEqual(ReceiveData.timestamp.offset, 16)
        self.assertEqual(ReceiveData._pack_, 1)

    def test_function_prototypes(self):
        ZlgCanDevice._bind(self.dll)
        self.assertIs(self.dll.ZCAN_OpenDevice.restype, C.c_void_p)
        self.assertIs(self.dll.ZCAN_InitCAN.restype, C.c_void_p)
        self.assertEqual(self.dll.ZCAN_SetValue.argtypes, [C.c_void_p, C.c_char_p, C.c_char_p])
        self.assertIs(self.dll.ZCAN_Receive.argtypes[-1], C.c_int32)
        self.assertEqual(self.dll.ZCAN_ReadChannelErrInfo.argtypes[-1]._type_, ChannelError)

    def test_lazy_creation(self):
        with patch("zlgcan_device.C.WinDLL", create=True) as load:
            can = ZlgCanDevice()
            self.assertFalse(can.status()["connected"])
            load.assert_not_called()

    def test_pe_header_reader(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.dll"
            for machine, expected in ((0x14C, 32), (0x8664, 64)):
                data = bytearray(128)
                data[:2] = b"MZ"
                struct.pack_into("<I", data, 0x3C, 0x40)
                data[0x40:0x44] = b"PE\0\0"
                struct.pack_into("<H", data, 0x44, machine)
                path.write_bytes(data)
                self.assertEqual(_pe_bits(path), expected)
            path.write_bytes(b"not a DLL")
            with self.assertRaises(ValueError):
                _pe_bits(path)

    def test_mismatched_architecture_rejected_before_load(self):
        can = ZlgCanDevice()
        with patch.object(can, "driver_info", return_value={"dll_exists": True, "architecture_matches": False, "python_bits": 64, "dll_bits": 32}):
            with patch("zlgcan_device.C.WinDLL", create=True) as load:
                with self.assertRaisesRegex(ZlgCanError, "不能混用"):
                    can.connect()
                load.assert_not_called()

    def test_connect_no_channel_start_and_idempotent(self):
        self.can.connect(2)
        self.can.connect(2)
        self.dll.ZCAN_OpenDevice.assert_called_once_with(4, 2, 0)
        self.dll.ZCAN_InitCAN.assert_not_called()
        self.dll.ZCAN_StartCAN.assert_not_called()
        self.dll.ZCAN_Transmit.assert_not_called()
        with self.assertRaises(ZlgCanError):
            self.can.connect(1)

    def test_open_failure(self):
        self.dll.ZCAN_OpenDevice.return_value = None
        with self.assertRaises(ZlgCanError):
            self.can.connect()
        self.assertFalse(self.can.status()["connected"])

    def test_default_listen_only_config_and_call_order(self):
        self.can.connect()
        config_seen = []

        def init(handle, channel, pointer):
            config = C.cast(pointer, C.POINTER(ChannelConfig)).contents
            config_seen.append(bytes(config))
            return self.channel_handle

        self.dll.ZCAN_InitCAN.side_effect = init
        self.can.init_channel(1)
        config = ChannelConfig.from_buffer_copy(config_seen[0])
        self.assertEqual(config.can_type, 0)
        self.assertEqual(config.config.can.mode, 1)
        self.assertEqual(config.config.can.acc_mask, 0xFFFFFFFF)
        self.assertEqual(config.config.can.acc_code, 0)
        self.assertEqual(config.config.can.filter, 0)
        names = [call[0] for call in self.dll.mock_calls]
        self.assertEqual(names, ["ZCAN_OpenDevice", "ZCAN_SetValue", "ZCAN_InitCAN", "ZCAN_StartCAN"])
        self.dll.ZCAN_SetValue.assert_called_once_with(self.device_handle, b"1/baud_rate", b"500000")
        with self.assertRaises(ZlgCanError):
            self.can.send_frame(1, 0x123, "AA")
        self.dll.ZCAN_Transmit.assert_not_called()

    def test_invalid_init_arguments_do_not_call_sdk(self):
        for channel, bitrate, mode in ((2, 500000, "normal"), (0, 5000, "normal"),
                                      (0, 123456, "normal"), (0, True, "normal"), (0, 500000, "bad")):
            with self.subTest(args=(channel, bitrate, mode)), self.assertRaises(ValueError):
                self.can.init_channel(channel, bitrate, mode)
        self.assertEqual(self.dll.mock_calls, [])

    def test_all_supported_bitrates(self):
        self.can.connect()
        for bitrate in BITRATES:
            with self.subTest(bitrate=bitrate):
                self.can.init_channel(0, bitrate)
                self.can.reset_channel(0)

    def test_init_failures(self):
        self.can.connect()
        self.dll.ZCAN_SetValue.return_value = 0
        with self.assertRaises(ZlgCanError):
            self.can.init_channel(0)
        self.dll.ZCAN_InitCAN.assert_not_called()
        self.dll.ZCAN_SetValue.return_value = 1
        self.dll.ZCAN_InitCAN.return_value = None
        with self.assertRaises(ZlgCanError):
            self.can.init_channel(0)
        self.assertEqual(self.can.status()["channels"], [])
        self.dll.ZCAN_StartCAN.assert_not_called()

    def test_start_failure_cleanup(self):
        self.can.connect()
        self.dll.ZCAN_StartCAN.return_value = 0
        with self.assertRaisesRegex(ZlgCanError, "清理 ResetCAN 返回 1"):
            self.can.init_channel(0)
        self.assertEqual(self.can.status()["channels"], [])
        self.dll.ZCAN_ResetCAN.assert_called_once_with(self.channel_handle)

    def test_start_and_cleanup_failure_remains_resettable(self):
        self.can.connect()
        self.dll.ZCAN_StartCAN.return_value = 0
        self.dll.ZCAN_ResetCAN.return_value = 0
        with self.assertRaises(ZlgCanError):
            self.can.init_channel(0)
        self.assertFalse(self.can.status()["channels"][0]["started"])
        with self.assertRaises(ZlgCanError):
            self.can.send_frame(0, 1)
        self.dll.ZCAN_ResetCAN.return_value = 1
        self.can.reset_channel(0)
        self.assertEqual(self.can.status()["channels"], [])

    def test_dual_channels_and_reset(self):
        self.open_channel()
        self.can.init_channel(1, 125000)
        with self.assertRaises(ZlgCanError):
            self.can.init_channel(0)
        self.can.reset_channel(0)
        channels = self.can.status()["channels"]
        self.assertEqual([item["channel"] for item in channels], [1])
        with self.assertRaises(ZlgCanError):
            self.can.receive_frames(0)
        self.can.init_channel(0)
        self.can.disconnect()
        self.assertEqual(self.can.status()["channels"], [])
        self.can.disconnect()
        self.dll.ZCAN_CloseDevice.assert_called_once_with(self.device_handle)

    def test_failed_close_keeps_handle_for_retry(self):
        self.open_channel()
        self.dll.ZCAN_CloseDevice.return_value = 0
        with self.assertRaises(ZlgCanError):
            self.can.disconnect()
        self.assertTrue(self.can.status()["connected"])
        self.dll.ZCAN_CloseDevice.return_value = 1
        self.assertFalse(self.can.disconnect()["connected"])

    def test_failed_reset_does_not_claim_stopped(self):
        self.open_channel()
        self.dll.ZCAN_ResetCAN.return_value = 0
        with self.assertRaises(ZlgCanError):
            self.can.reset_channel(0)
        self.assertTrue(self.can.status()["channels"][0]["started"])

    def test_transmit_encoding(self):
        self.open_channel(1)
        captured = []

        def transmit(handle, pointer, count):
            self.assertEqual(handle, self.channel_handle)
            self.assertEqual(count, 1)
            captured.append(TransmitData.from_buffer_copy(bytes(C.cast(pointer, C.POINTER(TransmitData)).contents)))
            return 1

        self.dll.ZCAN_Transmit.side_effect = transmit
        self.can.send_frame(1, "0x1ABCDE", "0x01,0x02,0xA5", extended=True)
        frame = captured[-1]
        self.assertEqual(frame.frame.can_id, CAN_EFF_FLAG | 0x1ABCDE)
        self.assertEqual(frame.frame.can_dlc, 3)
        self.assertEqual(bytes(frame.frame.data), b"\x01\x02\xA5" + b"\0" * 5)
        self.assertEqual(frame.transmit_type, 1)
        self.can.send_frame(1, 0x123, remote=True, dlc=8)
        self.assertEqual(captured[-1].frame.can_id, CAN_RTR_FLAG | 0x123)
        self.assertEqual(captured[-1].frame.can_dlc, 8)
        self.assertEqual(bytes(captured[-1].frame.data), b"\0" * 8)
        self.can.send_frame(1, 0)
        self.assertEqual(captured[-1].frame.can_dlc, 0)

    def test_invalid_transmit_rejected(self):
        self.open_channel()
        invalid = [
            {"can_id": 0x800}, {"can_id": -1}, {"can_id": True}, {"can_id": "ABC"},
            {"can_id": 0x20000000, "extended": True},
            {"can_id": 1, "data_hex": "00 " * 9}, {"can_id": 1, "data_hex": "A"},
            {"can_id": 1, "data_hex": "GG"}, {"can_id": 1, "data_hex": "01", "dlc": 0},
            {"can_id": 1, "dlc": 9}, {"can_id": 1, "data_hex": "01", "remote": True},
        ]
        for arguments in invalid:
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                self.can.send_frame(0, **arguments)
        self.dll.ZCAN_Transmit.assert_not_called()

    def test_send_failure_no_retry(self):
        self.open_channel()
        self.dll.ZCAN_Transmit.return_value = 0
        with self.assertRaises(ZlgCanError):
            self.can.send_frame(0, 1)
        self.dll.ZCAN_Transmit.assert_called_once()

    def test_receive_parse_flags_and_timestamp(self):
        self.open_channel()

        def receive(handle, buffer, capacity, timeout):
            self.assertEqual((handle, capacity, timeout), (self.channel_handle, 3, 25))
            buffer[0].frame.can_id = CAN_EFF_FLAG | 0x1ABCDE
            buffer[0].frame.can_dlc = 2
            buffer[0].frame.data[:2] = (0xAA, 0x55)
            buffer[0].timestamp = 1234567890123
            buffer[1].frame.can_id = CAN_RTR_FLAG | 0x123
            buffer[1].frame.can_dlc = 8
            buffer[2].frame.can_id = CAN_ERR_FLAG | 1
            return 3

        self.dll.ZCAN_Receive.side_effect = receive
        result = self.can.receive_frames(0, 3, 25)
        self.assertEqual(result["count"], 3)
        first, remote, error = result["frames"]
        self.assertEqual(first["can_id"], 0x1ABCDE)
        self.assertTrue(first["extended"])
        self.assertEqual(first["data_hex"], "AA 55")
        self.assertEqual(first["timestamp_us"], 1234567890123)
        self.assertTrue(remote["remote"])
        self.assertEqual(remote["data_hex"], "")
        self.assertTrue(error["error"])

    def test_receive_empty_and_invalid_count(self):
        self.open_channel()
        self.assertEqual(self.can.receive_frames(0)["frames"], [])
        for count in (101, 0xFFFFFFFF):
            self.dll.ZCAN_Receive.return_value = count
            with self.subTest(count=count), self.assertRaises(ZlgCanError):
                self.can.receive_frames(0, 100)

    def test_receive_invalid_dlc(self):
        self.open_channel()

        def receive(handle, buffer, capacity, timeout):
            buffer[0].frame.can_dlc = 9
            return 1

        self.dll.ZCAN_Receive.side_effect = receive
        with self.assertRaises(ZlgCanError):
            self.can.receive_frames(0)

    def test_receive_bounds(self):
        self.open_channel()
        for maximum, timeout in ((0, 0), (1001, 0), (100, -1), (100, 10001)):
            with self.subTest(args=(maximum, timeout)), self.assertRaises(ValueError):
                self.can.receive_frames(0, maximum, timeout)
        self.dll.ZCAN_Receive.assert_not_called()

    def test_receive_count(self):
        self.open_channel()
        self.dll.ZCAN_GetReceiveNum.return_value = 42
        self.assertEqual(self.can.get_receive_count(0)["count"], 42)
        self.dll.ZCAN_GetReceiveNum.assert_called_once_with(self.channel_handle, 0)
        self.dll.ZCAN_GetReceiveNum.return_value = 0xFFFFFFFF
        with self.assertRaises(ZlgCanError):
            self.can.get_receive_count(0)

    def test_error_structure_is_read_back(self):
        self.open_channel()

        def read_error(handle, pointer):
            error = C.cast(pointer, C.POINTER(ChannelError)).contents
            error.error_code = 0x12345678
            error.passive_err_data[:] = (1, 2, 3)
            error.ar_lost_err_data = 4
            return 1

        self.dll.ZCAN_ReadChannelErrInfo.side_effect = read_error
        result = self.can.read_error(0)
        self.assertEqual(result["error_code_hex"], "0x12345678")
        self.assertEqual(result["passive_err_data"], [1, 2, 3])
        self.assertEqual(result["ar_lost_err_data"], 4)
        self.dll.ZCAN_ReadChannelErrInfo.side_effect = None
        self.dll.ZCAN_ReadChannelErrInfo.return_value = 0
        with self.assertRaises(ZlgCanError):
            self.can.read_error(0)


if __name__ == "__main__":
    unittest.main()
