# 硬件 MCP 工具箱（hardware-mcp-toolkit）

通过 [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) 将串口、GPIB/VISA 仪器、J-Link、NI USB-845x I2C 接口、泰克示波器、ZLG USB-CAN2、Microchip PICkit 4 和 Bode 100 接入支持 MCP 的客户端。

本仓库包含 **8 个独立的 Python stdio MCP 服务**，以 Windows 实验室环境为主。可以只安装所需的项目；各项目使用自己的虚拟环境、依赖和启动入口，不需要同时安装或运行全部服务。

## 工具列表

| 项目 | 功能 | 环境与外部依赖 | 安装与使用 |
| --- | --- | --- | --- |
| `COM_MCP` | 串口枚举、连接、原始字节收发、RTS/DTR 控制；不绑定产品协议 | Python、pyserial；按设备需要安装串口驱动 | [串口 MCP](COM_MCP/README.md) |
| `GPIB_MCP` | 通用 GPIB/VISA 文本与二进制通信；Chroma 6530、66205、63200A 专用工具 | Python 3.12、uv、系统 VISA 和 GPIB 控制器驱动 | [GPIB MCP](GPIB_MCP/README.md) |
| `JLINK_MCP` | 固件擦除、烧录、校验、复位；支持 HEX/BIN/ELF | Windows、Python 3.10+、SEGGER J-Link；Python 与 DLL 位数必须一致 | [J-Link MCP](JLINK_MCP/README.md) |
| `NI8451_MCP` | NI USB-845x 设备连接、I2C 扫描、读写和写后读；不包含 SPI/数字 IO | Windows、Python 3.10+、NI-845x 驱动 | [NI USB-845x I2C MCP](NI8451_MCP/README.md) |
| `SCOPE_MCP` | 泰克 4/5/6 系列 MSO、MDO3000 系列的自动识别、截图、波形、测量及采集设置 | 64 位 Python 3.10+；示波器网络连接、raw socket SCPI 和截图用 VXI-11 | [泰克示波器 MCP](SCOPE_MCP/README.md) |
| `ZLGCAN_MCP` | ZLG USB-CAN2 双通道经典 CAN 收发、只听、队列计数、错误读取和复位；10 个工具 | Windows、Python 3.10+、ZLG SDK 与 USB 驱动；已验证的 x86 SDK 需要 32 位 Python | [ZLG USB-CAN2 MCP](ZLGCAN_MCP/README.md) |
| `MICROCHIP_MCP` | Microchip PICkit 4 枚举、HEX 烧录/校验、擦除、空白检查及读回；9 个工具 | Windows、Python 3.10+、完整 MPLAB X/IPE、适用器件包及 PK4 驱动 | [Microchip IPE / PK4 MCP](MICROCHIP_MCP/README.md) |
| `BODE100_MCP` | Bode 100 官方 SCPI 接口：测量配置、触发、结果读取、校准及状态；30 个工具 | 推荐 Python 3.12+；仪器电脑安装适用的 BAS 3.50+、驱动并单独启动官方 SCPI Server | [Bode 100 MCP](BODE100_MCP/README.md) |

> 支持范围和已知限制以各子项目 README 为准。不同仪器型号、固件版本和驱动组合可能需要实机验证。

## 快速开始

### 1. 获取源码

```powershell
git clone https://github.com/BBWxf/hardware-mcp-toolkit.git hardware-mcp-toolkit
cd hardware-mcp-toolkit
```

### 2. 选择一个项目安装

例如安装串口服务，在仓库根目录执行：

