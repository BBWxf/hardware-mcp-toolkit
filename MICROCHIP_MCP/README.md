# Microchip IPE / PICkit 4 MCP

Windows 本地 stdio MCP 服务，使用 **MPLAB X IPE v6.00 附带的官方 IPECMD** 操作 PICkit 4。不逆向 GUI、不调用内部 Java API、不监听网络端口，不需要模型 API Key。

**启动、查看配置、检查环境、预览命令均不运行 IPECMD。** 只有显式调用编程器枚举或目标操作工具才启动厂商程序。烧录、擦除、供电、复位以及可能发生的编程器固件更新会影响真实硬件，操作前必须确认目标和接线。

## 环境与安装

- Windows、Python 3.10+。
- 完整安装 MPLAB X/IPE 及对应器件包、PK4 驱动。不要单独搬运 `ipecmd.exe` / `ipecmd.jar`。
- PK4 支持的目标芯片和正确接线；供电能力及允许电压以 PK4、芯片及目标板规格为准。

在本项目目录安装依赖，并在首次安装时复制配置模板。**已有 `config.json` 时请保留，不要重新执行复制命令：**

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install --only-binary=:all: -r requirements.txt
Copy-Item config.example.json config.json
```

先将 `config.json` 中的 `ipe.executable_path` 改为本机实际安装路径，再启动：

```powershell
.venv/Scripts/python.exe server.py
```

启动后等待客户端输入是正常行为。标准输出只用于 MCP 协议，厂商子进程的输出会被捕获，不直接透传到协议流。Windows 的 `cryptography` 依赖固定为提供 x86 wheel 的版本，兼容本机可能使用的 32 位 Python；MCP 不在进程内加载 Microchip DLL，Python 无需与 IPE 的 Java 位数一致。

## 配置文件

仓库只提供 **`config.example.json`**；复制得到的现场配置 **`config.json`** 已被 Git 忽略，避免发布真实序列号和固件路径。默认读取脚本同目录的 `config.json`，与客户端当前工作目录无关。也可通过启动参数选择：

```powershell
.venv/Scripts/python.exe server.py --config "D:/configs/microchip.json"
```

每次工具调用重新读取配置，修改后无需重启。调用参数中的非 `null` 值只覆盖本次请求，不回写文件。文件及日志的相对路径相对于配置文件目录解析。

这是 **MCP 自己的配置格式**，不能直接导入 IPE；不读取、不继承 IPE GUI 当前会话。选项映射参照 IPECMD，未指定的高级选项由厂商工具按器件处理。

### 必须填写的值

仓库模板不猜测真实芯片、固件、电压及工具序列号。目标操作前请填写：

- `programmer.serial_number`：PK4 序列号，可从 IPE 或 `list_programmers` 查看。本服务必须明确指定，不自动选择第一台或弹出交互菜单。
- `target.device`：完整芯片型号。
- `target.voltage_v`：由 PK4 供电时选择 `3.3` 或 `5.0`。必须符合芯片及目标板规格，不会自动试电压。
- `programming.hex_file`：烧录或文件校验需要的 HEX 路径。
- `readback.hex_file`：读回时需要的输出 HEX 路径，也可由调用参数 `output_file` 提供。

例如下面的数值仅演示填写方式，**不代表你的目标板可用**：

```json
{
  "programmer": {"type": "PK4", "serial_number": "替换为真实PK4序列号"},
  "target": {"device": "PIC16F877", "voltage_v": 5.0},
  "programming": {"hex_file": "D:/firmware/app.hex"}
}
```

请修改现有完整 `config.json` 对应字段，不要用这个局部片段直接替换整个文件。

### 字段与 IPECMD 映射

| 字段 | 默认/模板值 | 行为 |
| --- | --- | --- |
| `ipe.executable_path` | v6.00 默认安装路径示例 | 改为本机实际 `ipecmd.exe` 路径，必须存在。 |
| `ipe.timeout_seconds` | `180` | 单次进程超时，允许 1–3600 秒。 |
| `ipe.output_encoding` | `null` | 解码厂商输出时使用系统首选编码；乱码时可明确指定 `gbk` / `utf-8`。不改变 MCP 的 UTF-8 协议编码。 |
| `ipe.logs_directory` | `logs` | 保存每次厂商程序调用的完整 JSON 日志。 |
| `ipe.dfp` | `null` | 不强制指定包；厂商按安装状态选择。可指定下文的名称/版本/厂商三元组。 |
| `programmer.type` | `PK4` | 固定映射 `-TPPK4`，目前不支持其他烧录器。 |
| `programmer.serial_number` | `null` | 目标操作必填，映射 `-TS<序列号>`。 |
| `target.device` | `null` | 目标操作必填，映射 `-P<器件名>`；PIC/dsPIC 前缀按 IPECMD 文档去除，其他名称原样传入。 |
| `target.power_from_programmer` | `true` | 本项目按需求由 PK4 供电；为 `false` 时不发送 `-W`，使用外部供电。 |
| `target.voltage_v` | `null` | 工具供电时必填，仅允许 3.3/5V，映射 `-W3.3` / `-W5`；不会发送含糊的裸 `-W`。 |
| `target.release_from_reset` | `false` | 不添加 `-OL`，采用 IPECMD 默认复位策略；`true` 才添加 `-OL`。具体器件状态以厂商行为为准。 |
| `target.interface` | `null` | 不强制覆盖。可为 `JTAG/SWD/UPDI/ISP/TPI/PDI/HVPP/HVSP`，映射 `-ORI<值>`；只能选择该器件/工具组合支持的接口。 |
| `target.speed_mhz` | `null` | 不强制覆盖；正数、单位 MHz，对应 `-ORS<值>`。合法档位由 IPECMD 判断，不自动改速重试。 |
| `programming.hex_file` | `null` | 输入 HEX，映射 `-F<路径>`；目前不接受 BIN/ELF。 |
| `programming.memory_region` | `all` | `all` 使用 `-M` / `-Y`；也可为 `P/E/I/C/A/B`。区域适用性由器件决定。 |
| `programming.verify_after_program` | `null` | 不追加独立校验；`true` 才追加 `-Y[区域]`。`false/null` 不会关闭 IPECMD 烧录内部校验。 |
| `readback.hex_file` | `null` | 读回输出，对应 `-GF<路径>`。实际先写临时文件，成功后才发布到指定路径。 |

指定已安装 DFP 的配置示例：

```json
"dfp": {
  "name": "PIC12-16F1xxx_DFP",
  "version": "1.2.63",
  "vendor": "Microchip"
}
```

该示例仅说明格式，必须替换为实际适用且已安装的包。生成 `-OWDPIC12-16F1xxx_DFP,1.2.63,Microchip`，不是自定义 DFP 目录路径。

`null` 的含义按上表逐项定义，不代表从 GUI 自动读取。未知字段、错误类型及缺少必需参数会报错，不静默忽略。配置中不提供任意命令行透传。

## MCP 工具（9 个）

| 工具 | 功能 |
| --- | --- |
| `get_environment` | 静态检查安装路径、JAR 版本和超时阻断状态；不访问 USB。 |
| `get_configuration` | 返回配置路径及当前配置；不修改文件。 |
| `list_programmers` | 运行 `-T`，列已连接工具和序列号；访问 USB，但不指定目标器件。保留原始输出，解析结果仅供参考。 |
| `preview_operation` | 对 `program/verify/erase/blank_check/read` 生成命令及有效配置；不运行厂商程序。 |
| `program_device` | `-M[区域]` 烧录 HEX，按 IPECMD 自身的擦除/校验流程执行。 |
| `verify_device` | `-Y[区域]` 独立校验 HEX。 |
| `erase_device` | `-E` 擦除；范围和保护设置影响由具体器件决定，不接受区域擦除参数。 |
| `blank_check` | `-C` 空白检查，不需要 HEX。 |
| `read_device` | `-GF` 读回 HEX；不绕过代码保护。默认拒绝覆盖已有文件。 |

目标操作均可在调用中覆盖 `device`、`serial_number`、`voltage_v`、`power_from_programmer`、`release_from_reset`、`timeout_seconds`。烧录/校验还可覆盖 `hex_file`、`memory_region`；烧录可覆盖 `verify_after_program`；读回使用 `output_file` 和 `overwrite`。接口、速度及 DFP 在配置中设置。

### 建议调用顺序

1. `get_environment`：确认安装可用。
2. `get_configuration`：检查真实型号、电压和固件是否已填写。
3. 如需查询 PK4 序列号，显式调用 `list_programmers` 并填写配置。
4. `preview_operation(operation="program")`：核对命令和有效配置。
5. 获得本次真实目标操作授权后调用 `program_device`。

工具元数据不是权限控制器，硬件操作及文件覆盖授权由 MCP 客户端/操作者管理。本服务不在启动或测试时自动调用任何目标操作。

## 返回结果与运行边界

- `success`：退出码为 0，未发现已知失败提示，且识别到该操作的完成标记。读回还要求生成非空输出文件。
- `failed`：非零退出码、已知失败提示、启动失败或读回文件发布失败。完整原始输出和退出码会保留。
- `unconfirmed`：退出码为 0，但没有识别到已知完成标记；作为 MCP 工具错误返回，**不能当作烧录成功**。以完整日志和硬件联调为准。
- `timeout`：已尝试终止 EXE/Java 进程树；供电、复位及目标内容状态未知，不自动重试或恢复。当前服务的后续硬件调用被阻断；目标操作超时还写入临时目录的 `recovery_file`，防止其他 MCP 进程再次操作同一序列号。只有检查 PK4、目标板及残留进程并确认可以继续后，才手动删除返回路径对应的阻断文件并重启服务；单纯重启不会清除跨进程阻断。
- 枚举成功退出返回 `completed`，不代表 PK4 已成功连接目标芯片。

每次厂商调用返回 `command`、`exit_code`、`stdout`、`stderr`、耗时及 `log_file`。目标操作还返回有效配置。MCP 响应的单路输出超过 32000 字符时截断，JSON 日志中保留完整内容；日志可能包含本地固件路径和工具序列号，请自行管理。

本服务同一时间只执行一项厂商操作；跨本机 MCP 进程对相同 PK4 序列号使用文件锁。文件锁不会阻止 IPE GUI、其他厂商工具或不同 Windows 用户会话访问同一硬件，调用前仍须释放其他会话占用。

读回先在输出文件同目录创建临时目录，识别成功且有非空 HEX 后才发布；已有文件默认拒绝，必须在获得明确覆盖授权后设置 `overwrite=true`。失败或未确认成功时不覆盖旧文件。不会自动创建输出文件的父目录。

**不承诺 Microchip 所有芯片都可烧录。** 范围是 PK4、当前 IPE 版本及已安装器件包共同支持的器件；不支持时报告原始错误，不切换相近型号、不尝试其他电压，也不绕过代码保护。

烧录使用 `-M`，不额外强制执行一次 `-E`。全器件与区域烧录的擦除行为存在差异；不把部分区域烧录伪装成全擦除。独立 `-Y` 也可能重复 IPECMD 已执行的内部校验。

`-V` 是 PM3 的 VPP 选项，不是 PK4 VDD 或版本查询。首次工具操作可能自动更新编程器固件；读取/校验同样可能影响供电和复位，不能当作无副作用的查询。安装检查及预览不会验证硬件兼容性。

## 接入 MCP 客户端

`mcp-config.example.json` 使用 `C:/path/to/hardware-mcp-toolkit/MICROCHIP_MCP/` 作为占位路径。必须将 `command` 和 `args` 改为本机实际克隆位置，并先创建 `config.json`，再将服务器条目加入所用客户端的 MCP 配置。**服务启动不会自动修改实际客户端设置或全局注册。**

Claude Code 本项目注册示例（安装完成后按需执行）：

```powershell
$python = (Resolve-Path ".venv/Scripts/python.exe").Path
$server = (Resolve-Path "server.py").Path
$config = (Resolve-Path "config.json").Path
claude.cmd mcp add --scope local --transport stdio microchip-ipe -- "$python" "$server" --config "$config"
```

如确需跨项目使用，将 `--scope local` 改为 `--scope user`。不要同时通过 JSON 和命令行重复注册同名服务。

## 测试

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -v
```

