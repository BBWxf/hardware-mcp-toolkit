# SCPI 封装对应关系

依据：[官方 3.50 SCPI 命令参考](https://documentation.omicron-lab.com/BodeAutomationInterface/3.50/articlesScpi/commandReference/general/abort.html)。下文省略逻辑 channel/trace 索引，均为官方支持的默认 1；物理输入用 CH1/CH2 指定。

`?` 表示查询。专用设置工具通常随后读回实际设置，并查询 `:SYST:ERR:ALL?`；没有自动启动、校准编排或参数寻优。多个设置不是原子事务，失败不回滚。

## 连接、设备和错误

| MCP | 官方 SCPI |
|---|---|
| `connect` | `*IDN?` → `:SYST:LOCK:REQ?` → `:FORM ASC` |
| `disconnect` | `:SYST:LOCK:REL?`，然后关闭 TCP |
| `get_connection_status` | 无，仅本地状态 |
| `get_device_limits` | `:SERV:SWE:FREQ:MIN?`、`:SERV:SWE:FREQ:MAX?`、`:SERV:SWE:POIN?`、`:SERV:SWE:POW:MIN?`、`:SERV:SWE:POW:MAX?` |
| `get_status` | `:STAT:OPER:COND?`、`:SYST:LOCK:OWN?` |
| `read_errors` | `:SYST:ERR:ALL?`，读取并清空队列 |
| `reset_device` | `*RST`，需另外等待完成 |

操作条件寄存器：[官方位定义](https://documentation.omicron-lab.com/BodeAutomationInterface/3.50/articlesScpi/commandReference/status/operationCondition.html)。bit 0 校准、3 扫频、4 测量、5 等待触发、9 请求锁、10 已锁。未锁定时不能依赖所有测量状态位。

## 配置

| MCP | 官方 SCPI | 参数/单位 |
|---|---|---|
| `configure_measurement` | `:CALC:PAR:DEF` | `S11/S21/GAIN/CH1/CH2/Z/R/T`；可显式附加 `,DEF` |
| 同上，阻抗方法 | `:SENS:Z:METH` | `P1R/TSH/TSER/ECO/EBR/IAD/VCG`；可显式附加 `,DEF` |
| `configure_sweep` | `:SENS:FREQ:STAR/STOP` | Hz，运行时读取仪器限制 |
| 同上 | `:SENS:SWE:POIN`、`:SENS:SWE:TYPE` | 1 至设备最大点数，`LIN/LOG` |
| 同上 | `:SENS:BAND` | Hz，设备可能舍入到支持值 |
| 同上 | `:SENS:SWE:DWEL` | 0–60 s，每频点稳定时间 |
| `configure_source` | `:SOUR:POW` | dBm，发送裸数，无 dBm 后缀 |
| 同上 | `:OUTP:AOFF` | `0/1`，不是立即开关源 |
| `input_settings` | `:INP:ATT:CH1/CH2` | dB，发送裸数；合法档位由设备判断 |
| 同上 | `:INP:GAIN:CH1/CH2` | 线性因子，非 dB；受测量模式限制 |
| 同上 | `:INP:IMP:CH1/CH2` | 50 / 1000000 Ω；受测量模式限制 |
| `configure_result` | `:CALC:FORM` | `GDEL/IMAG/MLIN/MLOG/PHAS/REAL/UPH/SWR/SMIT/SLIN/SLOG/SCOM/SADM` |
| 同上 | `:CALC:ZPAR:DEF` | `Z/Y/REFL/CP/CS/LP/LS/RP/RS/Q/QTG/D` |
| `averaging_settings` | `:SENS:AVER:STAT/COUN/CLE` | 开关、2–1e9 次、清平均缓存 |
| `get_configuration` | 上述测量/扫频/源/触发设置的对应查询 | 不返回所有校准或平均参数，它们有专用工具 |

参考：[测量模式](https://documentation.omicron-lab.com/BodeAutomationInterface/3.50/3_MeasurementModes.html)、[格式](https://documentation.omicron-lab.com/BodeAutomationInterface/3.50/articlesScpi/commandReference/calculate/format.html)、[阻抗结果参数](https://documentation.omicron-lab.com/BodeAutomationInterface/3.50/articlesScpi/commandReference/calculate/zparameterDefine.html)。

## 执行与读取

| MCP | 官方 SCPI |
|---|---|
| `configure_trigger` | `:TRIG:SOUR INT/BUS/EXT`、`:INIT:CONT 0/1` |
| `initiate_measurement` | `:INIT` |
| `send_trigger` | `:TRIG:SING` |
| `abort_measurement` | `:ABOR`，不更改 continuous |
| `estimate_measurement_time` | `:SENS:SWE:TIME?`，秒 |
| `wait_for_completion` | `*OPC?`，只有回应 1 才确认完成 |
| `read_measurement` | `:SENS:FREQ:DATA?`、`:CALC:DATA:SDAT?`；附带模式/格式元信息 |

[官方触发状态机](https://documentation.omicron-lab.com/BodeAutomationInterface/3.50/articlesScpi/general/trigger.html)：IDLE 时不接受触发。`:INIT` 或 continuous ON 使系统进入等待状态；BUS 测量仍需要触发。continuous OFF 时完成后回到 IDLE。

[SDAT 数据页](https://documentation.omicron-lab.com/BodeAutomationInterface/3.50/articlesScpi/commandReference/calculate/formattedData.html)：结果是两个连续 N 数组，某些格式的第二数组为零。查询前必须已有测量，否则可能不返回数据并超时；不兼容的结果格式也可能不返回数据。

`wait_for_completion` 完成所有 pending operations，但不凭这一点把校准、文件加载或 BUS 等待触发误判为新测量。专用工具只确认本会话明确启动的 INT 测量或提交的 BUS 触发；外部触发可用 `allow_unverified=true` 访问设备缓存。

## 校准

| MCP | 官方 SCPI |
|---|---|
| `get_calibration_status` | `:SENS:CORR:FULL/USER:ENAB?`、`:ACT?`、`:AVAI?` |
| `enable_calibration` | `:SENS:CORR:FULL/USER:ENAB 0/1` |
| `execute_calibration` | `:SENS:CORR:FULL/USER:OPEN/SHORT/LOAD/THRU`，一次只发指定的一项 |
| `calibration_model` | `:SENS:CORR:IMP`、`:LOAD`、`:SDEL`，及对应查询 |
| `save_calibration` | `:MMEM:STOR:CORR "绝对路径.mcalx"` |
| `load_calibration` | `:MMEM:LOAD:CORR:FULL/USER "绝对路径.mcalx"` |

上表 `/` 是选项缩写，**不是发送到仪器的命令文本**。校准执行和文件操作提交后不自动等待；使用 `wait_for_completion`，再查询状态。保存命令本身不区分 FULL/USER 参数；加载需指定范围。

参考：[全范围直通校准](https://documentation.omicron-lab.com/BodeAutomationInterface/3.50/articlesScpi/commandReference/sense/correction/fullRangeThru.html)、[校准保存](https://documentation.omicron-lab.com/BodeAutomationInterface/3.50/articlesScpi/commandReference/memory/storeCorrection.html)、[校准加载](https://documentation.omicron-lab.com/BodeAutomationInterface/3.50/articlesScpi/commandReference/memory/loadCorrectionFullRange.html)。

官方文本中存在少量说明/示例笔误。本服务按正式命令语法实现 LOAD 和 SDEL，不采用 LOAD 页 Python 示例里错写的 SDEL。名义阻抗上限不猜测；保存要求显式绝对路径，避免依赖存在矛盾说明的默认目录。没有增加 fallback 或替代命令。

## 原始文本接口

`scpi_write` / `scpi_query` 用于其余官方 ASCII 接口，例如：

```text
:SYST:TEMP:SENS?
:STAT:QUES:COND?
:SENS:CORR:USER:DATA:FREQ?
:SENS:CORR:USER:DATA:THRU?
*ESR?
```

这些是独立查询示例，不是可直接拼接的脚本。只接受单行、单条命令；引号外不允许分号拼接。`scpi_query` 必须有一个引号外的 `?`，返回原始文本；`scpi_write` 不能有引号外的 `?`，只确认发送，不读错误或等待。特殊的会返回数据的无问号命令不能走 write，应优先用专用接口。

只支持 ASCII 数据，不支持切换到 REAL/REAL32 二进制传输；二进制响应会使连接关闭并返回协议错误。专用数值读数已覆盖文本等价数据，不声称覆盖官方所有传输形式。

原始接口不维护一套官方全命令白名单，也不能保证任意输入适用于 Bode 100。共享文档中的 Bode 500-only 命令仍由设备拒绝，不应由模型当作可用功能。使用前查阅对应官方命令页；完整命令索引从 `get_reference` 获取。
