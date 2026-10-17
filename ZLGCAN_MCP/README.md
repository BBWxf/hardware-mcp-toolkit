# ZLG USB-CAN2 MCP

参考厂家 `zlgcan/CANzlg_Test2` 的 C# 示例，将 ZLG **USB-CAN2** 通讯卡封装为本地 stdio MCP 服务。厂商示例、SDK DLL 和 USB 驱动不随本仓库分发，请从 [ZLG 官方渠道](https://www.zlg.cn/)获取并遵守其授权。

- 设备类型固定为 `ZCAN_USBCAN2 = 4`，双通道 `0 / 1`，仅支持经典 CAN（每帧最多 8 字节）。
- 不是 `USBCAN-2E-U`、CAN FD 或其他厂家兼容卡的通用驱动。
- 使用 MCP Python SDK `FastMCP`，不需要 API Key，不监听网络端口。
- 启动服务不加载 DLL、不打开卡；`connect` 才打开设备，`init_channel` 才启动通道。
- 默认 **只听模式**。应用报文只能通过显式调用 `send_frame` 发出，不提供自动探测报文、周期发送或固件升级。

## 环境要求

1. Windows，安装与 USB-CAN2 对应的厂家 USB 驱动。
2. Python 3.10+。**本项目已验证的厂家 SDK DLL 是 x86，必须使用 32 位 Python。**
3. 下载 SDK 后，保持完整的 DLL、插件和配置目录。可以通过下文的 `ZLGCAN_DLL_PATH` 指向 DLL；也可以按默认查找路径放置文件（默认路径是相对于本项目目录）：

   ```text
   zlgcan/CANzlg_Test2/bin/x86/Debug/
   ├── zlgcan.dll
   └── kerneldlls/
       ├── usbcan.dll
       ├── CANDevCore.dll
       ├── dll_cfg.ini
       └── devices_property/...
   ```

4. 老版 SDK 依赖 **VC++ 2005 SP1 x86 / VC++ 2008 x86** 运行库。只安装 VC++ 2015–2022 不保证满足这些依赖。

实现会先静态检查 DLL 的 PE 位数，阻止 32/64 位混用。不要只复制一个 `zlgcan.dll`，也不要将其他版本的插件零散混入目录。

## 安装和启动

在 `ZLGCAN_MCP` 目录创建独立的 32 位 Python 环境。推荐 Python 3.12 x86；已安装该版本时，可使用 Python Launcher：

```powershell
py -3.12-32 -m venv .venv
.venv/Scripts/python.exe -m pip install --only-binary=:all: -r requirements.txt

# 启动后等待 MCP 客户端输入是正常行为
.venv/Scripts/python.exe server.py
```

也可以使用 uv，并指定已安装的 32 位解释器的完整路径：

```powershell
uv venv --python "C:/path/to/Python312-32/python.exe" .venv
uv pip install --python .venv/Scripts/python.exe --only-binary :all: -r requirements.txt
```

依赖中将 Windows 的 `cryptography` 固定为 `46.0.7`，因为该版本提供 x86 wheel，而 `50.0.2` 没有对应 wheel；避免安装过程中意外进入本地 Rust 编译。

### 指定另一套 SDK

默认 DLL 路径相对于 `zlgcan_device.py` 计算，不依赖客户端的启动目录。需要改变位置时设置完整 DLL 路径：

```powershell
$env:ZLGCAN_DLL_PATH = "D:\ZLG_SDK\zlgcan.dll"
.venv/Scripts/python.exe server.py
```

加载时会注册 DLL 和 `kerneldlls` 搜索目录，并将**当前服务进程**的工作目录切换到 SDK 目录，供旧版插件查找相对资源。该行为不会改变调用客户端的工作目录。若替换成 x64 SDK，必须同时更换完整配套文件和 Python 位数；本次只验证了开发环境中使用的 x86 SDK。

## 接入 MCP 客户端

`mcp-config.example.json` 提供通用配置模板。请将 `command` 和 `args` 中的占位路径替换为实际克隆位置：