```powershell
cd COM_MCP
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

其他使用 `requirements.txt` 的项目也应在各自目录创建独立环境。J-Link 和 ZLG CAN 须先确认 DLL 与 Python 的位数；ZLG 已验证的 SDK 为 x86，需使用 32 位 Python，不要直接套用不匹配的解释器。

GPIB 项目使用 uv，在 `GPIB_MCP` 目录执行：

```powershell
uv sync --python 3.12
```

**不要合并八个项目的依赖环境：** GPIB 使用 MCP SDK 2.x，其他项目使用 MCP SDK 1.x。

### 3. 接入 MCP 客户端

各服务通过 stdin/stdout 与客户端通信，不需要在服务中配置模型 API Key，也不启动 HTTP 监听端口。客户端负责启动服务；直接运行时等待输入、没有启动提示属于正常现象。

各子项目 README 提供客户端配置说明或注册命令。支持 stdio 的客户端也可以参考以下串口配置，将路径替换成实际克隆位置：

```json
{
  "mcpServers": {
    "serial-mcp": {
      "command": "C:\\path\\to\\hardware-mcp-toolkit\\COM_MCP\\.venv\\Scripts\\python.exe",
      "args": ["C:\\path\\to\\hardware-mcp-toolkit\\COM_MCP\\serial_mcp_server.py"]
    }
  }
}
```

客户端实际配置应保存在本机，不提交到仓库。GPIB 使用模块入口 `-m gpib_mcp.server`，其余服务的入口见各项目文档。

## 设备配置

- **串口 / GPIB / NI USB-845x：** 按工具要求传入串口、VISA 资源或设备名；自动发现不代表可以跳过设备身份核对。
- **J-Link：** 首次启动会生成 `%USERPROFILE%\.jlink_mcp\config.json`，按 [配置说明](JLINK_MCP/README.md#配置文件)填写安装路径、芯片和固件路径。实际固件及产品工程不包含在仓库中。
- **示波器：** 将 `SCOPE_MCP/scopes.example.json` 复制为同目录的 `scopes.json`，填写实际 IP。示例使用文档专用地址，不能直接连接真实设备；也可以在工具调用中显式指定 `ip`。
- **ZLG CAN：** 安装 USB-CAN2 驱动并准备完整 SDK，通过 `ZLGCAN_DLL_PATH` 指定 `zlgcan.dll`，或使用子项目文档中的默认目录。设备类型固定为 4，连接索引与 CAN 通道索引不同；默认只听，不自动发送报文。
- **Microchip PK4：** 将 `MICROCHIP_MCP/config.example.json` 复制为同目录的 `config.json`，填写 IPECMD 安装路径、真实序列号、芯片型号、电压及 HEX 路径。供电仅允许 3.3V/5V，具体器件必须同时受 PK4、当前 IPE 和器件包支持；实际配置、固件与日志不提交。
- **Bode 100：** 仪器电脑通过 USB 连接设备，并单独运行官方 SCPI Server；MCP 默认连接 `127.0.0.1:5025`。启动和工具发现不访问仪器，显式 `connect` 后才请求设备锁；不要与 BAS 或其他控制客户端同时操作。

厂商驱动、DLL、安装包和仪器手册不随仓库分发。请从官方渠道获取，并遵守对应授权条款：

- [NI 驱动下载](https://www.ni.com/en/support/downloads/drivers.html)：NI-VISA、NI-488.2、NI-845x。
- [SEGGER J-Link 下载](https://www.segger.com/downloads/jlink/)。
- [Microchip MPLAB X IDE / IPE](https://www.microchip.com/en-us/tools-resources/develop/mplab-x-ide)。
- [ZLG 官方网站](https://www.zlg.cn/)：获取 USB-CAN2 的 USB 驱动与配套 SDK。
- [OMICRON Lab Bode Analyzer Suite](https://www.omicron-lab.com/downloads/bode-analyzer-suite)：获取适用的 BAS、驱动和官方 SCPI Server；Windows 10/11 兼容性以对应版本要求为准。
- [Chroma 官方网站](https://www.chromaate.com/)：查阅对应型号的操作与编程手册。
- [泰克 4/5/6 系列 MSO 编程手册](https://download.tek.com/manual/4-5-6Series-MSO-Programmer-Interface-Manual_EN-US_077130508.pdf)。

## 硬件操作安全

**这些服务会真实操作硬件，不是仿真器。** MCP 客户端的工具审批不能代替电气安全检查和实验室操作规程。

- GPIB 通用命令不设命令白名单；查询命令也可能改变状态。电源及电子负载工具可以开启输出、改变电压或电流；通讯错误后不会自动保证输出关闭。
- J-Link 烧录默认先擦除、完成后复位运行，会覆盖目标芯片的固件。
- Microchip PK4 烧录使用 IPECMD 自身擦除/校验流程；读取和校验同样可能影响供电、复位及编程器固件。超时后不自动重试，必须检查真实硬件状态。
- I2C 扫描、写入以及部分读取可能改变从机状态；先确认协议、供电、上拉和电平。USB-8451 的 I2C 电平由外部上拉决定。
- 打开串口以及改变 RTS/DTR 可能复位开发板或切换烧录模式。
- CAN 发送会操作实际总线；先确认帧 ID、内容、波特率、接线和终端电阻。正常模式会 ACK 报文，默认只听模式不会。
- 示波器测量、波形读取和设置会更改部分仪器配置；多人或多客户端操作同一设备可能互相干扰。
- Bode 100 触发、校准和原始 SCPI 命令可能改变硬件状态；申请锁可能触发官方初始化或内部校准。超时断连不保证仪器停止，应重新连接并查询状态，不盲目重试。

调用前确认目标设备、接线、参数、额定范围和操作者授权。不要让自动化流程在无人确认的情况下执行擦除、烧录、供电或总线写操作。

## 无硬件测试

先在对应目录完成依赖安装，再使用各自的虚拟环境运行：

| 项目 | 无硬件验证命令（在子项目目录运行） |
| --- | --- |
| COM | `.\.venv\Scripts\python.exe -m unittest -v` |
| GPIB | `uv run pytest --ignore=tests/test_hardware.py` |
| NI USB-845x | `.\.venv\Scripts\python.exe -m unittest -v`；`.\.venv\Scripts\python.exe check_mcp.py` |
| SCOPE | `.\.venv\Scripts\python.exe -m unittest discover -v` |
| ZLG CAN | `.\.venv\Scripts\python.exe -m unittest -v`；`.\.venv\Scripts\python.exe check_mcp.py` |
| Microchip PK4 | `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` |
| Bode 100 | `.\.venv\Scripts\python.exe -m unittest discover -s tests -v`（假 SCPI Server + MCP stdio，尚未实机验证） |

J-Link 的 `manual_test.py flash` 和示波器的 `test_connection.py` 是真实硬件操作脚本，不属于无硬件单元测试。不要在仓库根目录对所有项目统一执行测试发现，也不要在无硬件验证中使用 NI 检查脚本的 `--hardware` / `--connect` 参数，或 ZLG 检查脚本的 `--connect` 参数。

## 仓库结构

```text
.
├── README.md
├── LICENSE
├── .gitignore
├── BODE100_MCP/
├── COM_MCP/
├── GPIB_MCP/
├── JLINK_MCP/
├── MICROCHIP_MCP/
├── NI8451_MCP/
├── SCOPE_MCP/
└── ZLGCAN_MCP/
```

虚拟环境、缓存、本地客户端配置、真实示波器/PK4 配置、运行日志、截图、产品固件、J-Flash 工程、厂商二进制文件和本地开发资料均不纳入版本管理。发布前仍应检查实际待提交文件，避免泄露产品信息和凭据。

## 许可证

本仓库代码及自有文档使用 [MIT License](LICENSE)。第三方依赖、驱动、厂商软件和手册遵守各自的许可证；仓库的 MIT 许可不替代或扩展这些第三方授权。
