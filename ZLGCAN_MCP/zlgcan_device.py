"""根据 zlgcan/CANzlg_Test2/zlgcan.cs 封装 USB-CAN2 的经典 CAN 接口。

主函数为 stdcall；保留 CANFD 初始化联合体仅为了匹配 32 字节 ABI，不提供 CAN FD 通讯。
"""

from __future__ import annotations

import ctypes as C
import os
import struct
import sys
from pathlib import Path
from threading import RLock

ZCAN_USBCAN2 = 4
STATUS_OK = 1
CAN_EFF_FLAG = 0x80000000
CAN_RTR_FLAG = 0x40000000
CAN_ERR_FLAG = 0x20000000
CAN_ID_MASK = 0x1FFFFFFF
BITRATES = (10000, 20000, 50000, 100000, 125000, 250000, 500000, 800000, 1000000)
DEFAULT_DLL = Path(__file__).resolve().parent / "zlgcan/CANzlg_Test2/bin/x86/Debug/zlgcan.dll"


class CanConfig(C.Structure):
    _fields_ = [
        ("acc_code", C.c_uint32), ("acc_mask", C.c_uint32), ("reserved", C.c_uint32),
        ("filter", C.c_uint8), ("timing0", C.c_uint8), ("timing1", C.c_uint8), ("mode", C.c_uint8),
    ]


class CanFdConfig(C.Structure):
    _fields_ = [
        ("acc_code", C.c_uint32), ("acc_mask", C.c_uint32),
        ("abit_timing", C.c_uint32), ("dbit_timing", C.c_uint32), ("brp", C.c_uint32),
        ("filter", C.c_uint8), ("mode", C.c_uint8), ("pad", C.c_uint16), ("reserved", C.c_uint32),
    ]


class ConfigUnion(C.Union):
    _fields_ = [("can", CanConfig), ("canfd", CanFdConfig)]


class ChannelConfig(C.Structure):
    _fields_ = [("can_type", C.c_uint32), ("config", ConfigUnion)]


class CanFrame(C.Structure):
    _pack_ = 1
    _fields_ = [
        ("can_id", C.c_uint32), ("can_dlc", C.c_uint8),
        ("pad", C.c_uint8), ("res0", C.c_uint8), ("res1", C.c_uint8),
        ("data", C.c_uint8 * 8),
    ]


class TransmitData(C.Structure):
    _fields_ = [("frame", CanFrame), ("transmit_type", C.c_uint32)]


class ReceiveData(C.Structure):
    _pack_ = 1
    _fields_ = [("frame", CanFrame), ("timestamp", C.c_uint64)]


class ChannelError(C.Structure):
    _fields_ = [
        ("error_code", C.c_uint32), ("passive_err_data", C.c_uint8 * 3),
        ("ar_lost_err_data", C.c_uint8),
    ]


class ZlgCanError(RuntimeError):
    pass


def _integer(name: str, value: int, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} 必须是 {minimum}~{maximum} 的整数。")
    return value


def _pe_bits(path: Path) -> int:
    """只读取 PE 文件头，不执行 DLL。"""
    with path.open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise ValueError(f"不是 Windows PE 文件：{path}")
        stream.seek(0x3C)
        offset_bytes = stream.read(4)
        if len(offset_bytes) != 4:
            raise ValueError("DLL 的 DOS 文件头不完整。")
        stream.seek(struct.unpack("<I", offset_bytes)[0])
        if stream.read(4) != b"PE\0\0":
            raise ValueError("DLL 的 PE 签名不正确。")
        machine_bytes = stream.read(2)
        if len(machine_bytes) != 2:
            raise ValueError("DLL 的 PE 文件头不完整。")
        machine = struct.unpack("<H", machine_bytes)[0]
    if machine not in (0x14C, 0x8664):
        raise ValueError(f"不支持的 DLL Machine：0x{machine:04X}")
    return 32 if machine == 0x14C else 64


def _parse_data(data_hex: str) -> bytes:
    if not isinstance(data_hex, str):
        raise ValueError("data_hex 必须是十六进制字符串。")
    normalized = data_hex.replace("0x", "").replace("0X", "").replace(",", " ").strip()
    try:
        data = bytes.fromhex(normalized)
    except ValueError as exc:
        raise ValueError("data_hex 必须由完整的两位十六进制字节组成，例如 '01 02 A5'。") from exc
    if len(data) > 8:
        raise ValueError("经典 CAN 每帧最多 8 字节，不会自动截断或分包。")
    return data


