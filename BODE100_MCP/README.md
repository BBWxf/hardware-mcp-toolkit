# Bode 100 MCP

通过 OMICRON Lab 官方 SCPI Server，把 **Bode 100** 的设备接口提供为 30 个 stdio MCP 工具。模型负责选择和组合调用，服务不实现环路分析、绘图、自动调参、供电/负载联动或固定实验流程。

```text
MCP 客户端 / 模型 ←stdio→ server.py ←ASCII TCP→ 官方 SCPI Server ←USB→ Bode 100
```

不直接访问 USB，不逆向协议，不模拟鼠标，不需要模型 API Key。MCP 与 SCPI Server 是不同进程；5025 是官方 SCPI 端口，不是 MCP HTTP 端口。

## 1. 前置软件

仪器所在电脑需要安装官方 **Bode Analyzer Suite（BAS）及设备驱动**，并单独启动其中的 SCPI Server。MCP 不安装、不启动厂商软件。

- 官方自动化说明要求 BAS **3.50 或以上**，本项目按 **3.50 SCPI 文档**实现。
- 官方 3.50 系统要求明确列出 Windows 10 / 11、.NET 8 或以上。
- 当前 3.52 下载页列出的系统要求为 Windows 11；不能据此保证其支持 Windows 10。Windows 10 电脑应选择官方确认适用的版本。
- Python 侧只依赖 MCP SDK；使用标准库 TCP，不要求 NI-VISA、PyVISA 或 pythonnet。
- 仪器接线、输入额定值和校准标准仍按官方手册执行，软件不能替代硬件限制。

在官方安装目录运行（PowerShell；替换安装目录和实际序列号）：

```powershell
& "C:\实际安装目录\OmicronLab.VectorNetworkAnalysis.ScpiRunner.exe" -s "实际序列号" -p 5025
```

`-s` 选择 USB 仪器，`-p` 指定监听端口。只有一台设备时官方允许省略 `-s`；多台设备应显式选择。**本 MCP 不提供虚构的 USB 扫描接口**，一个连接对应已由 SCPI Server 选定的设备。

测量时不要让 BAS 或其他客户端同时控制同一台仪器。SCPI 服务应留在本机或可信实验室网络，不向公网开放。

## 2. 安装与启动

推荐 Python 3.12 或以上。以下命令在本项目目录执行：

```powershell
uv venv --python 3.12 .venv
uv pip install --python .venv/Scripts/python.exe -r requirements.txt
```

没有 uv 时：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

检查启动参数：

```powershell
.\.venv\Scripts\python.exe server.py --help
```

由 MCP 客户端启动服务；手动启动时会等待 stdin 上的 MCP 请求，不是交互式控制台：

```powershell
.\.venv\Scripts\python.exe server.py --host 127.0.0.1 --port 5025 --timeout 10
```

启动及工具发现都不连接仪器、不复位、不开始测量；只有显式调用 `connect` 才访问 SCPI Server。`connect` 核对身份、请求设备锁并选择 ASCII 数据传输；请求锁可能使官方程序执行设备初始化或内部校准。

## 3. MCP 客户端配置

参考 [mcp-config.example.json](mcp-config.example.json)，按实际目录修改绝对路径，再加入客户端自己的 MCP 配置。示例默认通过本机 `127.0.0.1:5025` 连接官方 SCPI Server。

- MCP 使用 **stdio**，日志只写 stderr，不污染 stdout。
- 不依赖客户端的工作目录。
- `PYTHONUTF8=1` 用于 Windows 中文环境。
- 示例是可提交的模板；不会自动修改你的 `.mcp.json` 或全局配置。
- TCP 超时通过 `--timeout` 指定；`wait_for_completion` / `scpi_query` 可单次覆盖。MCP 客户端自己的请求超时也必须足够长。

## 4. 工具清单

