# 泰克示波器 MCP Server（tek-oscilloscope）

让 Claude（Claude Code / Claude Desktop）通过网口直接读取和控制实验室里的泰克示波器：

- **4/5/6 系列 MSO**：MSO44 及同系列，如 MSO46/MSO54/MSO56/MSO58/MSO64。
- **MDO3000 系列**：MDO3012/MDO3014、MDO3022/MDO3024、MDO3032/MDO3034、
  MDO3052/MDO3054、MDO3102/MDO3104（型号中的 O 为字母，不是数字 0）。

支持多台、不同系列的示波器混用：每次调用工具时传入对应 IP 即可。
存在代际差异的功能会通过 `*IDN?` 自动识别型号，不需要手工选择型号或修改配置格式。
MDO3000 支持本项目现有的模拟通道功能；两通道型号只允许 CH1/CH2。
新增支持已按官方命令资料核对并通过模拟测试。2026-10-16 已在 **MDO3054、固件 v1.30**
实机验证型号查询、PNG 截图、CH1 波形及即时测量读取，仪器事件队列为空。
数据传输和即时测量设置在测试后已恢复并回读确认；通道显示、挡位、时基、触发源及采集状态未变。
其他 MDO3000 型号/固件及通道、触发、时基、采集控制写操作尚未实机验证。

---

## 1. 整体架构

```
Claude ── (MCP协议, stdio) ──> server.py ──> scope_commands.py ──> scope_client.py ──(网口SCPI)──> 示波器
                                  │                  │                     │
                             注册MCP工具        具体功能实现           TCP socket通信
                            (对Claude暴露)     (截图/波形/测量/控制)   (不依赖NI-VISA)
```

三层职责分离，互不干扰：

| 文件 | 职责 | 什么时候需要改它 |
|---|---|---|
| `scope_client.py` | 最底层：TCP连接、发命令、收数据（含二进制块解析） | 几乎不用改，除非换传输方式 |
| `scope_commands.py` | 具体功能：截图/波形/测量/通道/触发/采集/时基 | **SCPI命令语法不对时改这里** |
| `server.py` | 把上面的功能注册成 MCP 工具，供 Claude 调用 | 想加/删"工具"（新功能）时改这里 |

**通信方式**：普通命令通过泰克示波器的 raw socket SCPI（端口 4000）发送，
使用 Python 标准库 TCP socket。截图通过 `pyvisa` 和 `pyvisa-py` 使用 VXI-11
传输，不依赖系统 NI-VISA；请同时确认示波器侧允许这两种网络通信。

---

## 2. 安装

### 2.1 示波器一侧（一次性设置，每台示波器都要做）

1. 示波器菜单 → Utility → I/O，确认已连网并有固定/可预期的 IP
   （建议用固定IP，不然示波器重启后IP可能变，配置文件就要跟着改）
2. 确认 Socket Server（网口 SCPI 原始通信，端口4000）是打开的
   ——4/5/6系列出厂一般默认打开，如果连不上，第一个要查的就是这个开关
3. **MDO3000**：将 Socket Server 端口设为 **4000**，协议设为 **NONE**，
   不要使用会插入欢迎语/提示符的 TERMINAL 模式；截图还需要网络 VXI-11 可用

### 2.2 电脑一侧

建议使用 **64 位 Python 3.10+**。Windows 上如果同时装了 32 位和 64 位 Python，
先用 `py -0p` 找到 64 位解释器，再创建虚拟环境；否则 `cryptography` 等依赖可能没有
对应的 32 位 wheel，安装会失败。

```powershell
# 先进入克隆仓库中的 SCOPE_MCP 目录
py -0p
# 将下面的路径替换成 py -0p 输出的 64 位 Python 路径
& "C:\Path\To\64-bit\python.exe" -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
# 如果项目里还没有 scopes.json，再从示例复制一份
if (-not (Test-Path scopes.json)) { Copy-Item scopes.example.json scopes.json }
# 编辑 scopes.json，填入你实验室里所有示波器的真实IP
```

仓库只提供 `scopes.example.json`，其中使用文档专用示例地址；复制后必须替换成真实设备 IP。
本机的 `scopes.json` 和截图目录已被 Git 忽略。也可以直接在工具调用中传入 `ip`。

**Python版本**：建议 3.10+（必须使用 64 位 Python）。

### 2.3 先跑独立测试脚本（强烈建议，别跳过这步）

