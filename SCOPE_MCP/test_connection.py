"""
test_connection.py
====================
独立测试脚本，不涉及 MCP，只用来验证：
1. 电脑能不能连上示波器
2. 基本 SCPI 命令、截图、波形、测量命令的语法在这台示波器上对不对

用法:
    python test_connection.py 192.0.2.1
    示例地址需替换为真实设备 IP；本脚本会访问并设置真实仪器。

建议第一次接入新示波器/新型号时，先跑这个脚本，
一步步确认每个功能都正常，再去用 MCP。
"""

import sys
import scope_commands


def main():
    if len(sys.argv) != 2:
        print("用法: python test_connection.py <示波器IP>")
        sys.exit(1)

    ip = sys.argv[1]

    print(f"[1/5] 测试基本连接 (*IDN?) ...")
    idn = scope_commands.get_idn(ip)
    print(f"  -> {idn}")

    print(f"[2/5] 测试截图 ...")
    path = scope_commands.get_screenshot(ip, "test_screenshot.png")
    print(f"  -> 已保存到 {path}，请打开看看是否是正常的示波器屏幕画面")

    print(f"[3/5] 测试波形数据读取 (CH1) ...")
    wfm = scope_commands.get_waveform(ip, "CH1", max_points=1000)
    print(f"  -> 读到 {wfm['n_points']} 个点，"
          f"前3个电压值: {wfm['voltage'][:3]}")

    print(f"[4/5] 测试测量值读取 (CH1 频率) ...")
    meas = scope_commands.get_measurement(ip, "CH1", "FREQUENCY")
    print(f"  -> {meas}")

    print(f"[5/5] 测试通道设置 (CH1 挡位查询前后对比) ...")
    from scope_client import TekScope
    with TekScope(ip) as scope:
        before = scope.query("CH1:SCAle?")
    result = scope_commands.set_channel(ip, "CH1", scale=float(before))  # 设成原来的值，不实际改变
    print(f"  -> {result}")

    print("\n全部测试完成。如果某一步报错或结果看起来不对，"
          "请对照 README.md 里的'排查'章节逐条核对 SCPI 命令。")


if __name__ == "__main__":
    main()