| 分类 | 工具 |
|---|---|
| 连接 | `connect`、`disconnect`、`get_connection_status` |
| 能力与状态 | `get_device_limits`、`get_status`、`get_configuration`、`get_reference` |
| 测量配置 | `configure_measurement`、`configure_sweep`、`configure_source`、`input_settings` |
| 结果与平均 | `configure_result`、`averaging_settings` |
| 触发与执行 | `configure_trigger`、`initiate_measurement`、`send_trigger`、`abort_measurement`、`estimate_measurement_time`、`wait_for_completion` |
| 数据与诊断 | `read_measurement`、`read_errors` |
| 校准 | `get_calibration_status`、`enable_calibration`、`execute_calibration`、`calibration_model`、`save_calibration`、`load_calibration` |
| 显式复位 | `reset_device` |
| 官方文本命令通道 | `scpi_write`、`scpi_query` |

每个工具包含参数 schema、单位、前置条件和副作用说明。常用工具不是完整官方命令列表的机械复制；未单独封装的状态寄存器、校准数组、温度等文本接口可通过原始 SCPI 通道访问。详细对应关系见 [SCPI 接口说明](SCPI.md)。

### 支持的测量模式

- `GAIN`：增益/相位。
- `S11`、`S21`：S 参数。
- `CH1` / `CH2`（别名 `R` / `T`）：绝对通道 VRMS。
- `Z`：`P1R` 单端反射、`TSH` 并联穿通、`TSER` 串联穿通、`ECO` 外部耦合器、`EBR` 外部桥、`IAD` 阻抗适配器、`VCG` 电压电流增益。

`configure_measurement(reset_defaults=false)` 保留已有输入设置；只有显式传 `true` 才向官方接口传 `DEF`。默认重置可能改变源电平和终端等，不能视为无害初始化。

### 不虚构 Bode 100 的接口

共享文档中有些接口仅适用于 Bode 500，**没有提供对应专用工具**：

- `:SENS:SWE:PSSTime`：路径切换等待时间。
- `:MMEM:CAT:CORR?`：设备内校准文件目录。
- `:MMEM:DEL:CORR`：删除设备内校准文件。

也不使用未在所查文档中定义的通用 `:OUTP ON/OFF`。`configure_source(auto_off=...)` 对应 `:OUTP:AOFF`，只控制扫频结束后自动关源，**不是立即开关输出**。

## 5. 调用语义

### 触发顺序由模型决定

例如，官方 BUS 单次测量可以按以下顺序调用；这只是用法示例，不是服务内部硬编码的实验流程：

1. `connect()`。
2. 按需要设置测量模式、输入、扫频、源和结果格式。
3. `configure_trigger(source="BUS", continuous=false)`。
4. `initiate_measurement()`。
5. `send_trigger()`。
6. `wait_for_completion(timeout_seconds=...)`。
7. `read_measurement()`。

配置工具不会附加启动或触发命令。INT 触发及 `continuous=true` 可能立即开始/反复测量；外部触发需要设备支持。校准只执行用户指定的一项标准，不代替人更换接线。

### 数据不是交错复数

官方 `:CALC:DATA:SDAT?` 返回 **N 个主值，随后 N 个次值**。服务拆成：

- `frequency_hz`：频率。
- `primary`、`secondary`：两个等长数组。
- `measurement`、`result_format`，阻抗模式另有 `z_parameter`：解释数据所需的元信息。

例如 SLIN 是线性幅值+相位，SLOG 是对数幅值+相位，SCOM 是实部+虚部。相位单位度；绝对通道测量返回 VRMS。不能一律把主值解释为 dB。单值格式的次数组可能全为 0。非有限数据用 JSON `null` 保留位置，不改写成 0。

默认每页最多 1000 点，`offset` / `limit` 可分页，每页上限 10000；返回 `total_points`、`returned_points`、`has_more`，不静默截断。每次读取都从设备取完整数组后分页；分页之间不要重新测量。当前不写结果文件或生成图表。