在接入 MCP / 接入 Claude 之前，先用这个脚本单独验证连接和每个命令是否正常：

```bash
.\.venv\Scripts\python.exe test_connection.py 192.0.2.1
```

将示例地址替换为真实设备 IP。**这是实机测试，不是无硬件单元测试**，
会依次执行连接、截图、波形读取、测量值读取、通道设置，改变部分仪器状态。
确认设备、接线和操作者授权后再运行。如果某一步报错，检查 `scope_commands.py` 中对应的 SCPI 命令，
调通了再去接 Claude —— 这样出问题时排查范围小很多。

不连接设备的单元测试（使用模拟 SCPI 响应，不会操作真实仪器）：

```powershell
& .\.venv\Scripts\python.exe -m unittest discover -v
```

### 2.4 接入 Claude Code

推荐使用 Claude Code 命令注册为**用户级（全局）** MCP 服务，这样所有项目都能使用，
并且固定使用虚拟环境里的 Python，不依赖系统默认 Python：

```powershell
$python = (Resolve-Path ".\.venv\Scripts\python.exe").Path
$server = (Resolve-Path ".\server.py").Path
claude.cmd mcp add --scope user --transport stdio tek-oscilloscope -- "$python" "$server"
claude mcp list
```

看到 `tek-oscilloscope ... Connected` 后，重启 Claude Code（或重新加载 MCP），
就能在所有项目的对话里直接让 Claude 操作示波器。移除全局注册：

```powershell
claude mcp remove --scope user tek-oscilloscope
```

---

## 3. 配置文件 `scopes.json`

```json
{
  "scopes": [
    "192.0.2.1",
    "192.0.2.2",
    "192.0.2.3"
  ]
}
```

服务**直接用 IP 区分设备，没有别名**。加/换示波器只需要改这个文件，
不用碰代码。每个工具调用时都要传 `ip` 参数，Claude 会先调用 `list_scopes`
看看有哪些已知IP，你也可以直接口头告诉Claude用哪个IP。

---

## 4. 工具（能力）清单

| 工具 | 说明 | 关键参数 |
|---|---|---|
| `list_scopes` | 列出配置文件里的示波器IP | 无 |
| `get_idn` | 连接测试，查询型号/序列号/固件版本 | `ip` |
| `get_screenshot` | 截取当前屏幕，存成PNG | `ip` |
| `get_waveform` | 读取原始采样数据（时间+电压数组） | `ip`, `channel`, `max_points` |
| `get_measurement` | 读取自动测量值 | `ip`, `channel`, `meas_type` |
| `set_channel` | 通道开关/挡位/耦合/位置 | `ip`, `channel`, `enabled`, `scale`, `coupling`, `position` |
| `set_trigger` | 边沿触发：源/电平/模式/斜率 | `ip`, `source`, `level`, `mode`, `slope` |
| `run_acquisition` | 采集控制：运行/停止/单次触发 | `ip`, `action` (`run`/`stop`/`single`) |
| `set_horizontal` | 时基：秒/格、水平位置 | `ip`, `scale`, `position` |

`meas_type` 常见取值：`FREQUENCY` `PERIOD` `PK2Pk` `AMPlitude` `MEAN` `RMS`
`RISetime` `FALltime` `PWIDth` `NWIDth` `MAXimum` `MINImum` `HIGH` `LOW` `OVERshoot`

---

## 5. SCPI 命令参考（每个工具具体发了什么命令）

