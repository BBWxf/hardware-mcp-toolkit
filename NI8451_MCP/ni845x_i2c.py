"""
ni845x_i2c.py
-------------
NI USB-8451 / USB-8452 的最小化 I2C ctypes 封装。

只封装了 I2C 相关的函数（设备查找/打开/关闭、I2C 配置、读/写/写后读、
IO 电平设置、错误码转字符串）。SPI 和数字 IO 部分未实现。

运行前提（必须在 Windows 上）：
1. 已安装 NI-845x 驱动（NI 官网下载，安装后会自带 Ni845x.dll，
   通常会被安装到系统可搜索到的路径，一般无需手动指定路径）。
2. USB-8451 已通过 USB 物理连接到本机，并能在 NI MAX
   （Measurement & Automation Explorer）里看到该设备。

函数原型来自 NI-845x Hardware and Software Manual 中公开的 C API 文档。
如果调用时报 "找不到函数" 或参数不匹配的错误，请对照你本机安装的
NI-845x SDK 里的 Ni845x.h 头文件核实一遍函数签名（不同驱动版本可能有
细微差异）。
"""

from __future__ import annotations

import ctypes
import logging
import platform
from typing import Optional


class NI845xError(Exception):
    """封装了 NI-845x 返回的错误码和对应的可读错误信息。"""

    def __init__(self, status_code: int, message: str = ""):
        self.status_code = status_code
        self.message = message or f"NI-845x error code {status_code}"
        super().__init__(f"[{status_code}] {self.message}")


# I2C 地址位数
NI845X_7BIT_ADDRESSING = 0
NI845X_10BIT_ADDRESSING = 1

# 本机 NI-845x 16.0 ni845x.h：NiHandle 在 Win32/Win64 分别为 32/64 位。
NiHandle = ctypes.c_size_t

# 可编程参考电压是 USB-8452 功能；USB-8451 I2C 使用外部上拉。
_IO_VOLTAGE_LEVELS = {1.2: 12, 1.5: 15, 1.8: 18, 2.5: 25, 3.3: 33}
NI845X_ERROR_ADDRESS_NAK = -301742

# USB-8451 支持的 I2C 时钟（单位：kHz）。
_SUPPORTED_CLOCK_RATES_KHZ = {32, 40, 50, 64, 80, 100, 125, 160, 200, 250}

# 服务单次传输上限（不是 NI 硬件规格），避免错误参数造成巨量内存分配。
_MAX_TRANSFER_SIZE = 65536

_MAX_DEVICE_NAME_SIZE = 256
_MAX_ERROR_STRING_SIZE = 1024


