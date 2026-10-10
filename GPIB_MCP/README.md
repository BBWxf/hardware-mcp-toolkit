# GPIB MCP Server

一个供 Claude Code 本机使用的 GPIB/VISA MCP 服务。服务通过 PyVISA 调用系统已安装的 NI-VISA/NI-488.2，并使用 stdio 与 Claude Code 通信。

## 功能

服务提供两类 MCP 工具。

### 通用 GPIB 工具

| 工具 | 用途 |
|---|---|
| `gpib_scan` | 扫描所有 GPIB `INSTR` 资源 |
| `gpib_identify` | 向仪器发送 `*IDN?` |
| `gpib_query` | 发送任意文本命令并读取文本响应 |
| `gpib_write` | 发送任意 SCPI/设备写命令 |
| `gpib_query_raw` | 写命令后读取原始字节，以 Base64 返回 |
| `gpib_read_raw` | 读取仪器已经等待返回的原始字节 |

### Chroma 仪器专用工具

专用工具每次都会以 `*IDN?` 校验目标设备型号。`resource` 可省略：服务会扫描并在仅发现一台匹配仪器时自动选择；同型号设备多于一台时，必须先调用 `chroma_discover`，再显式提供对应的 `resource`。

| 工具 | 用途 |
|---|---|
| `chroma_discover` | 扫描并识别 Chroma 6530、66205、63200A 系列模块 |
| `chroma_6530_configure_output` | 设置 6530 电压、频率和/或交流输出开关；电压和 `RANG AUTO` 在同一命令中写入 |
| `chroma_66205_read_input` | 读取 66205 的 RMS 电压、电流、有功功率、PF 和输入电流 THD |
| `chroma_63200a_set_cc_current` | 将 63200A 切换为 `CCH` 并设定 `CURR:STAT:L1`，不改变负载开关状态 |
| `chroma_63200a_set_cv_voltage` | 将 63200A 切换为 `CVH` 并设定 `VOLT:STAT:L1`，不改变负载开关状态 |
| `chroma_63200a_set_load_enabled` | 开启或关闭 63200A 负载输入 |
| `chroma_63200a_read_measurements` | 读取 63200A 实际电压、电流和功率 |

63200A 会从 `*IDN?` 返回的实际模块型号动态识别模块，但当前未提供模块规格表，服务只校验设定值非负；模块的最大电压、电流和功率仍由仪器本身校验。66205 的 THD 原始数值没有在手册中明确说明是百分比还是比例值，工具保留仪器返回的原始数值，不做换算。

所有 VISA 操作在进程内串行执行。每次调用都会单独打开并关闭仪器会话，避免多个 MCP 调用交错执行 query 的 write/read。

## 安全警告

`gpib_write`、`gpib_query` 和 `gpib_query_raw` **不会过滤命令，也没有命令白名单**。命令可能：

- 打开电源、射频、激光或其他输出；
- 改变电压、电流、功率、频率、量程和触发状态；
- 清除仪器存储或恢复出厂设置；
- 启动校准、扫描或长时间采集。

服务只保证通信串行化，不判断物理操作是否安全。写入前应先使用 `gpib_identify` 核对资源对应的真实仪器，并按照仪器手册和实验室安全流程操作。

## 环境要求

- Windows 10/11
- Python 3.12 x64
- `uv`
- NI-VISA、Keysight VISA 或兼容的系统 VISA 实现
- 对应 GPIB 控制器驱动，例如 NI-488.2

当前项目默认使用：

```python
pyvisa.ResourceManager()
```

它会加载系统 VISA，不会自动切换到 `pyvisa-py` 或模拟后端。

## 安装

在项目目录执行：

```powershell
uv sync --python 3.12
```

检查依赖和 VISA 后端：

```powershell
uv run python -c "from importlib.metadata import version; print('mcp', version('mcp')); print('PyVISA', version('PyVISA'))"
uv run pyvisa-info
```

直接检查可见资源：

```powershell
uv run python -c "import pyvisa; rm=pyvisa.ResourceManager(); print(rm.visalib); print(rm.list_resources()); rm.close()"
```

## 本地运行

服务默认使用 stdio。不要直接向 stdout 写日志或启动提示；本项目日志固定输出到 stderr。

