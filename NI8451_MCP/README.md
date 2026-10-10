# NI USB-8451 I2C MCP 服务

把 NI USB-8451（I2C/SPI 接口卡）的 I2C 功能包装成一个 MCP 服务，
让 Claude（或其他支持 MCP 的客户端）可以直接扫描 I2C 总线、读写寄存器。

**本项目只实现了 I2C，没有实现 SPI 和数字 IO。**

## 运行环境要求（重要）

- **必须是 Windows**（NI-845x 驱动只提供 `Ni845x.dll`，没有 Linux/macOS 版本）
- 已从 NI 官网安装 **NI-845x 驱动**
- USB-8451 已通过 USB 接到本机，并且在 **NI MAX**
  （Measurement & Automation Explorer）里能看到这个设备
- Python 3.10+

## 安装

建议使用独立的 **64 位 Python** 环境，避免系统 `python` 指向另一套 32 位解释器：

```powershell
py -3.12 -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
```

本项目使用 MCP Python SDK 的 `FastMCP`，通过 stdio 运行，不需要 API Key 或网络监听端口。

- `ni845x_i2c.py` — 对 `Ni845x.dll` 的 ctypes 封装（只有 I2C 部分）
- `server.py` — MCP 服务本体
- `test_ni845x_i2c.py`、`test_server.py` — 无硬件单元测试
- `check_mcp.py` — 真实子进程握手和 USB 枚举检查

## 本地验证

```powershell
# 假 DLL 单元测试，不访问硬件
.venv/Scripts/python.exe -m unittest -v

# 启动 stdio 子进程，验证握手、8 个工具和连接状态，不加载驱动
.venv/Scripts/python.exe check_mcp.py

# 额外加载驱动、枚举 USB 卡；不连接从机，不扫描/读写 I2C 总线
.venv/Scripts/python.exe check_mcp.py --hardware

# 打开并关闭 USB 卡；不访问 I2C 总线
.venv/Scripts/python.exe check_mcp.py --connect

# 直接启动服务（等待 MCP 客户端的 stdin 输入是正常现象）
.venv/Scripts/python.exe server.py
```

如需 MCP Inspector，可另行安装 `mcp[cli]==1.30.0`，再用 `.venv/Scripts/mcp.exe dev server.py`。
不要把 I2C 扫描当成无副作用检测：确认从机协议、供电、接线和上拉后才执行总线操作。

## 接入 Claude Desktop / Claude Code

在 `NI8451_MCP` 目录使用虚拟环境的绝对路径注册到 Claude Code：

```powershell
$python = (Resolve-Path ".\.venv\Scripts\python.exe").Path
$server = (Resolve-Path ".\server.py").Path
claude.cmd mcp add --scope local --transport stdio ni8451-i2c -- "$python" "$server"
claude mcp get ni8451-i2c
```

`--scope local` 仅在当前项目生效；如需全局使用，改为 `--scope user`。新会话会加载此配置；当前会话可在 `/mcp` 中检查并尝试重新连接。

其他客户端可在 MCP 配置里加一段，将示例路径替换成实际克隆位置（Claude Desktop 的 `claude_desktop_config.json`，
或 Claude Code 的 `.mcp.json`；同一项目不要与上面的 local 注册重复添加）：

```json
{
  "mcpServers": {
    "ni8451-i2c": {
      "command": "C:\\path\\to\\lab-instrument-mcp\\NI8451_MCP\\.venv\\Scripts\\python.exe",
      "args": ["C:\\path\\to\\lab-instrument-mcp\\NI8451_MCP\\server.py"]
    }
  }
}
```

## 提供的工具

| 工具 | 作用 |
|---|---|
| `list_devices` | 列出已连接的 USB-845x 设备 |
| `connect(device_name?, io_voltage=None)` | 连接设备；未填名称时必须只发现一台。USB-8451 不设置电压 |
| `disconnect` | 断开设备 |
| `status` | 查看当前连接状态 |
| `i2c_scan(clock_rate_khz=100)` | 用脚本发送 START + 地址/写方向 + STOP，探测 0x03~0x77，无数据阶段 |
| `i2c_write(address, data_hex, clock_rate_khz=100, address_size=7)` | 写数据 |
| `i2c_read(address, num_bytes, clock_rate_khz=100, address_size=7)` | 读数据 |
| `i2c_write_read(address, data_hex, num_bytes_to_read, ...)` | 先写后读（常用于"写寄存器地址再读值"） |

`clock_rate_khz` 仅支持 USB-8451 的 `32/40/50/64/80/100/125/160/200/250` kHz；默认值为 100。

`data_hex` 参数支持几种写法，效果一样：`"00 10 A5"` / `"0010A5"` / `"0x00,0x10,0xA5"`。

## 典型对话示例

- "连接 I2C 设备，然后扫描一下总线上有哪些从机"
- "往地址 0x50 写入 0x00 0x10，然后读 16 个字节"（EEPROM 常见的读操作模式）
- "读一下地址 0x48 这个温度传感器的第 0 号寄存器"

## 已知限制 / 后续可扩展

- 未实现 SPI（`Ni845xSpi*` 系列函数），如果你也需要可以按同样的模式加
- 未实现数字 IO（`Ni845xDio*` 系列函数）
- `ni845x_i2c.py` 里的函数签名来自 NI-845x 公开的编程手册；如果你安装的
  驱动版本较旧或较新导致调用报错，对照本机 SDK 目录下的 `Ni845x.h`
  头文件核实一下参数类型即可
- 当前只支持单个设备连接；如果你有多张 USB-8451，需要扩展成按
  `device_name` 区分的多实例管理