class NI845xI2C:
    """对单个 USB-8451/8452 设备的 I2C 主机接口封装。"""

    def __init__(self):
        if platform.system() != "Windows":
            raise RuntimeError(
                "NI-845x 驱动只提供 Windows 版本的 Ni845x.dll，"
                "这段代码必须运行在装有 NI-845x 驱动的 Windows 主机上。"
            )
        try:
            self._dll = ctypes.WinDLL("Ni845x.dll")
        except OSError as exc:
            raise RuntimeError(
                f"无法加载 Ni845x.dll（Python {ctypes.sizeof(NiHandle) * 8} 位）。"
                "请安装/修复对应位数的 NI-845x 驱动。"
            ) from exc
        self._declare_prototypes()

        self.device_handle: Optional[int] = None
        self.device_name: Optional[str] = None
        # (address, address_size, clock_rate_khz) -> configuration handle
        self._i2c_configs: dict[tuple[int, int, int], int] = {}

    # ---------- ctypes 函数签名声明 ----------
    def _declare_prototypes(self):
        d = self._dll

        d.ni845xFindDevice.argtypes = [
            ctypes.c_char_p, ctypes.POINTER(NiHandle), ctypes.POINTER(ctypes.c_uint32)
        ]
        d.ni845xFindDevice.restype = ctypes.c_int32

        d.ni845xFindDeviceNext.argtypes = [NiHandle, ctypes.c_char_p]
        d.ni845xFindDeviceNext.restype = ctypes.c_int32

        d.ni845xCloseFindDeviceHandle.argtypes = [NiHandle]
        d.ni845xCloseFindDeviceHandle.restype = ctypes.c_int32

        d.ni845xOpen.argtypes = [ctypes.c_char_p, ctypes.POINTER(NiHandle)]
        d.ni845xOpen.restype = ctypes.c_int32

        d.ni845xClose.argtypes = [NiHandle]
        d.ni845xClose.restype = ctypes.c_int32

        d.ni845xI2cConfigurationOpen.argtypes = [ctypes.POINTER(NiHandle)]
        d.ni845xI2cConfigurationOpen.restype = ctypes.c_int32

        d.ni845xI2cConfigurationClose.argtypes = [NiHandle]
        d.ni845xI2cConfigurationClose.restype = ctypes.c_int32

        d.ni845xI2cConfigurationSetAddress.argtypes = [NiHandle, ctypes.c_uint16]
        d.ni845xI2cConfigurationSetAddress.restype = ctypes.c_int32

        d.ni845xI2cConfigurationSetAddressSize.argtypes = [NiHandle, ctypes.c_int32]
        d.ni845xI2cConfigurationSetAddressSize.restype = ctypes.c_int32

        d.ni845xI2cConfigurationSetClockRate.argtypes = [NiHandle, ctypes.c_uint16]
        d.ni845xI2cConfigurationSetClockRate.restype = ctypes.c_int32

        d.ni845xI2cWrite.argtypes = [
            NiHandle, NiHandle, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint8)
        ]
        d.ni845xI2cWrite.restype = ctypes.c_int32

        d.ni845xI2cRead.argtypes = [
            NiHandle, NiHandle, ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint8)
        ]
        d.ni845xI2cRead.restype = ctypes.c_int32

        d.ni845xI2cWriteRead.argtypes = [
            NiHandle, NiHandle,
            ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint8)
        ]
        d.ni845xI2cWriteRead.restype = ctypes.c_int32

        d.ni845xSetIoVoltageLevel.argtypes = [NiHandle, ctypes.c_uint8]
        d.ni845xSetIoVoltageLevel.restype = ctypes.c_int32

        d.ni845xStatusToString.argtypes = [ctypes.c_int32, ctypes.c_uint32, ctypes.c_char_p]
        d.ni845xStatusToString.restype = None

        d.ni845xI2cScriptOpen.argtypes = [ctypes.POINTER(NiHandle)]
        d.ni845xI2cScriptOpen.restype = ctypes.c_int32
        for name in ("ni845xI2cScriptClose", "ni845xI2cScriptReset",
                     "ni845xI2cScriptIssueStart", "ni845xI2cScriptIssueStop"):
            function = getattr(d, name)
            function.argtypes = [NiHandle]
            function.restype = ctypes.c_int32
        d.ni845xI2cScriptRun.argtypes = [NiHandle, NiHandle, ctypes.c_uint8]
        d.ni845xI2cScriptRun.restype = ctypes.c_int32
        d.ni845xI2cScriptAddressWrite.argtypes = [NiHandle, ctypes.c_uint8]
        d.ni845xI2cScriptAddressWrite.restype = ctypes.c_int32
        d.ni845xI2cScriptClockRate.argtypes = [NiHandle, ctypes.c_uint16]
        d.ni845xI2cScriptClockRate.restype = ctypes.c_int32
        d.ni845xI2cScriptAckPollTimeout.argtypes = [NiHandle, ctypes.c_uint16]
        d.ni845xI2cScriptAckPollTimeout.restype = ctypes.c_int32

    # ---------- 内部工具 ----------
    def _status_to_string(self, status_code: int) -> str:
        buf = ctypes.create_string_buffer(_MAX_ERROR_STRING_SIZE)
        self._dll.ni845xStatusToString(status_code, _MAX_ERROR_STRING_SIZE, buf)
        try:
            return buf.value.decode(errors="replace")
        except Exception:
            return str(status_code)

    def _check(self, status_code: int):
        if status_code < 0:
            raise NI845xError(status_code, self._status_to_string(status_code))
        if status_code > 0:
            logging.getLogger(__name__).warning(
                "NI-845x [%s]: %s", status_code, self._status_to_string(status_code)
            )

    # ---------- 设备查找 / 打开 / 关闭 ----------
    def find_devices(self) -> list[str]:
        """返回当前连接的所有 USB-845x 设备资源名列表。"""
        name_buf = ctypes.create_string_buffer(_MAX_DEVICE_NAME_SIZE)
        find_handle = NiHandle(0)
        num_found = ctypes.c_uint32(0)

        status = self._dll.ni845xFindDevice(
            name_buf, ctypes.byref(find_handle), ctypes.byref(num_found)
        )
        try:
            self._check(status)
            if num_found.value == 0:
                return []
            devices = [name_buf.value.decode(errors="replace")]
            for _ in range(num_found.value - 1):
                self._check(self._dll.ni845xFindDeviceNext(find_handle, name_buf))
                devices.append(name_buf.value.decode(errors="replace"))
            return devices
        finally:
            if find_handle.value:
                self._check(self._dll.ni845xCloseFindDeviceHandle(find_handle))

    def open(self, device_name: Optional[str] = None, io_voltage: Optional[float] = None):
        """打开设备；未指定名称时只在恰好发现一台设备时自动选择。

        USB-8451 应保持 io_voltage=None，不调用仅适用于支持可编程电压设备的设置。
        """
        if self.device_handle is not None:
            raise RuntimeError("设备已经打开，请先关闭。")
        if io_voltage is not None and io_voltage not in _IO_VOLTAGE_LEVELS:
            raise ValueError("io_voltage 仅支持 1.2、1.5、1.8、2.5、3.3V；USB-8451 请留空。")
        if device_name is None:
            devices = self.find_devices()
            if not devices:
                raise RuntimeError(
                    "没有找到任何 NI USB-845x 设备。请检查：设备是否已用 USB 连接、"
                    "NI-845x 驱动是否安装成功、在 NI MAX 里是否能看到该设备。"
                )
            if len(devices) != 1:
                raise RuntimeError(f"发现多台设备，请明确指定 device_name：{devices}")
            device_name = devices[0]

        handle = NiHandle(0)
        status = self._dll.ni845xOpen(device_name.encode(), ctypes.byref(handle))
        self._check(status)

        self.device_handle = handle.value
        self.device_name = device_name
        try:
            if io_voltage is not None:
                self.set_io_voltage(io_voltage)
        except Exception:
            self.close()
            raise

    def close(self):
        """关闭所有 I2C 配置句柄，然后关闭设备连接。"""
        errors = []
        for key, cfg_handle in list(self._i2c_configs.items()):
            try:
                self._check(self._dll.ni845xI2cConfigurationClose(cfg_handle))
                del self._i2c_configs[key]
            except NI845xError as exc:
                errors.append(exc)

        if self.device_handle is not None:
            try:
                self._check(self._dll.ni845xClose(self.device_handle))
                self.device_handle = None
                self.device_name = None
            except NI845xError as exc:
                errors.append(exc)
        if errors:
            raise errors[0]

    def set_io_voltage(self, volts: float):
        if self.device_handle is None:
            raise RuntimeError("设备尚未打开，请先调用 open()。")
        if volts not in _IO_VOLTAGE_LEVELS:
            raise ValueError("可编程 I/O 电平仅支持 1.2、1.5、1.8、2.5、3.3V。")
        level = _IO_VOLTAGE_LEVELS[volts]
        status = self._dll.ni845xSetIoVoltageLevel(self.device_handle, level)
        self._check(status)

    # ---------- I2C 配置管理（内部会按 address/clock 自动缓存复用） ----------
    @staticmethod
    def _validate_clock_rate(clock_rate_khz: int):
        if type(clock_rate_khz) is not int or clock_rate_khz not in _SUPPORTED_CLOCK_RATES_KHZ:
            supported = "/".join(str(rate) for rate in sorted(_SUPPORTED_CLOCK_RATES_KHZ))
            raise ValueError(f"USB-8451 clock_rate_khz 仅支持 {supported} kHz。")

    def _get_i2c_config(
        self, address: int, address_size: int = 7, clock_rate_khz: int = 100
    ) -> int:
        if type(address_size) is not int or address_size not in (7, 10):
            raise ValueError("address_size 必须是 7 或 10。")
        if type(address) is not int or not 0 <= address < (1 << address_size):
            raise ValueError(f"address 必须是 0~{(1 << address_size) - 1} 的整数。")
        self._validate_clock_rate(clock_rate_khz)
        key = (address, address_size, clock_rate_khz)
        if key in self._i2c_configs:
            return self._i2c_configs[key]

        cfg_handle = NiHandle(0)
        self._check(self._dll.ni845xI2cConfigurationOpen(ctypes.byref(cfg_handle)))

        addr_size_const = (
            NI845X_7BIT_ADDRESSING if address_size == 7 else NI845X_10BIT_ADDRESSING
        )
        try:
            self._check(
                self._dll.ni845xI2cConfigurationSetAddressSize(cfg_handle, addr_size_const)
            )
            self._check(
                self._dll.ni845xI2cConfigurationSetAddress(cfg_handle, address)
            )
            self._check(
                self._dll.ni845xI2cConfigurationSetClockRate(cfg_handle, clock_rate_khz)
            )
        except Exception:
            self._check(self._dll.ni845xI2cConfigurationClose(cfg_handle))
            raise

        self._i2c_configs[key] = cfg_handle.value
        return cfg_handle.value

    # ---------- I2C 读写 ----------
    @staticmethod
    def _validate_transfer_size(size: int):
        if type(size) is not int or not 1 <= size <= _MAX_TRANSFER_SIZE:
            raise ValueError(f"单次传输字节数必须是 1~{_MAX_TRANSFER_SIZE} 的整数。")

    def write(
        self, address: int, data: bytes, clock_rate_khz: int = 100, address_size: int = 7
    ):
        if self.device_handle is None:
            raise RuntimeError("设备尚未打开，请先调用 open()。")
        self._validate_transfer_size(len(data))
        cfg = self._get_i2c_config(address, address_size, clock_rate_khz)
        buf = (ctypes.c_uint8 * len(data))(*data)
        status = self._dll.ni845xI2cWrite(
            self.device_handle, cfg, ctypes.c_uint32(len(data)), buf
        )
        self._check(status)

    def read(
        self,
        address: int,
        num_bytes: int,
        clock_rate_khz: int = 100,
        address_size: int = 7,
    ) -> bytes:
        if self.device_handle is None:
            raise RuntimeError("设备尚未打开，请先调用 open()。")
        self._validate_transfer_size(num_bytes)
        cfg = self._get_i2c_config(address, address_size, clock_rate_khz)
        buf = (ctypes.c_uint8 * num_bytes)()
        num_read = ctypes.c_uint32(0)
        status = self._dll.ni845xI2cRead(
            self.device_handle, cfg, ctypes.c_uint32(num_bytes),
            ctypes.byref(num_read), buf
        )
        self._check(status)
        if num_read.value > num_bytes:
            raise RuntimeError(f"驱动返回异常读取长度：{num_read.value} > {num_bytes}")
        return bytes(buf[: num_read.value])

    def write_read(
        self,
        address: int,
        write_data: bytes,
        num_bytes_to_read: int,
        clock_rate_khz: int = 100,
        address_size: int = 7,
    ) -> bytes:
        if self.device_handle is None:
            raise RuntimeError("设备尚未打开，请先调用 open()。")
        self._validate_transfer_size(len(write_data))
        self._validate_transfer_size(num_bytes_to_read)
        cfg = self._get_i2c_config(address, address_size, clock_rate_khz)
        write_buf = (ctypes.c_uint8 * len(write_data))(*write_data)
        read_buf = (ctypes.c_uint8 * num_bytes_to_read)()
        num_read = ctypes.c_uint32(0)
        status = self._dll.ni845xI2cWriteRead(
            self.device_handle, cfg,
            ctypes.c_uint32(len(write_data)), write_buf,
            ctypes.c_uint32(num_bytes_to_read), ctypes.byref(num_read), read_buf
        )
        self._check(status)
        if num_read.value > num_bytes_to_read:
            raise RuntimeError(
                f"驱动返回异常读取长度：{num_read.value} > {num_bytes_to_read}"
            )
        return bytes(read_buf[: num_read.value])

    def scan(self, clock_rate_khz: int = 100) -> list[int]:
        """使用 START + 地址/写方向 + STOP 探测 0x03~0x77，不含数据阶段。

        这仍会访问总线，不能保证对所有从机无副作用。只将地址 NAK 视为未发现。
        """
        if self.device_handle is None:
            raise RuntimeError("设备尚未打开，请先调用 open()。")
        self._validate_clock_rate(clock_rate_khz)
        d = self._dll
        script = NiHandle(0)
        self._check(d.ni845xI2cScriptOpen(ctypes.byref(script)))
        found = []
        try:
            for address in range(0x03, 0x78):
                self._check(d.ni845xI2cScriptReset(script))
                self._check(d.ni845xI2cScriptClockRate(script, clock_rate_khz))
                self._check(d.ni845xI2cScriptAckPollTimeout(script, 0))
                self._check(d.ni845xI2cScriptIssueStart(script))
                self._check(d.ni845xI2cScriptAddressWrite(script, address))
                self._check(d.ni845xI2cScriptIssueStop(script))
                status = d.ni845xI2cScriptRun(script, self.device_handle, 0)
                if status < 0:
                    # 文档未保证出错后继续执行已排队的 STOP，因此显式尝试释放总线。
                    self._check(d.ni845xI2cScriptReset(script))
                    self._check(d.ni845xI2cScriptIssueStop(script))
                    self._check(d.ni845xI2cScriptRun(script, self.device_handle, 0))
                if status == NI845X_ERROR_ADDRESS_NAK:
                    continue
                self._check(status)
                found.append(address)
            return found
        finally:
            self._check(d.ni845xI2cScriptClose(script))
