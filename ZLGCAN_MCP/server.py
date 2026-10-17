"""ZLG USB-CAN2 MCP 服务；stdio 启动时不加载驱动、不打开设备。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Literal

from mcp.server.fastmcp import FastMCP

from zlgcan_device import ZlgCanDevice

LOGGER = logging.getLogger(__name__)


def create_server(device: ZlgCanDevice | None = None) -> FastMCP:
    can = device if device is not None else ZlgCanDevice()

    @asynccontextmanager
    async def lifespan(app: FastMCP):
        try:
            yield
        finally:
            try:
                can.disconnect()
            except (RuntimeError, OSError):
                LOGGER.exception("服务退出时关闭 USB-CAN2 失败")

    mcp = FastMCP("ZLG USB-CAN2", lifespan=lifespan)

    @mcp.tool()
    def driver_info() -> dict:
        """查看 Python/DLL 位数、SDK 路径和支持的波特率；不加载 DLL、不访问硬件。"""
        return can.driver_info()

    @mcp.tool()
    def status() -> dict:
        """查看本服务保存的连接与通道配置；不访问硬件，不代表设备仍在线或总线健康。"""
        return can.status()

    @mcp.tool()
    def connect(device_index: int = 0) -> dict:
        """打开指定索引的 ZLG USB-CAN2（设备类型固定为 4），不启动 CAN 通道。

        device_index 从 0 开始；多卡时请明确索引。不是 USB 序列号，不自动扫描其他型号。
        """
        return can.connect(device_index)

    @mcp.tool()
    def disconnect() -> dict:
        """关闭当前 USB-CAN2 及其所有通道；会中断本服务的 CAN 通讯。"""
        return can.disconnect()

    @mcp.tool()
    def init_channel(
        channel: int,
        bitrate: int = 500000,
        mode: Literal["listen_only", "normal"] = "listen_only",
    ) -> dict:
        """配置并启动 CAN 通道，channel 为 0 或 1，bitrate 单位 bit/s。

        默认 listen_only 只听，不发送也不 ACK；normal 可收发且会自动 ACK 总线报文。
        已启动的通道不能直接改配置，必须先 reset_channel。接收过滤器默认全部接收。
        操作前须确认接线、终端电阻和波特率；本工具不发送应用报文。
        """
        return can.init_channel(channel, bitrate, mode)

    @mcp.tool()
    def reset_channel(channel: int) -> dict:
        """复位并停止指定 CAN 通道；之后需要重新 init_channel，不自动重启。"""
        return can.reset_channel(channel)

    @mcp.tool()
    def send_frame(
        channel: int,
        can_id: int | str,
        data_hex: str = "",
        extended: bool = False,
        remote: bool = False,
        dlc: int | None = None,
    ) -> dict:
        """向已启动的 normal 通道发送一帧经典 CAN，采用单次发送、不自动重试。

        can_id 支持十进制整数或带 0x 前缀的字符串；标准 ID 最大 0x7FF，扩展 ID 最大 0x1FFFFFFF。
        data_hex 例如 '01 02 A5'、'0102A5'、'0x01,0x02,0xA5'，最多 8 字节。
        数据帧 dlc 留空自动计算，指定时必须与数据长度一致；远程帧 remote=true 时数据必须为空，
        dlc 指定请求长度（0~8）。成功返回仅表示驱动报告已发送，不表示从机已处理。
        会改变物理总线/设备状态，必须先确认目标协议和报文内容。无周期发送功能。
        """
        return can.send_frame(channel, can_id, data_hex, extended, remote, dlc)

    @mcp.tool()
    def receive_frames(channel: int, max_frames: int = 100, timeout_ms: int = 0) -> dict:
        """读取并消费指定通道接收缓冲区，最多 1~1000 帧，等待时间 0~10000 ms。

        timeout_ms=0 立即返回；无报文返回空列表。不会发请求或自动清空缓冲区。
        返回原始 CAN ID、标志、DLC、十六进制数据和 SDK 时间戳；不做业务协议解析。
        """
        return can.receive_frames(channel, max_frames, timeout_ms)

    @mcp.tool()
    def get_receive_count(channel: int) -> dict:
        """查询通道接收缓冲区当前帧数，不消费报文。"""
        return can.get_receive_count(channel)

    @mcp.tool()
    def read_error(channel: int) -> dict:
        """读取 SDK 通道错误码及原始辅助字段；该调用可能消费/清除驱动保存的错误信息。"""
        return can.read_error(channel)

    return mcp


mcp = create_server()

if __name__ == "__main__":
    mcp.run(transport="stdio")