```powershell
uv run gpib-mcp
```

直接运行时服务会等待 MCP 客户端通过 stdin/stdout 连接，看起来没有输出属于正常现象。

开发时也可以使用 MCP Inspector：

```powershell
uv run mcp dev src/gpib_mcp/server.py
```

## 接入 Claude Code

使用绝对路径注册为当前项目的 local-scope MCP Server。Windows PowerShell 中应调用 `claude.cmd`，避免 `claude.ps1` 将服务启动参数（如 `-m`）误解析为自身参数：

```powershell
$python = (Resolve-Path ".\.venv\Scripts\python.exe").Path
claude.cmd mcp add --transport stdio --scope local gpib -- "$python" -m gpib_mcp.server
```

在 `GPIB_MCP` 目录执行上述命令，使用由 `uv sync --python 3.12` 创建的虚拟环境。`--scope local` 只在当前项目生效；如需全局使用，可改为 `--scope user`。若此前已注册同名服务，先在对应 scope 移除，再重新添加。

检查注册状态：

```powershell
claude mcp list
claude mcp get gpib
```

然后重新进入或刷新 Claude Code，在会话中执行：

```text
/mcp
```

应当能看到 `gpib` 服务及其通用 GPIB 工具和 Chroma 专用工具。

如需移除：

```powershell
claude.cmd mcp remove gpib --scope local
```

## 使用示例

可以在 Claude Code 中提出：

```text
扫描当前 GPIB 仪器。
```

```text
对 GPIB0::5::INSTR 执行 *IDN?，超时设为 5000 毫秒。
```

```text
对 GPIB0::5::INSTR 查询 MEAS:VOLT?。
```

```text
对 GPIB0::5::INSTR 执行 WAV:DATA?，使用原始数据查询并返回 Base64。
```

Chroma 专用工具示例：

```text
扫描并识别当前连接的 Chroma 仪器。
```

```text
将自动识别到的 Chroma 6530 设为 230 V、50 Hz 并开启输出。
```

```text
读取自动识别到的 Chroma 66205 输入 RMS 电压、电流、功率、PF 和 THD。
```

```text
将 GPIB0::8::INSTR 上的 63200A 设置为 CCH 模式、10 A；不要开启负载。
```

```text
读取 GPIB0::8::INSTR 上 63200A 的即时电压、电流和功率。
```

`chroma_6530_configure_output` 和 `chroma_63200a_set_load_enabled` 会改变物理输出状态。调用前请核对识别结果、连接对象、设定值及实验室安全流程。63200A 专用工具在错误后只报告错误，不会自动关闭电子负载；6530 同样不会在异常时自动关闭交流输出。

调用参数中的终止符：

- `"\n"`：换行终止符，常见于 SCPI 仪器；
- `"\r\n"`：CRLF；
- `null`：禁用字符终止符，依赖 GPIB EOI/END 或设备自身协议。

终止符必须以仪器编程手册为准。并非所有 GPIB 仪器都支持 SCPI 或 `*IDN?`。

## 测试

运行无硬件测试：

```powershell
uv run pytest --ignore=tests/test_hardware.py
```

测试包括：

- PyVISA 服务层；
- MCP 进程内客户端调用；
- Base64 原始数据序列化；
- 全局 VISA I/O 串行化；
- pyvisa-sim 模拟仪器；
- Chroma 动态识别、6530 耦合电压/量程命令、66205 批量测量解析；
- 63200A CCH/CVH 设定、即时测量和仪器错误时不自动关闭负载的行为。

真实硬件测试默认跳过。显式启用：

```powershell
$env:GPIB_MCP_HARDWARE_TEST = "1"
$env:GPIB_MCP_TEST_RESOURCE = "GPIB0::5::INSTR"
uv run pytest tests/test_hardware.py -v
```

硬件测试只执行扫描和 `*IDN?`，不会自动发送改变仪器状态的写命令。

## 日志

日志只写 stderr。默认级别为 `INFO`，可以通过环境变量调整：

```powershell
$env:GPIB_MCP_LOG_LEVEL = "DEBUG"
uv run gpib-mcp
```

`DEBUG` 会记录命令文本，但不会记录完整二进制响应。