自动测试使用临时配置、假 EXE 和模拟子进程，覆盖命令映射、参数拒绝、失败/超时、并发锁、日志、读回覆盖保护以及真实 MCP stdio 初始化和静态工具调用。**这些测试不运行 IPECMD，不证明真实 PK4 烧录链路已通过。**

## 实机验证范围

已在 Windows、MPLAB X/IPE v6.00、PICkit 4、`dsPIC33FJ64GS606`、设置 3.3V 的组合上完成一次 HEX 烧录及 IPECMD 内部校验，退出码为 0。该结果不代表所有器件、操作或应用运行功能均已验证。

本机 `-T` 曾在列出 PICkit 4 后返回非零退出码 `50`；服务保留该退出码并报告 `failed`，不会仅因解析出序列号就改判成功。尚未确认该返回码的具体含义。

## 本机参考资料

- `C:/Program Files/Microchip/MPLABX/v6.00/mplab_platform/mplab_ipe/modules/ext/Readme for IPECMD.htm`。
- `ipecmd.jar` 的版本资源 `com/microchip/mplab/ipecmd/common/ipeproperty.properties`，版本值 `IPECMD.VERSION = v6.00`。
- IPECMD 与 PK4Tool 的英文资源字符串用于保守判断完成/失败；未识别的输出不报告成功。
- 随附 HTML 有旧版本内容及占位符，具体器件行为仍需用实际安装、器件包和目标板验证。
