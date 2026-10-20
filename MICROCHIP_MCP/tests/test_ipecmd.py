"""全部使用临时文件和模拟子进程，不运行真实 IPECMD。"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from ipecmd import Ipecmd, OUTPUT_LIMIT, _device_argument, _programmer_lock


class IpecmdTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.exe = self.root / "IPE with spaces" / "ipecmd.exe"
        self.exe.parent.mkdir()
        self.exe.touch()
        self.hex_file = self.root / "固件 with spaces.hex"
        self.hex_file.write_text(":00000001FF\n", encoding="ascii")
        self.config_path = self.root / "config.json"
        self.data = {
            "ipe": {"executable_path": str(self.exe)},
            "programmer": {"type": "PK4", "serial_number": "BUR123456789"},
            "target": {"device": "PIC16F877", "voltage_v": 3.3},
            "programming": {"hex_file": str(self.hex_file)},
        }
        self.save()
        self.ipe = Ipecmd(self.config_path)

    def save(self):
        self.config_path.write_text(json.dumps(self.data), encoding="utf-8")

    @staticmethod
    def result(stdout="Program Succeeded.", exit_code=0, stderr="", timed_out=False):
        return {"stdout": stdout, "stderr": stderr, "exit_code": exit_code,
                "timed_out": timed_out, "duration_seconds": 0.01}

    def test_program_defaults_no_extra_erase_verify_or_run(self):
        with patch("ipecmd.subprocess.Popen") as popen:
            plan = self.ipe.preview("program")
            popen.assert_not_called()
        self.assertEqual(plan["command"], [str(self.exe), "-P16F877", "-TPPK4",
                                          "-TSBUR123456789", "-W3.3",
                                          f"-F{self.hex_file}", "-M"])
        self.assertFalse(plan["hardware_accessed"])
        self.assertFalse((self.root / "logs").exists())

    def test_supported_device_prefixes(self):
        for device, expected in [("PIC16F877", "16F877"), ("dsPIC33EP256MC506", "33EP256MC506"),
                                 ("ATtiny817", "ATtiny817"), ("ATSAMD21G18A", "ATSAMD21G18A")]:
            with self.subTest(device=device):
                self.assertEqual(_device_argument(device), expected)

    def test_overrides_and_reload_without_mutating_configuration(self):
        plan = self.ipe.preview("program", voltage_v=5.0, release_from_reset=True,
                                memory_region="P", verify_after_program=True)
        self.assertIn("-W5", plan["command"])
        self.assertIn("-OL", plan["command"])
        self.assertEqual(plan["command"][-2:], ["-MP", "-YP"])
        self.assertEqual(self.ipe.load_config().target.voltage_v, 3.3)
        self.data["target"]["voltage_v"] = 5.0
        self.save()
        self.assertEqual(self.ipe.load_config().target.voltage_v, 5.0)

    def test_external_power_omits_w(self):
        plan = self.ipe.preview("erase", power_from_programmer=False)
        self.assertFalse(any(arg.startswith("-W") for arg in plan["command"]))

    def test_interface_speed_and_dfp(self):
        self.data["target"].update(device="ATtiny817", interface="UPDI", speed_mhz=0.1)
        self.data["ipe"]["dfp"] = {"name": "ATtiny_DFP", "version": "1.0.0", "vendor": "Microchip"}
        self.save()
        args = self.ipe.preview("program")["command"]
        self.assertIn("-PATtiny817", args)
        self.assertIn("-ORIUPDI", args)
        self.assertIn("-ORS0.1", args)
        self.assertIn("-OWDATtiny_DFP,1.0.0,Microchip", args)

    def test_missing_required_values_fail_before_subprocess(self):
        for section, key in [("target", "device"), ("target", "voltage_v"),
                             ("programmer", "serial_number"), ("programming", "hex_file")]:
            with self.subTest(key=key):
                original = self.data[section][key]
                self.data[section][key] = None
                self.save()
                with patch("ipecmd.subprocess.Popen") as popen:
                    with self.assertRaises(ValueError):
                        self.ipe.preview("program")
                    popen.assert_not_called()
                self.data[section][key] = original
        self.save()

    def test_unknown_keys_and_invalid_types_rejected(self):
        cases = [("target", "voltage_v", 1.8), ("target", "power_from_programmer", "false"),
                 ("target", "device", "16F877 -E"), ("programmer", "serial_number", "abc -E"),
                 ("ipe", "timeout_seconds", 0), ("target", "speed_mhz", float("inf")),
                 ("target", "interface", "invalid"), ("target", "unknown", True)]
        for section, key, value in cases:
            with self.subTest(key=key, value=value):
                original = self.data[section].copy()
                self.data[section][key] = value
                self.save()
                with self.assertRaises(ValueError):
                    self.ipe.load_config()
                self.data[section] = original
        self.save()
        with self.assertRaises(ValueError):
            self.ipe.preview("program", extra_args=["-E"])
        with self.assertRaises(ValueError):
            self.ipe.preview("unknown")

    def test_hex_path_validation_and_relative_paths(self):
        with self.assertRaises(ValueError):
            self.ipe.preview("program", hex_file="missing.hex")
        binary = self.root / "file.bin"
        binary.touch()
        with self.assertRaises(ValueError):
            self.ipe.preview("program", hex_file=str(binary))
        plan = self.ipe.preview("program", hex_file=self.hex_file.name)
        self.assertIn(f"-F{self.hex_file}", plan["command"])

    def test_erase_blank_check_and_verify_commands(self):
        for operation, flag in [("erase", "-E"), ("blank_check", "-C"), ("verify", "-Y")]:
            with self.subTest(operation=operation):
                args = self.ipe.preview(operation)["command"]
                self.assertEqual(args[-1], flag)
                if operation != "verify":
                    self.assertFalse(any(arg.startswith("-F") for arg in args))

    def test_environment_reads_jar_without_executing(self):
        with zipfile.ZipFile(self.exe.with_name("ipecmd.jar"), "w") as archive:
            archive.writestr("com/microchip/mplab/ipecmd/common/ipeproperty.properties",
                             "IPECMD.VERSION = v6.00\n")
        with patch("ipecmd.subprocess.Popen") as popen:
            result = self.ipe.environment()
            popen.assert_not_called()
        self.assertEqual(result["ipecmd_version"], "v6.00")
        self.assertFalse(result["hardware_accessed"])

    def test_operation_success_markers_and_false_positives(self):
        for operation, text in [("program", "Programming/Verify complete"),
                                ("program", "Program Succeeded."),
                                ("verify", "Verify Succeeded."),
                                ("verify", "Verification successful."),
                                ("erase", "Erase successful"),
                                ("blank_check", "Blank check complete, device is blank."),
                                ("read", "Read successfully.")]:
            with self.subTest(operation=operation, text=text):
                self.assertEqual(Ipecmd._status(operation, self.result(text)), "success")
        for text in ["Program Succeeded.\nVerify Target Failed.", "No Tools Connected.",
                     "Error: invalid device", "Programmer not found."]:
            self.assertEqual(Ipecmd._status("program", self.result(text)), "failed")
        self.assertEqual(Ipecmd._status("program", self.result("tool exited")), "unconfirmed")
        self.assertEqual(Ipecmd._status("program", self.result(exit_code=7)), "failed")
        self.assertEqual(Ipecmd._status("program", self.result(timed_out=True)), "timeout")

    def test_extra_verify_requires_its_own_completion_marker(self):
        for text in ("Program Succeeded.", "Programming/Verify complete"):
            self.assertEqual(Ipecmd._status("program", self.result(text), True), "unconfirmed")
        self.assertEqual(Ipecmd._status("program", self.result(
            "Programming/Verify complete\nVerification successful.\n"), True), "success")

    @unittest.skipUnless(os.name == "nt", "Windows 执行路径")
    def test_timeout_quarantine_survives_backend_restart(self):
        self.data["programmer"]["serial_number"] = "TEST" + uuid.uuid4().hex
        self.save()
        with patch.object(self.ipe, "_run", return_value=self.result("partial", timed_out=True)):
            result = self.ipe.execute("program")
        recovery = Path(result["recovery_file"])
        self.addCleanup(recovery.unlink, missing_ok=True)
        self.assertTrue(recovery.is_file())
        other = Ipecmd(self.config_path)
        with patch.object(other, "_run") as run:
            with self.assertRaisesRegex(RuntimeError, "跨进程阻断"):
                other.execute("program")
            run.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows 执行路径")
    def test_execute_logs_raw_output_and_does_not_retry_failure(self):
        with patch.object(self.ipe, "_run", return_value=self.result("Verify Target Failed.", exit_code=7)) as run:
            result = self.ipe.execute("verify")
            run.assert_called_once()
        self.assertEqual(result["status"], "failed")
        log = json.loads(Path(result["log_file"]).read_text(encoding="utf-8"))
        self.assertEqual(log["stdout"], "Verify Target Failed.")
        self.assertEqual(log["exit_code"], 7)

    @unittest.skipUnless(os.name == "nt", "Windows 执行路径")
    def test_stdout_truncated_but_log_complete(self):
        text = "Program Succeeded.\n" + "x" * (OUTPUT_LIMIT + 20)
        with patch.object(self.ipe, "_run", return_value=self.result(text)):
            result = self.ipe.execute("program")
        self.assertLess(len(result["stdout"]), len(text))
        log = json.loads(Path(result["log_file"]).read_text(encoding="utf-8"))
        self.assertEqual(log["stdout"], text)

    @unittest.skipUnless(os.name == "nt", "Windows 执行路径")
    def test_no_overwrite_and_read_failure_preserves_file(self):
        output = self.root / "read.hex"
        output.write_text("old", encoding="ascii")
        with patch.object(self.ipe, "_run") as run:
            with self.assertRaises(ValueError):
                self.ipe.execute("read", output_file=str(output))
            run.assert_not_called()
        with patch.object(self.ipe, "_run", return_value=self.result("Export Hex Failed.", exit_code=7)):
            result = self.ipe.execute("read", output_file=str(output), overwrite=True)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(output.read_text(), "old")

    @unittest.skipUnless(os.name == "nt", "Windows 执行路径")
    def test_readback_published_only_after_success(self):
        output = self.root / "read.hex"

        def run(args, config):
            path = Path(next(arg[3:] for arg in args if arg.startswith("-GF")))
            self.assertNotEqual(path, output)
            path.write_text(":00000001FF\n", encoding="ascii")
            return self.result("Read successfully.")

        with patch.object(self.ipe, "_run", side_effect=run):
            result = self.ipe.execute("read", output_file=str(output))
        self.assertEqual(result["status"], "success")
        self.assertEqual(output.read_text(), ":00000001FF\n")

    @unittest.skipUnless(os.name == "nt", "Windows 执行路径")
    def test_read_missing_artifact_not_success(self):
        output = self.root / "read.hex"
        with patch.object(self.ipe, "_run", return_value=self.result("Read successfully.")):
            result = self.ipe.execute("read", output_file=str(output))
        self.assertEqual(result["status"], "failed")
        self.assertFalse(output.exists())

    @unittest.skipUnless(os.name == "nt", "Windows 执行路径")
    def test_read_race_does_not_clobber_new_file(self):
        output = self.root / "read.hex"

        def run(args, config):
            Path(args[-1][3:]).write_text(":00000001FF\n", encoding="ascii")
            output.write_text("created by someone else", encoding="ascii")
            return self.result("Read successfully.")

        with patch.object(self.ipe, "_run", side_effect=run):
            result = self.ipe.execute("read", output_file=str(output))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(output.read_text(), "created by someone else")

    @unittest.skipUnless(os.name == "nt", "Windows 文件锁")
    def test_process_lock_refuses_same_serial(self):
        with _programmer_lock("TEST1234"):
            with self.assertRaises(RuntimeError):
                with _programmer_lock("TEST1234"):
                    self.fail("不应同时持有相同序列号的锁")
        with _programmer_lock("TEST1234"):
            pass

    @unittest.skipUnless(os.name == "nt", "Windows 执行路径")
    def test_thread_lock_and_quarantine(self):
        self.ipe._lock.acquire()
        try:
            with self.assertRaises(RuntimeError):
                self.ipe.execute("erase")
        finally:
            self.ipe._lock.release()
        self.ipe._blocked = True
        with patch.object(self.ipe, "_run") as run:
            with self.assertRaises(RuntimeError):
                self.ipe.execute("erase")
            run.assert_not_called()

    def test_run_uses_argv_cwd_captured_pipes_and_no_shell(self):
        process = MagicMock()
        process.communicate.return_value = (b"Program Succeeded.", b"")
        process.returncode = 0
        config = self.ipe.load_config()
        with patch("ipecmd.subprocess.Popen", return_value=process) as popen:
            result = self.ipe._run([str(self.exe), "-M"], config)
        self.assertFalse(popen.call_args.kwargs["shell"])
        self.assertEqual(popen.call_args.kwargs["cwd"], self.exe.parent)
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["stdout"], "Program Succeeded.")

    def test_run_launch_failure(self):
        with patch("ipecmd.subprocess.Popen", side_effect=OSError("missing runtime")):
            result = self.ipe._run([str(self.exe)], self.ipe.load_config())
        self.assertEqual(Ipecmd._status("program", result), "failed")
        self.assertIn("missing runtime", result["stderr"])

    def test_timeout_terminates_process_and_blocks_further_operations(self):
        process = MagicMock()
        process.communicate.side_effect = [subprocess.TimeoutExpired("ipecmd", 1), (b"partial", b"")]
        process.returncode = 1
        with patch("ipecmd.subprocess.Popen", return_value=process), patch.object(self.ipe, "_terminate") as terminate:
            result = self.ipe._run([str(self.exe)], self.ipe.load_config())
        terminate.assert_called_once_with(process)
        self.assertTrue(result["timed_out"])
        self.assertTrue(self.ipe._blocked)
        self.assertEqual(result["stdout"], "partial")

    @unittest.skipUnless(os.name == "nt", "Windows 枚举路径")
    def test_list_programmers_no_target_or_power_options(self):
        text = "1) PICkit 4     S.No: BUR123456789\n2) PICkit 4    S.No: BUR987654321\n"
        with patch.object(self.ipe, "_run", return_value=self.result(text)) as run:
            result = self.ipe.list_programmers()
        self.assertEqual(run.call_args.args[0], [str(self.exe), "-T"])
        self.assertEqual(len(result["programmers"]), 2)
        self.assertEqual(result["programmers"][1]["serial_number"], "BUR987654321")

    @unittest.skipUnless(os.name == "nt", "Windows 枚举路径")
    def test_list_programmers_actual_format_preserves_nonzero_exit(self):
        text = "Available Tool List\r\n-------------------\r\n1  PICkit 4 S.No : BUR123456789\r\n"
        with patch.object(self.ipe, "_run", return_value=self.result(text, exit_code=50)):
            result = self.ipe.list_programmers()
        self.assertEqual(result["programmers"], [
            {"type": "PICkit 4", "serial_number": "BUR123456789"},
        ])
        self.assertEqual(result["exit_code"], 50)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["stdout"], text)


if __name__ == "__main__":
    unittest.main()