```json
{
  "mcpServers": {
    "zlgcan": {
      "command": "C:\\path\\to\\hardware-mcp-toolkit\\ZLGCAN_MCP\\.venv\\Scripts\\python.exe",
      "args": ["C:\\path\\to\\hardware-mcp-toolkit\\ZLGCAN_MCP\\server.py"]
    }
  }
}
```

若 SDK 不在默认位置，可在该服务器条目中额外添加：

```json
"env": {"ZLGCAN_DLL_PATH": "D:\\ZLG_SDK\\zlgcan.dll"}
```

在本项目目录注册到 Claude Code（仅当前项目）：

```powershell
$python = (Resolve-Path ".\.venv\Scripts\python.exe").Path
$server = (Resolve-Path ".\server.py").Path
claude.cmd mcp add --scope local --transport stdio zlgcan -- "$python" "$server"
claude.cmd mcp get zlgcan
```

需要跨项目使用时将 `--scope local` 改为 `--scope user`。在新会话或 `/mcp` 中重新连接；不要同时用 JSON 和命令行重复注册同名服务器。本次实现没有修改客户端实际配置。

## 工具（10 个）

| 工具 | 作用与注意事项 |
|---|---|
| `driver_info()` | 静态读取 Python/DLL 位数、路径和支持的波特率；不加载驱动 |
| `status()` | 查询服务保存的连接、通道和配置；不代表实际在线或总线健康 |
| `connect(device_index=0)` | 打开类型 4 的设备索引（0~31）；不启动通道 |
| `disconnect()` | 关闭本服务持有的整张卡，两个通道都会停止 |
| `init_channel(channel, bitrate=500000, mode="listen_only")` | 配置并启动通道；只听或正常模式，全接收过滤 |
| `reset_channel(channel)` | 复位并停止通道；不自动重新启动 |
| `send_frame(channel, can_id, data_hex="", extended=false, remote=false, dlc=null)` | 单次发送一帧经典 CAN；只允许在 normal 通道调用 |
| `receive_frames(channel, max_frames=100, timeout_ms=0)` | 消费接收缓冲区，最多 1000 帧，有限等待 0~10000 ms |
| `get_receive_count(channel)` | 查询经典 CAN 接收队列长度，不消费报文 |
| `read_error(channel)` | 读取原始错误码与辅助字段；可能消费/清除 SDK 保存的错误信息 |

### 通道配置

- 两通道可分别设置不同波特率。
- 支持 `10000 / 20000 / 50000 / 100000 / 125000 / 250000 / 500000 / 800000 / 1000000` bit/s。
- 只开放本地 USB-CAN2 XML 和示例代码一致列出的九档。暂不支持 5 kbit/s、自定义 Timing0/Timing1。
- `listen_only`：只听，不主动 ACK，也禁止 `send_frame`。
- `normal`：正常收发，会自动 ACK 合法总线报文。因此即使不调用 `send_frame`，也不能把正常模式称为完全被动监听。
- 已初始化通道改配置时必须先 `reset_channel`，再 `init_channel`。
- `connect` 在连接同一索引时幂等；切换卡必须先 `disconnect`。一个服务进程只持有一张卡，不会自动扫描/占用其他设备。

### 报文格式

`can_id` 支持 JSON 十进制整数，或 `"0x123"` 格式的字符串。标准 ID 范围 `0~0x7FF`，扩展 ID 范围 `0~0x1FFFFFFF`；标志位由服务添加，不要预先混入 ID。

`data_hex` 支持 `"01 02 A5"`、`"0102A5"`、`"0x01,0x02,0xA5"`，不接受超过 8 字节、半字节或非十六进制数据，不会静默截断或自动分包。

- 数据帧：`dlc` 留空时等于数据长度；显式设置时必须与长度一致。允许零长度数据帧。
- 远程帧：`remote=true`，`data_hex` 必须为空，通过 `dlc` 指定请求长度（0~8）。
- 发送固定使用 `transmit_type=1`（单次发送），服务不进行软件重试。SDK 返回 1 帧不表示从机已处理或应用层成功。
- 接收保留标准/扩展、远程、错误标志和原始 `raw_can_id`。远程帧的数据域返回空字符串。
- `timestamp_us` 是 SDK 微秒时间戳，**不是 Unix 时间**；本地资料没有定义绝对起点或跨卡同步规则。
- 接收超时或暂无数据返回 `count=0, frames=[]`。所有原生调用串行化，不存在后台线程与工具同时抢读队列的情况；长等待会暂时阻塞其他工具操作。

