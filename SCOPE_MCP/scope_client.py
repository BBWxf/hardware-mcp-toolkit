"""
scope_client.py
================
泰克 4/5/6 系列 MSO 示波器（含 MSO44）SCPI 底层通信层。

设计原则：
- 不依赖 NI-VISA / pyvisa，纯 Python 标准库 socket 实现。
  原因：实验室里示波器多、电脑多，装 VISA 驱动是额外负担；
  泰克示波器的网口本身就支持"raw socket SCPI"（固定端口 4000），
  直接 TCP 连接发送 ASCII 命令即可，无需额外驱动。
- 所有网络细节封装在这一个文件里，其他代码只管调用方法、不管 socket。

如果以后想换底层实现（比如换成 pyvisa），只需要保证这个类的
公开方法（connect/close/write/query/query_raw_block/check_error）
行为不变，上层代码完全不用动。
"""

import socket
import time

# 泰克示波器网口 raw socket SCPI 固定端口，4/5/6 系列都是这个
DEFAULT_PORT = 4000
DEFAULT_TIMEOUT = 30.0  # 秒。截图、大波形传输可能较慢，超时不要设太短


class ScopeConnectionError(Exception):
    """网络层面的问题：连不上、连接中断、超时等"""
    pass


class ScopeCommandError(Exception):
    """协议层面的问题：返回数据格式不对（比如期待块数据头 '#' 但没收到）"""
    pass


class TekScope:
    """
    代表一台示波器的一次连接。

    用法：
        with TekScope("192.0.2.1") as scope:
            idn = scope.query("*IDN?")

    也可以手动 connect()/close()，但推荐用 with，保证网络连接一定会关闭。
    """

    def __init__(self, ip, port=DEFAULT_PORT, timeout=DEFAULT_TIMEOUT):
        self.ip = ip
        self.port = port
        self.timeout = timeout
        self.sock = None

    # ---------- 连接管理 ----------

    def connect(self):
        try:
            self.sock = socket.create_connection((self.ip, self.port), timeout=self.timeout)
        except (socket.timeout, OSError) as e:
            raise ScopeConnectionError(
                f"无法连接到示波器 {self.ip}:{self.port} —— {e}。"
                f"请检查：1) IP是否正确 2) 电脑和示波器是否在同一网段 "
                f"3) 示波器网络设置里 Socket Server 是否开启"
            ) from e
        self.sock.settimeout(self.timeout)
        # HEADER OFF：让查询命令返回纯数值，不带命令名前缀，方便解析
        # 例如 "CH1:SCALE?" 返回 "1.0E+0" 而不是 "CH1:SCALE 1.0E+0"
        self.write("HEADER OFF")

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    # ---------- 基础读写 ----------

    def write(self, cmd: str):
        """发送一条命令，不等待返回（用于设置类命令，如 CH1:SCALE 1.0）"""
        if self.sock is None:
            raise ScopeConnectionError("尚未连接，请先调用 connect()")
        data = (cmd.strip() + "\n").encode("ascii", errors="replace")
        try:
            self.sock.sendall(data)
        except OSError as e:
            raise ScopeConnectionError(f"发送命令失败: {cmd} —— {e}") from e

    def _recv_exact(self, n: int) -> bytes:
        """从 socket 精确读取 n 字节（TCP 可能分片，必须循环读到够为止）"""
        if n < 0:
            raise ScopeCommandError(f"请求读取的字节数不能为负数: {n}")

        buf = b""
        while len(buf) < n:
            try:
                chunk = self.sock.recv(n - len(buf))
            except socket.timeout as e:
                raise ScopeConnectionError(
                    f"读取示波器 {self.ip} 的响应超时（{self.timeout:g} 秒）"
                ) from e
            except OSError as e:
                raise ScopeConnectionError(f"读取示波器响应失败: {e}") from e
            if not chunk:
                raise ScopeConnectionError("连接被对方关闭，数据读取不完整")
            buf += chunk
        return buf

    def _read_line(self, max_bytes: int = 1 << 20) -> bytes:
        """读取一行文本响应，直到遇到换行符"""
        buf = b""
        while not buf.endswith(b"\n"):
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout as e:
                raise ScopeConnectionError(
                    f"读取示波器 {self.ip} 的响应超时（{self.timeout:g} 秒）"
                ) from e
            except OSError as e:
                raise ScopeConnectionError(f"读取示波器响应失败: {e}") from e
            if not chunk:
                break
            buf += chunk
            if len(buf) > max_bytes:
                raise ScopeCommandError("响应数据超出预期长度，可能协议解析有误")
        return buf

    def query(self, cmd: str) -> str:
        """发送一条查询命令（以 ? 结尾），返回文本响应（已去除首尾空白）"""
        self.write(cmd)
        raw = self._read_line()
        return raw.decode("ascii", errors="replace").strip()

    def query_raw_block(self, cmd: str) -> bytes:
        """
        读取 IEEE 488.2 "definite length block" 格式的二进制数据。
        用于返回 IEEE 488.2 长度块的命令。

        块格式: '#' + N(1位数字，表示长度字段有几位) + 长度字段(N位数字) + 数据本体
        例如: #800001234<1234字节的数据>
              '#' 后面的 '8' 表示长度字段占8位数字，即 '00001234'，代表数据长度1234字节
        """
        self.write(cmd)

        header = self._recv_exact(2)  # 读 '#' 和 N
        if header[0:1] != b"#":
            # 有些命令出错时会直接返回错误文本而不是块数据，这里给出更友好的提示
            rest = self._read_line()
            raise ScopeCommandError(
                f"命令 '{cmd}' 预期返回二进制块数据(以'#'开头)，"
                f"实际收到: {header + rest!r}。"
                f"可能该命令在这台示波器上语法不同，请用 check_error() 查看示波器报错队列"
            )

        try:
            num_digits = int(header[1:2])
        except ValueError as e:
            raise ScopeCommandError(f"命令 '{cmd}' 返回了无效的块数据头: {header!r}") from e
        if not 1 <= num_digits <= 9:
            raise ScopeCommandError(
                f"命令 '{cmd}' 返回了不支持的块数据长度字段: {num_digits}"
            )

        len_field = self._recv_exact(num_digits)
        try:
            data_len = int(len_field.decode("ascii"))
        except (UnicodeDecodeError, ValueError) as e:
            raise ScopeCommandError(
                f"命令 '{cmd}' 返回了无效的块数据长度: {len_field!r}"
            ) from e
        data = self._recv_exact(data_len)

        # 块数据后通常还有一个换行符，非阻塞方式尝试读掉，读不到也没关系
        try:
            self.sock.settimeout(0.3)
            self.sock.recv(1)
        except (socket.timeout, OSError):
            pass
        finally:
            self.sock.settimeout(self.timeout)

        return data

    # ---------- 调试辅助 ----------

    def check_error(self, max_items: int = 10):
        """
        查询示波器事件队列（ALLev?）。
        排查"命令语法在我这台示波器上不对"的问题时，这是最有用的工具：
        执行完一条可疑命令后立刻调用这个方法，就能看到示波器是否拒绝了该命令。
        """
        errors = []
        for _ in range(max_items):
            resp = self.query("ALLev?")
            if resp.startswith("0,") or "No events" in resp:
                break
            errors.append(resp)
        return errors