默认拒绝把旧缓存当新结果：需要本会话提交触发/内部启动并等待完成，且没有改测量设置。`allow_unverified=true` 可显式读取外部触发或已有缓存，返回的确认标记为 false；未实际测量过仍可能由官方接口报错或超时。该标记只是本会话跟踪，不保证其他客户端没改仪器。

结果格式转换不强制重新扫频。连续 INT/EXT 模式可能在读取两组数组之间更新缓冲区，因此专用读数工具拒绝该状态；BUS 连续等待触发不受此限制。

### 错误、并发和超时

- 同一连接串行操作，不允许两条查询串包。
- 专用设置工具在命令后检查官方错误队列；错误会以 MCP `isError=true` 和结构化 `error.code/message` 返回。
- 某项设置被拒绝时，先前已接受的设置**不回滚，也不自动重试**。
- 专用工具中的错误检查会消费队列，可能报告之前遗留的错误。`read_errors` 显式读并清空队列。
- 原始 `scpi_write` / `scpi_query` 不追加错误查询或等待，保留底层语义；“发送成功”不表示“设备接受并执行完成”。
- 原始查询也可能清状态、申请锁或触发自检，工具不标记为只读。原始调用会使本地测量确认失效。
- 超时后关闭 TCP，防止迟到响应被错配给下一次请求。**关闭连接不保证仪器停止**，重新连接后先看状态，不要盲目重试。
- `wait_for_completion` 使用 `*OPC?`，等待期间占用同一串行连接，不能同时插入 abort。长测量先用 `get_status` 查进度，再短等待；官方也不建议长时间占用 `*OPC?`。
- `disconnect` 和正常退出只释放锁、关连接，不擅自复位、中止或更改源输出。

### 校准文件

`save_calibration` / `load_calibration` 只传给官方接口，不由 MCP 本地直接读写文件。路径属于 **SCPI Server 所在电脑**，必须是绝对路径、ASCII 文本、`.mcalx` 后缀；父目录必须已存在。

官方 3.50 的保存页面对无路径文件名说明不一致，所以专用工具不依赖默认目录、不尝试备用路径。文件操作提交后显式调用 `wait_for_completion`。保存可能覆盖文件，应先确认目标；加载后用状态查询核对，不假定自动启用。

校准 `enabled`、`active`、`available` 是三个不同状态：允许使用、实际使用、完整可用。名义校准阻抗上限在官方页面有异常文本，本服务不凭经验硬编码上限，由设备返回是否接受。

## 6. 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

测试使用回环地址上的假 SCPI Server 和真实 MCP stdio 子进程，覆盖连接/设备锁、SCPI 映射、参数拒绝、触发、两个连续结果数组、分页、旧结果保护、异常数值、超时断连、错误恢复及工具 schema。

**这些测试不证明真实仪器通信和电气测量正确；当前尚未进行实机验证。** 测试不依赖官方驱动，不启动 BAS，也不连接 USB 或外网设备。

## 官方资料

- [自动化接口总览](https://www.omicron-lab.com/products/vector-network-analysis/bode-analyzer-suite/automation)
- [BAS 官方下载](https://www.omicron-lab.com/downloads/bode-analyzer-suite)
- [3.50 系统要求](https://documentation.omicron-lab.com/BodeAutomationInterface/3.50/2_SystemRequirement.html)
- [3.50 SCPI 文档](https://documentation.omicron-lab.com/BodeAutomationInterface/3.50/articlesScpi/general/SCPIOverview.html)
- [官方 SCPI Server 启动方式](https://documentation.omicron-lab.com/BodeAutomationInterface/3.50/articlesScpi/serverStarting/consoleRunner.html)
- [测量模式及默认设置](https://documentation.omicron-lab.com/BodeAutomationInterface/3.50/3_MeasurementModes.html)
- [Python 官方示例](https://documentation.omicron-lab.com/BodeAutomationInterface/3.50/articlesScpi/general/PythonEx.html)