## 调用示例

### 先只听观察

```text
connect({"device_index": 0})
init_channel({"channel": 0, "bitrate": 500000, "mode": "listen_only"})
get_receive_count({"channel": 0})
receive_frames({"channel": 0, "max_frames": 100, "timeout_ms": 100})
read_error({"channel": 0})
disconnect({})
```

### 确认协议后再发送

以下仅说明工具参数，不是可直接用于未知设备的测试报文：

```text
connect({"device_index": 0})
init_channel({"channel": 0, "bitrate": 500000, "mode": "normal"})
send_frame({"channel": 0, "can_id": "0x123", "data_hex": "01 02 A5"})
receive_frames({"channel": 0, "max_frames": 10, "timeout_ms": 100})
disconnect({})
```

发送前必须确认目标协议、供电、共地、CAN_H/CAN_L、波特率和总线两端的终端电阻。USB-CAN2 的内部终端电阻开关没有在此示例中得到证实，本服务不提供该功能。

## 测试与验证

```powershell
# 假 DLL 测试：不加载 SDK，不接触硬件
.venv/Scripts/python.exe -m unittest -v

# 真实子进程 MCP 握手、工具列表和静态驱动信息；不打开卡
.venv/Scripts/python.exe check_mcp.py

# 需卡已接入：只打开/关闭索引 0，不启动通道，也不发帧
.venv/Scripts/python.exe check_mcp.py --connect --device-index 0
```

已验证：30 项单元测试通过，真实 stdio 握手和 10 个工具注册通过，开发环境中的 x86 DLL 加载及函数绑定通过。2026-10-17 接入实际 USB-CAN2 后，`check_mcp.py --connect --device-index 0` 打开、状态查询和关闭均通过；测试结束已释放设备。**尚未启动实际 CAN 通道或进行物理 CAN 收发；默认检查仍不打开卡。**

## 实现与边界

- `zlgcan_device.py`：ctypes ABI、DLL 位数检查、设备与双通道生命周期、经典 CAN 收发和原始错误读取。
- `server.py`：FastMCP 工具和退出时关闭设备。
- `check_mcp.py`：真实 MCP stdio 客户端检查。
- `test_zlgcan_device.py`、`test_server.py`：无硬件测试。
- `mcp-config.example.json`：客户端配置示例。

实现依据是本地 `zlgcan.cs` 中的 stdcall 函数签名和结构体、`Form_Main.cs` 的波特率设置顺序及 `usbcan2.xml`。保留完整 32 字节初始化联合体，避免错误地缩成 20 字节；修正了示例中经典 CAN 的只听模式被覆盖、错误信息未回读的问题。

网上也存在 [ZLG CAN Python 接口示例](https://github.com/guochuangjian/zlgcan_py/blob/master/zlgcan.py)，但网络示例不能证明与本地二进制版本兼容。本地缺少匹配的完整头文件，因此没有根据导出名猜测 `GetAvailableDevices`、`GetDeviceInf` 或 `ReadChannelStatus` 的 ABI：当前不提供真正的 USB 枚举、序列号读取和详细控制器状态工具，也不解释未定义的错误码位。

其他限制：不支持 CAN FD、ISO-TP、DBC、J1939、自动重连、定时发送或协议层固件升级。报错不会自动断开其他通道或重新发送；关闭/复位失败会报告 SDK 原始返回值，保留本地状态供显式重试。SDK 接收缓冲区容量由厂家驱动决定，低频轮询无法保证高负载下不丢帧。

厂商示例和 DLL 保留在本地 `zlgcan/`，已加入本项目 `.gitignore`，未确认再分发授权前不随源码发布；在其他机器部署时需要自行准备完整 SDK。
