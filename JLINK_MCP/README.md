# J-Link 固件烧录 MCP Server

这是一个运行在 Windows 上的 stdio MCP Server。它让 Claude Code 通过 SEGGER J-Link 对单片机执行**烧录**操作；功能范围仅限配置工程、擦除、烧录、校验、复位，不包含调试、RTT 或寄存器读写。

> 烧录、擦除和复位会直接改变已连接硬件状态。确认目标板、固件和芯片型号正确后再调用 `flash_firmware`。

## 前置条件

- Windows、Python 3.10 或更高版本。
- 已安装 [SEGGER J-Link Software and Documentation Pack](https://www.segger.com/downloads/jlink/)，其中必须有 `JLink.exe` 和 `JLinkARM.dll`。
- J-Link 探针与目标板已按 SWD 或 JTAG 正确连接。

## 安装

先进入本仓库的 `JLINK_MCP` 目录，确认 `JLinkARM.dll` 的位数，再选择相同位数的 Python。以下路径为占位示例，需替换成实际解释器路径：

```powershell
py -0p
# 32 位 DLL 使用 32 位 Python；64 位 DLL 使用 64 位 Python
& "C:\path\to\python.exe" -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

本项目固定使用 `pylink-square==2.0.1`。PyLink 是 `JLinkARM.dll` 的 Python 封装；其 DLL 显式加载、`connect`、`erase`、`flash_file` 和进度回调 API 参考 [PyLink 官方 API 文档](https://pylink.readthedocs.io/en/latest/pylink.html)。若 DLL 是 64 位，应改用 64 位 Python；位数不一致会出现 `WinError 193`。

## 配置文件

此服务是用户级全局服务，配置**不在工程目录**中。首次启动 Server、调用 `list_projects`，或运行手测脚本时，会自动创建：

```text
%USERPROFILE%\.jlink_mcp\config.json
```

例如：`C:\Users\<用户名>\.jlink_mcp\config.json`。

首次创建后，工具会明确返回配置文件路径。请编辑模板中的示例值并填入真实的**绝对路径**；每次工具调用都会重新读取该文件，因此新增或修改工程后无需重启 MCP Server。

```json
{
  "jlink_exe_path": "C:\\Program Files\\SEGGER\\JLink\\JLink.exe",
  "jlink_dll_path": "C:\\Program Files\\SEGGER\\JLink\\JLinkARM.dll",
  "projects": {
    "motor_control": {
      "firmware_path": "D:\\Projects\\MotorControl\\Debug\\firmware.hex",
      "device": "STM32F103C8",
      "interface": "SWD",
      "speed": 4000
    },
    "led_blink": {
      "firmware_path": "D:\\Projects\\LedBlink\\Debug\\firmware.hex",
      "device": "STM32F407VE",
      "interface": "SWD",
      "speed": 4000
    }
  }
}
```

字段说明：

| 字段 | 含义 |
| --- | --- |
| `jlink_exe_path` | `JLink.exe` 的绝对路径，Commander 后端使用；J-Flash 后端从同目录查找 `JFlash.exe`。 |
| `jlink_dll_path` | `JLinkARM.dll` 的绝对路径，最后的 PyLink 后备后端使用。 |
| `projects` | 以用户自定义工程别名为 key 的对象。 |
| `firmware_path` | 固件绝对路径，只支持 `.hex`、`.bin`、`.elf`。 |
| `device` | SEGGER 可识别的目标芯片名称，例如 `STM32F103C8`。 |
| `interface` | `SWD` 或 `JTAG`。 |
| `speed` | 接口速率，单位 kHz，例如 `4000`。 |
| `jflash_project_path` | 可选；已由 J-Flash GUI 创建并验证过的 `.jflash` 工程绝对路径。配置后优先使用 JFlash 自动烧录。 |

即使只想使用 Commander 后端，两个 J-Link 路径也都必须在配置中有效；这样可以在调用前明确发现安装或配置问题。

## 注册为 Claude Code 全局 MCP 服务

依赖安装完成后，使用实际的 Python 路径注册。若使用上面的虚拟环境，推荐：

```powershell
$python = (Resolve-Path ".\.venv\Scripts\python.exe").Path
$server = (Resolve-Path ".\server.py").Path
claude.cmd mcp add --scope user --transport stdio jlink-flash -- "$python" "$server"
```

也可以使用系统 Python：

```powershell
$server = (Resolve-Path ".\server.py").Path
claude.cmd mcp add --scope user --transport stdio jlink-flash -- python "$server"
```

`--scope user` 必须位于 `--` 之前。注册完成后可执行：

```powershell
claude mcp list
```

确认 `jlink-flash` 已存在。该注册信息由 Claude Code 写入 `%USERPROFILE%\.claude.json`；它与本 MCP 自己读取的 `%USERPROFILE%\.jlink_mcp\config.json` 是**两个不同文件**：

- `.claude.json`：Claude Code 的 MCP 服务注册表，通常无需手工编辑。
- `.jlink_mcp\config.json`：J-Link 安装路径和工程固件配置，需要由用户维护。

## MCP 工具

### `list_projects`

无参数。返回所有已登记的工程别名及完整配置，并包含实际使用的 `config_path`。

在 Claude Code 中可直接请求：

```text
调用 jlink-flash 的 list_projects，列出已登记的固件工程。
```

### `flash_firmware`

参数：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `project_name` | string，可选 | 配置文件中的工程别名。 |
| `firmware_path` | string，可选 | 固件绝对路径；传入时覆盖工程默认值。 |
| `device` | string，可选 | 目标芯片型号；传入时覆盖工程默认值。未从工程取得时必填。 |
| `interface` | string，可选 | `SWD` 或 `JTAG`；优先显式值，再取工程默认，最后为 `SWD`。 |
| `speed` | integer，可选 | kHz；优先显式值，再取工程默认，最后为 `4000`。 |
| `base_address` | string，可选 | 仅 `.bin` 必填，如 `0x08000000`。 |
| `erase_before_flash` | boolean，可选 | 默认 `true`，烧录前全片擦除。 |
| `reset_after_flash` | boolean，可选 | 默认 `true`，完成后复位并运行。 |

**通过工程别名烧录：**

```text
调用 jlink-flash 的 flash_firmware，project_name 使用 motor_control。
```

**用显式路径烧录：**

```text
调用 jlink-flash 的 flash_firmware：
firmware_path = D:\Builds\demo.bin
base_address = 0x08000000
device = STM32F407VE
interface = SWD
speed = 4000
erase_before_flash = true
reset_after_flash = true
```

成功结果是 JSON 对象，包含 `duration_seconds`、`bytes_written`、`target_device`、`project_name`、后端名称和校验状态。失败结果包含 `stage`（如 `read_config`、`connect`、`erase`、`flash`、`verify`）以及原始底层错误 `details.raw_error`。

## 后端与校验逻辑

1. **可选 J-Flash 后端：** 工程配置了 `jflash_project_path` 时，调用同目录 `JFlash.exe` 的 `-openprj`、`-open`、`-auto`、`-exit`。该项目必须先通过 J-Flash GUI 创建并验证，保存目标芯片、接口、速度及 Flash 设置；此后端失败时会继续尝试 Commander。
2. **两段式 Commander 后端：** 未配置 J-Flash 工程时，先在一个独立的 `JLink.exe` 会话中复位、halt、擦除并退出；再在新的会话中连接、下载、校验、复位。这与 J-Flash Lite 的“擦除后重新连接并下载”流程一致。
   - `.hex` / `.elf`：使用 J-Link Commander 的 `LoadFile`（该命令默认校验）。
   - `.bin`：使用 `LoadFile` 后追加 `VerifyBin`。
3. **PyLink DLL 后备：** Commander 失败时，使用配置中的 `jlink_dll_path` 显式加载 `JLinkARM.dll` 后调用 PyLink；烧录进度必须报告 `Verify` 阶段才会返回成功。

J-Flash 命令行参数以 [SEGGER J-Flash 用户手册](https://kb.segger.com/UM08003_JFlash) 为准；`LoadFile` 和 `VerifyBin` 行为以 [SEGGER J-Link Commander 官方命令文档](https://kb.segger.com/J-Link_Commander) 为准。

## 常见错误

| 错误代码或阶段 | 处理建议 |
| --- | --- |
| `configuration_required` | 编辑工具返回的 `%USERPROFILE%\.jlink_mcp\config.json` 模板。 |
| `invalid_config_json` | 修复配置文件 JSON 语法。 |
| `invalid_jlink_paths` | 检查 `JLink.exe`、`JLinkARM.dll` 是否存在且为绝对路径。 |
| `project_not_found` | 使用 `list_projects` 查看可用别名。 |
| `missing_device` | 显式传 `device` 或补充工程配置；服务不会猜测芯片型号。 |
| `firmware_not_found` / `unsupported_firmware_format` | 检查固件绝对路径和扩展名。 |
| `missing_base_address` | 对 `.bin` 指定十六进制加载地址。 |
| `connect` | 检查 J-Link USB、目标板供电、SWD/JTAG 接线、芯片型号、接口和速度。 |
| `verify` | 校验数据不一致；检查供电、接线、Flash 保护和固件是否匹配芯片。 |

## 不通过 MCP 的手动测试

手测脚本直接调用与 MCP 完全相同的配置和烧录函数，便于先隔离验证环境：

```powershell
# 首次执行会创建配置模板，并列出配置内容
python .\manual_test.py list

# 通过工程别名烧录
python .\manual_test.py flash --project-name motor_control

# 显式参数烧录 BIN
python .\manual_test.py flash `
  --firmware-path D:\Builds\demo.bin `
  --device STM32F407VE `
  --interface SWD `
  --speed 4000 `
  --base-address 0x08000000
```

手测脚本在配置或参数错误时返回非零退出码；在硬件烧录失败时将输出与 MCP 相同的结构化错误 JSON。