class ZlgCanDevice:
    """一个服务进程持有一张卡；所有原生调用串行，不开启后台收包线程。"""

    def __init__(self, dll_path: str | Path | None = None):
        configured = dll_path or os.getenv("ZLGCAN_DLL_PATH") or DEFAULT_DLL
        self.dll_path = Path(configured).expanduser().resolve()
        self._dll = None
        self._dll_directories = []
        self._device_handle = None
        self._device_index = None
        self._channels: dict[int, dict] = {}
        self._lock = RLock()

    def driver_info(self) -> dict:
        exists = self.dll_path.is_file()
        bits = _pe_bits(self.dll_path) if exists else None
        python_bits = C.sizeof(C.c_void_p) * 8
        return {
            "platform": sys.platform,
            "python_bits": python_bits,
            "dll_path": str(self.dll_path),
            "dll_exists": exists,
            "dll_bits": bits,
            "architecture_matches": bits == python_bits if exists else None,
            "device_type": ZCAN_USBCAN2,
            "channel_indices": [0, 1],
            "bitrates_bps": list(BITRATES),
        }

    def _load_sdk(self) -> None:
        if self._dll is not None:
            return
        if sys.platform != "win32":
            raise ZlgCanError("此 SDK 只能在 Windows 上使用。")
        info = self.driver_info()
        if not info["dll_exists"]:
            raise ZlgCanError(f"找不到 zlgcan.dll：{self.dll_path}；可用 ZLGCAN_DLL_PATH 指定完整路径。")
        if not info["architecture_matches"]:
            raise ZlgCanError(
                f"Python 为 {info['python_bits']} 位，DLL 为 {info['dll_bits']} 位，不能混用。"
                "附带示例需要 32 位 Python，或更换整套匹配位数的厂家 SDK。"
            )
        sdk_dir = self.dll_path.parent
        # 此进程专用于 SDK；旧版插件和属性文件使用 kerneldlls 相对目录。
        # 使用绝对脚本/DLL 路径，避免 MCP 客户端启动目录影响厂商资源查找。
        for directory in (sdk_dir, sdk_dir / "kerneldlls"):
            if directory.is_dir():
                self._dll_directories.append(os.add_dll_directory(str(directory)))
        os.chdir(sdk_dir)
        try:
            dll = C.WinDLL(str(self.dll_path))
            self._bind(dll)
        except (OSError, AttributeError) as exc:
            raise ZlgCanError(
                f"加载 ZLG SDK 失败：{exc}。请保留完整 kerneldlls 目录，安装 USB-CAN 驱动，"
                "并检查 VC++ 2005 SP1/2008 x86 运行库。"
            ) from exc
        self._dll = dll

    @staticmethod
    def _bind(dll) -> None:
        handle, u32 = C.c_void_p, C.c_uint32
        signatures = {
            "ZCAN_OpenDevice": ([u32, u32, u32], handle),
            "ZCAN_CloseDevice": ([handle], u32),
            "ZCAN_SetValue": ([handle, C.c_char_p, C.c_char_p], u32),
            "ZCAN_InitCAN": ([handle, u32, C.POINTER(ChannelConfig)], handle),
            "ZCAN_StartCAN": ([handle], u32),
            "ZCAN_ResetCAN": ([handle], u32),
            "ZCAN_Transmit": ([handle, C.POINTER(TransmitData), u32], u32),
            "ZCAN_GetReceiveNum": ([handle, C.c_uint8], u32),
            "ZCAN_Receive": ([handle, C.POINTER(ReceiveData), u32, C.c_int32], u32),
            "ZCAN_ReadChannelErrInfo": ([handle, C.POINTER(ChannelError)], u32),
        }
        for name, (argtypes, restype) in signatures.items():
            function = getattr(dll, name)
            function.argtypes = argtypes
            function.restype = restype

    @staticmethod
    def _check(operation: str, result: int) -> None:
        if result != STATUS_OK:
            raise ZlgCanError(f"{operation} 失败，SDK 返回 {result}；未自动重试或重新连接。")

    def _require_device(self) -> None:
        if self._device_handle is None:
            raise ZlgCanError("尚未连接 USB-CAN2，请先 connect。")

    def _require_channel(self, channel: int, started: bool = True) -> dict:
        _integer("channel", channel, 0, 1)
        self._require_device()
        state = self._channels.get(channel)
        if state is None or (started and not state["started"]):
            raise ZlgCanError(f"通道 {channel} 未启动，请先 init_channel。")
        return state

    def status(self) -> dict:
        with self._lock:
            return {
                "connected": self._device_handle is not None,
                "device_type": ZCAN_USBCAN2,
                "device_index": self._device_index,
                "channels": [
                    {"channel": channel, **{key: value for key, value in state.items() if key != "handle"}}
                    for channel, state in sorted(self._channels.items())
                ],
            }

    def connect(self, device_index: int = 0) -> dict:
        _integer("device_index", device_index, 0, 31)
        with self._lock:
            if self._device_handle is not None:
                if device_index != self._device_index:
                    raise ZlgCanError("已有设备连接，切换卡前必须先 disconnect。")
                return self.status()
            self._load_sdk()
            handle = self._dll.ZCAN_OpenDevice(ZCAN_USBCAN2, device_index, 0)
            if not handle:
                raise ZlgCanError(
                    f"ZCAN_OpenDevice(4, {device_index}, 0) 返回空句柄。"
                    "请检查设备型号、索引、USB 驱动及是否被其他程序占用；不能据此区分具体原因。"
                )
            self._device_handle = handle
            self._device_index = device_index
            return self.status()

    def disconnect(self) -> dict:
        with self._lock:
            if self._device_handle is not None:
                self._check("ZCAN_CloseDevice", self._dll.ZCAN_CloseDevice(self._device_handle))
                self._device_handle = None
                self._device_index = None
                self._channels.clear()
            return self.status()

    def init_channel(self, channel: int, bitrate: int = 500000, mode: str = "listen_only") -> dict:
        _integer("channel", channel, 0, 1)
        if type(bitrate) is not int or bitrate not in BITRATES:
            raise ValueError(f"bitrate 必须为以下 bit/s 档位之一：{BITRATES}")
        if mode not in ("normal", "listen_only"):
            raise ValueError("mode 必须是 normal 或 listen_only。")
        with self._lock:
            self._require_device()
            if channel in self._channels:
                raise ZlgCanError(f"通道 {channel} 已初始化，请先 reset_channel 再改配置。")
            self._check(
                "ZCAN_SetValue(baud_rate)",
                self._dll.ZCAN_SetValue(self._device_handle, f"{channel}/baud_rate".encode("ascii"), str(bitrate).encode("ascii")),
            )
            config = ChannelConfig()
            config.can_type = 0
            config.config.can.acc_mask = 0xFFFFFFFF
            config.config.can.mode = 1 if mode == "listen_only" else 0
            handle = self._dll.ZCAN_InitCAN(self._device_handle, channel, C.byref(config))
            if not handle:
                raise ZlgCanError(f"ZCAN_InitCAN 通道 {channel} 返回空句柄。")
            self._channels[channel] = {"handle": handle, "started": False, "bitrate": bitrate, "mode": mode}
            result = self._dll.ZCAN_StartCAN(handle)
            if result != STATUS_OK:
                cleanup = self._dll.ZCAN_ResetCAN(handle)
                if cleanup == STATUS_OK:
                    del self._channels[channel]
                raise ZlgCanError(
                    f"ZCAN_StartCAN 返回 {result}，清理 ResetCAN 返回 {cleanup}；"
                    "未自动重试。清理失败时请 reset_channel 或 disconnect。"
                )
            self._channels[channel]["started"] = True
            return self.status()

    def reset_channel(self, channel: int) -> dict:
        with self._lock:
            state = self._require_channel(channel, started=False)
            self._check("ZCAN_ResetCAN", self._dll.ZCAN_ResetCAN(state["handle"]))
            del self._channels[channel]
            return self.status()

    def send_frame(
        self, channel: int, can_id: int | str, data_hex: str = "",
        extended: bool = False, remote: bool = False, dlc: int | None = None,
    ) -> dict:
        if type(extended) is not bool or type(remote) is not bool:
            raise ValueError("extended 和 remote 必须是布尔值。")
        if isinstance(can_id, str):
            try:
                can_id = int(can_id, 16 if can_id.lower().startswith("0x") else 10)
            except ValueError as exc:
                raise ValueError("can_id 必须是十进制整数或带 0x 前缀的十六进制字符串。") from exc
        _integer("can_id", can_id, 0, CAN_ID_MASK if extended else 0x7FF)
        data = _parse_data(data_hex)
        if dlc is None:
            dlc = len(data)
        _integer("dlc", dlc, 0, 8)
        if remote and data:
            raise ValueError("远程帧不能携带 data_hex；用 dlc 指定请求的数据长度。")
        if not remote and dlc != len(data):
            raise ValueError("数据帧的 dlc 必须等于 data_hex 的实际字节数。")
        tx = TransmitData()
        tx.frame.can_id = can_id | (CAN_EFF_FLAG if extended else 0) | (CAN_RTR_FLAG if remote else 0)
        tx.frame.can_dlc = dlc
        tx.frame.data[:len(data)] = data
        tx.transmit_type = 1  # 单次发送，避免无 ACK 时由本服务请求自动重发。
        with self._lock:
            state = self._require_channel(channel)
            if state["mode"] != "normal":
                raise ZlgCanError("只听通道不能发送；请先 reset_channel，再以 normal 模式 init_channel。")
            sent = self._dll.ZCAN_Transmit(state["handle"], C.byref(tx), 1)
            if sent != 1:
                raise ZlgCanError(f"ZCAN_Transmit 请求 1 帧，SDK 返回 {sent}；未自动重发。")
            return {"channel": channel, "sent": sent, "can_id": can_id, "can_id_hex": f"0x{can_id:X}", "dlc": dlc, "transmit_type": 1}

    def receive_frames(self, channel: int, max_frames: int = 100, timeout_ms: int = 0) -> dict:
        _integer("max_frames", max_frames, 1, 1000)
        _integer("timeout_ms", timeout_ms, 0, 10000)
        with self._lock:
            state = self._require_channel(channel)
            buffer = (ReceiveData * max_frames)()
            count = self._dll.ZCAN_Receive(state["handle"], buffer, max_frames, timeout_ms)
            if not 0 <= count <= max_frames:
                raise ZlgCanError(f"ZCAN_Receive 返回异常计数 {count}（容量 {max_frames}），未解析缓冲区。")
            frames = []
            for item in buffer[:count]:
                frame = item.frame
                if frame.can_dlc > 8:
                    raise ZlgCanError(f"SDK 返回非法经典 CAN DLC={frame.can_dlc}；本次已读取的报文已消费。")
                remote = bool(frame.can_id & CAN_RTR_FLAG)
                can_id = frame.can_id & CAN_ID_MASK
                frames.append({
                    "can_id": can_id, "can_id_hex": f"0x{can_id:X}", "raw_can_id": frame.can_id,
                    "extended": bool(frame.can_id & CAN_EFF_FLAG), "remote": remote,
                    "error": bool(frame.can_id & CAN_ERR_FLAG), "dlc": frame.can_dlc,
                    "data_hex": "" if remote else bytes(frame.data[:frame.can_dlc]).hex(" ").upper(),
                    "timestamp_us": item.timestamp,
                })
            return {"channel": channel, "count": count, "frames": frames}

    def get_receive_count(self, channel: int) -> dict:
        with self._lock:
            state = self._require_channel(channel)
            count = self._dll.ZCAN_GetReceiveNum(state["handle"], 0)
            if count == 0xFFFFFFFF:
                raise ZlgCanError("ZCAN_GetReceiveNum 返回异常计数 0xFFFFFFFF，不能视为有效缓冲区长度。")
            return {"channel": channel, "count": count}

    def read_error(self, channel: int) -> dict:
        with self._lock:
            state = self._require_channel(channel)
            error = ChannelError()
            self._check("ZCAN_ReadChannelErrInfo", self._dll.ZCAN_ReadChannelErrInfo(state["handle"], C.byref(error)))
            return {
                "channel": channel, "error_code": error.error_code,
                "error_code_hex": f"0x{error.error_code:08X}",
                "passive_err_data": list(error.passive_err_data), "ar_lost_err_data": error.ar_lost_err_data,
            }