| 工具 | 用到的SCPI命令 | 可信度 |
|---|---|---|
| `get_idn` | `*IDN?` | ✅ 标准命令，各泰克机型通用 |
| `get_screenshot` | VXI-11：`SAVE:IMAGE:FILEName` → `SAVE:IMAGE` → `*OPC?` → `FILESystem:READFile` → `FILESystem:DELEte` | ✅ `READFile` 返回原始 PNG 字节；截图走 VXI-11，其他命令仍走 raw socket |
| `get_waveform` | `DATa:SOUrce` `DATa:ENCdg ASCIi` `DATa:STARt/STOP` `WFMOutpre:XINcr/XZEro/YMUlt/YZEro/YOFf` `CURVE?` | ✅ 已核实（含第三方 `pyMSO4` 库实测过 `CURVE?` 用法） |
| `get_measurement` | `MEASUrement:MEAS1:SOUrce1/TYPe/STATE/VALue?` | ⚠️ 按4/5/6系列新版"测量槽位"架构的通用命名规律实现，**未逐字核实**，见下方排查章节 |
| `set_channel` | `DISplay:GLObal:CHx:STATE` `CHx:SCAle` `CHx:COUPling` `CHx:POSition` | ⚠️ `SCAle`/`COUPling`/`POSition` 是泰克跨机型通用命令，可信度高；`DISplay:GLObal:CHx:STATE` 是4/5/6系列命名规律，建议实机验证 |
| `set_trigger` | `TRIGger:A:TYPe/EDGE:SOUrce/EDGE:SLOpe/LEVel/MODe` | ✅ 泰克A事件边沿触发的标准命名 |
| `run_acquisition` | `ACQuire:STOPAfter` `ACQuire:STATE` | ✅ 泰克通用命令 |
| `set_horizontal` | `HORizontal:SCAle` `HORizontal:POSition` | ✅ 泰克通用命令 |

MDO3000 与上表不同的命令：

| 功能 | MDO3000 指令 / 行为 |
|---|---|
| 截图 | VXI-11：`SAVE:IMAGe:FILEFormat PNG` → `HARDCopy STARt` → 原始字节读取；随后恢复原格式，不写仪器存储文件 |
| 自动测量 | `MEASUrement:IMMed:SOUrce1` → `MEASUrement:IMMed:TYPe` → `*OPC?` → `MEASUrement:IMMed:VALue?` |
| 通道显示开关 | `SELect:CHx ON/OFF`（仪器同时重置采集，开启时会选中该波形） |
| 边沿触发电平 | `TRIGger:A:LEVel:CHx`；不传 `source` 时查询当前边沿触发源。若当前源不是模拟通道，需显式传入有效的 CH1–CH4 |
| 波形 | 仍用 `DATa:ENCdg ASCIi`、`WFMOutpre` 和 `CURVE?`；单次最多 100 万点，时间轴使用 `XZERO + index * XINCR` |

官方完整命令手册（4/5/6 Series MSO Programmer Manual）：
https://download.tek.com/manual/4-5-6Series-MSO-Programmer-Interface-Manual_EN-US_077130508.pdf

MDO3000 官方编程手册（与 MDO4000 等系列共用，077-0510-09 Rev A）：
https://download.tek.com/manual/MDO4000-B-C-MSO-DPO4000B-and-MDO3000-Oscilloscope-Programmer-Manual-Revision-A-077051009.pdf

关键章节：截图 2-331～332，通道耦合 2-221～222，即时测量 2-424～431，
通道开关 2-755，触发电平 2-878，波形前导 2-959～971。
上述指令依据官方资料核对，实机验证范围见文首；模拟测试不代替其他型号/固件的实机验证。

---

## 6. 排查指南（重点看这里，标 ⚠️ 的地方最可能需要你动手调整）

### 6.1 万用排查法：`check_error()`

`scope_client.TekScope` 有个 `check_error()` 方法，读取示波器的错误队列。
任何一条命令怀疑发错了，照这个模板在Python里试：

```python
from scope_client import TekScope
with TekScope("192.0.2.1") as scope:
    scope.write("MEASUrement:MEAS1:TYPe FREQUENCY")   # 换成你想测试的命令
    print(scope.check_error())   # 如果命令语法不对，这里会打印出示波器的报错信息
```

示波器会直接告诉你是"命令不认识"还是"参数不对"，比瞎猜快得多。

### 6.2 如果 `get_measurement` 报错

以下针对原有 **4/5/6 系列 MSO** 测量槽位命令（见上表⚠️标注）；
MDO3000 使用 `MEASUrement:IMMed:SOUrce1/TYPe/VALue?`，不发送 `STATE ON`，
也不覆盖面板上的 MEAS1。对现有接口的 `RISetime/FALltime/OVERshoot`，
MDO3000 分别发送 `RISE/FALL/POVERSHOOT`（正过冲）；负过冲可显式传入 `NOVERSHOOT`。
约 `9.9E37` 的不可测量哨兵会报告错误，不作为有效测量值返回。

MSO 排查步骤：

1. 用上面的 `check_error()` 方法，把 `scope_commands.py` 里
   `get_measurement` 函数中的四条命令逐条单独跑一遍，看哪一条报错
