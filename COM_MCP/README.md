# Serial MCP

协议无关的 stdio 串口收发 MCP 服务。它只提供端口枚举、连接、字节收发和 RTS/DTR 控制；每个产品的帧组包、校验和与解析应由后续的产品专属代码实现。

## MCP 工具

- `list_ports`：枚举系统识别到的串口。
- `connect(port, baud_rate=9600, data_bits=8, parity="N", stop_bits=1, flow_control="none")`：打开唯一的当前串口连接。`flow_control` 支持 `none`、`rtscts`、`xonxoff`。
- `disconnect()`：关闭当前连接。
- `send(data, encoding="utf8")`：发送数据。`encoding` 支持 `utf8`、`hex`、`base64`，不会自动追加换行。
- `read(timeout_ms=1000, max_bytes=4096, encoding="hex")`：单次超时读取。
- `read_until(idle_timeout_ms=100, max_wait_ms=1000, max_bytes=4096, encoding="hex")`：读到首批数据后，持续读取直到空闲期结束；完全未收到数据时等待至 `max_wait_ms`。
- `set_control_lines(rts=None, dtr=None)`：控制 RTS/DTR。

`read` 与 `read_until` 都同时返回原始 `hex`、UTF-8 解码尝试和按 `encoding` 请求格式化后的 `data`。全部工具返回结构化 JSON：`ok` 为 `false` 时，`error.code` 会明确区分 `validation_error`、`serial_not_open`、`port_busy`、`device_disconnected`、`serial_timeout`、`serial_io_error` 等错误。

> **RTS/DTR 注意事项**：部分开发板会将它们用于复位或进入下载模式。只有在协议明确要求，或已获得设备操作者确认后，才能调用 `set_control_lines`。

## 安装与本地验证

先进入本仓库的 `COM_MCP` 目录，再在 Windows PowerShell 中执行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest -v
```

测试使用内存中的 `FakeSerial` mock，不会打开真实串口，也不需要硬件。

## 注册为 Claude Code 全局 MCP

在本项目目录执行以下命令后，服务会写入 Claude Code 的**用户级** MCP 配置，并可被所有本机项目使用：

```powershell
$python = (Resolve-Path ".\.venv\Scripts\python.exe").Path
$server = (Resolve-Path ".\serial_mcp_server.py").Path
claude.cmd mcp add --scope user --transport stdio serial-mcp -- "$python" "$server"
claude mcp list
```

注册完成后，重新打开一个 Claude Code 会话即可看到 `serial-mcp` 的工具。

## 真实设备联调流程

1. 先调用 `list_ports`，确认端口和设备身份，避免连接错误设备。
2. 按协议提供的串口参数调用 `connect`。
3. 先用 `send` 配合 `read` 或 `read_until` 手动观察设备真实回复，再实现该产品专属的组包和解析。
4. 设备不通时，针对同一问题最多自行调整和重测三次；仍未解决时保留发送内容、实际响应和错误信息，交由操作者确认协议或硬件状态。