2. 常见的备选写法（如果 `VALue?` 不对，试试这些）：
   - `MEASUrement:MEAS1:RESUlts:CURRentacq:MEAN?`
   - `MEASUrement:MEAS1:RESUlts:ALLAcqs:MEAN?`
3. 改好之后，把新命令替换进 `scope_commands.py` 的 `get_measurement` 函数即可，
   其他文件不用动

### 6.3 如果 `set_channel` 里的开关命令报错

4/5/6 系列 MSO 使用原有的 `DISplay:GLObal:CH1:STATE ON`；
MDO3000 自动改用 `SELect:CH1 ON`。可用 `check_error()` 读取仪器事件队列。
两系列的耦合均为 `AC/DC/DCREJect`，不支持 `GND`；`DCREJect` 需要探头支持，
否则仪器可能返回 settings conflict，实际耦合不变，需检查返回的 `scope_errors`。
非法耦合或两通道 MDO 上的 CH3/CH4 会在发送设置命令前报错。

### 6.4 截图打不开 / 是空文件

- **MSO44**：`SAVE:IMAGE` 是异步操作，保存后查询 `*OPC?` 并确认返回 `1`，
  不能仅依靠固定延时。文件名通过 `SAVE:IMAGE:FILEName` 配置，随后不带参数
  发送 `SAVE:IMAGE`；`FILESystem:READFile` 返回原始文件字节，不是 IEEE 488.2 长度块。
- **MDO3000**：设置 `SAVE:IMAGE:FILEFORMAT PNG` 后，发送 `HARDCOPY START` 并原始读取图像，
  不使用 `SAVE:IMAGE:FILENAME`，不需要 U 盘。注意不能省略 `START`，否则会输出到打印机。
  读取后尝试恢复原截图格式；代码校验 PNG 文件头和 IEND，拒绝空文件或截断数据。

截图传输均使用 VXI-11（`TCPIP0::<IP>::inst0::INSTR`），需要 `pyvisa` 与 `pyvisa-py`；
其余 SCPI 命令继续使用端口 4000 的 raw socket。已在 MDO3054 v1.30 上确认直接返回完整 PNG，
无需解析 IEEE 488.2 块头；其他型号/固件仍需实机验证。

### 6.5 波形数据读取很慢

`get_waveform` 默认用 ASCII 编码传输（简单可靠但慢），且默认限制
`max_points=100_000`。MDO3000 单次 ASCII 传输上限为 100 万点，传入更大的
`max_points` 也只会读取前 100 万点。源通道须已经显示，本工具不会为读取而擅自开启通道。
如果需要更快或更多点数：
- 减少 `max_points`（比如只要5000点看形状足够了）够快
- 如果需要又快又多点，需要改成二进制编码传输（`DATa:ENCdg SRIBINARY`），
  但要处理字节序和数据宽度，改动量较大，目前版本先不做，按需再升级

### 6.6 MCP SDK 报错 `No module named 'mcp.server.fastmcp'`

说明装到了 mcp 2.x 版本。本项目的 `requirements.txt` 已锁定 `mcp>=1.28,<2`，
正常 `pip install -r requirements.txt` 不会踩到。如果手动 `pip install mcp`
装到了最新版，回退一下：
```bash
pip install "mcp>=1.28,<2" --force-reinstall --no-deps
```

---

## 7. 已知限制

- **MSO 测量槽位共用**：4/5/6 系列的 `get_measurement` 固定用 `MEAS1` 这个槽位；
  MDO3000 使用独立即时测量，不占用该槽位。如果有人
  同时在示波器面板上手动配置了 MEAS1，会互相覆盖。如果需要，可以在
  `scope_commands.py` 里把 `_TEMP_MEAS_SLOT` 改成固定用 `MEAS8`（示波器
  面板上人工操作一般不会用到最后一个槽位），减少冲突概率
- **无安全确认机制**：服务内所有写操作（改通道/触发/时基/采集）
  直接执行，不会先询问确认。多人共用同一台示波器时请注意
- **无并发保护**：如果同时有两个Claude会话操作同一台示波器，可能互相干扰
- **波形数据默认用ASCII编码**：简单可靠但传输大量点数时较慢（见6.5）

## 8. 可能的后续扩展方向（暂未实现）

- 二进制波形传输（提速）
- 数字通道 (D0-D15) 支持
- FFT / 频谱相关命令
- 多示波器批量截图/批量测量（一次调用操作多台）
