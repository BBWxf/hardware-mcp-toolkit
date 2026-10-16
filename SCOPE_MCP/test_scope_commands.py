"""无硬件回归测试：模拟 SCPI 响应，不连接真实示波器。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import scope_commands
from scope_client import ScopeCommandError, ScopeConnectionError, TekScope


class ScopeClientTests(unittest.TestCase):
    def test_query_honors_response_length_limit(self):
        for limit, should_raise in ((8, True), (16, False)):
            with self.subTest(limit=limit):
                scope = TekScope("192.0.2.1")
                scope.sock = MagicMock()
                scope.sock.recv.return_value = b"1,2,3,4,5\n"
                if should_raise:
                    with self.assertRaises(ScopeCommandError):
                        scope.query("CURVE?", max_bytes=limit)
                else:
                    self.assertEqual(scope.query("CURVE?", max_bytes=limit), "1,2,3,4,5")
                scope.sock.sendall.assert_called_once_with(b"CURVE?\n")


class ScopeCommandsTests(unittest.TestCase):
    def setUp(self):
        scope_patch = patch("scope_commands.TekScope")
        self.scope = scope_patch.start().return_value.__enter__.return_value
        self.addCleanup(scope_patch.stop)
        self.scope.check_error.return_value = []
        self.responses = {
            "*IDN?": "TEKTRONIX,MSO44,B000000,CF:91.1CT FV:1.0", "*OPC?": "1",
        }
        self.scope.query.side_effect = lambda command, **kwargs: self.responses[command]
        sleep_patch = patch("scope_commands.time.sleep")
        sleep_patch.start()
        self.addCleanup(sleep_patch.stop)

    def test_idn(self):
        self.assertEqual(scope_commands.get_idn("192.0.2.1"), self.responses["*IDN?"])

    def test_mso_measurement(self):
        self.responses["MEASUrement:MEAS1:VALue?"] = "1000.0"
        result = scope_commands.get_measurement("192.0.2.1", "ch1", "FREQUENCY")
        self.assertEqual(result, {"channel": "CH1", "type": "FREQUENCY", "value": 1000.0})
        self.assertEqual(self.scope.write.call_args_list, [
            call("MEASUrement:MEAS1:SOUrce1 CH1"),
            call("MEASUrement:MEAS1:TYPe FREQUENCY"),
            call("MEASUrement:MEAS1:STATE ON"),
        ])

    def test_mso_channel(self):
        result = scope_commands.set_channel(
            "192.0.2.1", "ch2", enabled=False, scale=0.5, coupling="DC", position=1.0,
        )
        self.assertEqual(self.scope.write.call_args_list, [
            call("DISplay:GLObal:CH2:STATE OFF"),
            call("CH2:SCAle 0.5"),
            call("CH2:COUPling DC"),
            call("CH2:POSition 1.0"),
        ])
        self.assertEqual(result["scope_errors"], [])

    def test_mso_trigger(self):
        scope_commands.set_trigger("192.0.2.1", "CH2", 0.5, "AUTO", "RISE")
        self.assertEqual(self.scope.write.call_args_list, [
            call("TRIGger:A:TYPe EDGE"),
            call("TRIGger:A:EDGE:SOUrce CH2"),
            call("TRIGger:A:EDGE:SLOpe RISE"),
            call("TRIGger:A:LEVel 0.5"),
            call("TRIGger:A:MODe AUTO"),
        ])

    def test_mso_waveform(self):
        self.responses.update({
            "HORizontal:RECOrdlength?": "1000",
            "WFMOutpre:XINcr?": "0.001",
            "WFMOutpre:XZEro?": "-0.1",
            "WFMOutpre:YMUlt?": "0.5",
            "WFMOutpre:YZEro?": "1.0",
            "WFMOutpre:YOFf?": "2.0",
            "CURVE?": "2,4,0",
        })
        result = scope_commands.get_waveform("192.0.2.1", max_points=3)
        self.assertEqual(result["n_points"], 3)
        self.assertEqual(result["voltage"], [1.0, 2.0, 0.0])
        self.assertAlmostEqual(result["time"][0], -0.1)
        self.assertAlmostEqual(result["time"][2], -0.098)
        self.scope.write.assert_any_call("DATa:STOP 3")

    def test_mdo_measurement_uses_immediate_commands(self):
        for model in ("MDO3012", "MDO3014", "MDO3022", "MDO3024", "MDO3032",
                      "MDO3034", "MDO3052", "MDO3054", "MDO3102", "MDO3104"):
            with self.subTest(model=model):
                self.scope.write.reset_mock()
                self.responses["*IDN?"] = f"TEKTRONIX, {model.lower()} ,B000000,FV:1.0"
                self.responses["MEASUrement:IMMed:VALue?"] = "3.3"
                result = scope_commands.get_measurement("192.0.2.1", "CH2", "RMS")
                self.assertEqual(result, {"channel": "CH2", "type": "RMS", "value": 3.3})
                self.assertEqual(self.scope.write.call_args_list, [
                    call("MEASUrement:IMMed:SOUrce1 CH2"),
                    call("MEASUrement:IMMed:TYPe RMS"),
                ])

    def test_mdo34_is_not_misidentified_as_mdo3000(self):
        self.responses["*IDN?"] = "TEKTRONIX,MDO34,B000000,FV:1.0"
        self.responses["MEASUrement:MEAS1:VALue?"] = "1.0"
        scope_commands.get_measurement("192.0.2.1", meas_type="RMS")
        self.scope.write.assert_any_call("MEASUrement:MEAS1:TYPe RMS")
        self.assertNotIn(call("MEASUrement:IMMed:TYPe RMS"), self.scope.write.call_args_list)

    def test_mdo_measurement_names_match_manual(self):
        self.responses["*IDN?"] = "TEKTRONIX,MDO3054,B000000,FV:1.0"
        self.responses["MEASUrement:IMMed:VALue?"] = "1.0"
        for requested, actual in (("RISetime", "RISE"), ("FALltime", "FALL"),
                                  ("OVERshoot", "POVERSHOOT"), ("NOVERSHOOT", "NOVERSHOOT")):
            with self.subTest(requested=requested):
                self.scope.write.reset_mock()
                result = scope_commands.get_measurement("192.0.2.1", meas_type=requested)
                self.scope.write.assert_any_call(f"MEASUrement:IMMed:TYPe {actual}")
                self.assertEqual(result["type"], requested)

    def test_mdo_invalid_measurement_sentinel_is_not_a_valid_value(self):
        self.responses["*IDN?"] = "TEKTRONIX,MDO3054,B000000,FV:1.0"
        self.responses["MEASUrement:IMMed:VALue?"] = "9.9E37"
        with self.assertRaises(ScopeCommandError):
            scope_commands.get_measurement("192.0.2.1")

    def test_mdo_non_numeric_measurement_is_preserved(self):
        self.responses["*IDN?"] = "TEKTRONIX,MDO3054,B000000,FV:1.0"
        self.responses["MEASUrement:IMMed:VALue?"] = "***"
        self.assertEqual(scope_commands.get_measurement("192.0.2.1")["value"], "***")

    def test_mdo_channel_uses_select(self):
        self.responses["*IDN?"] = "TEKTRONIX,MDO3054,B000000,FV:1.0"
        result = scope_commands.set_channel("192.0.2.1", "CH3", enabled=True, coupling="dcreject")
        self.assertEqual(self.scope.write.call_args_list, [
            call("SELect:CH3 ON"), call("CH3:COUPling DCREJECT"),
        ])
        self.assertEqual(result["applied"], {"enabled": True, "coupling": "DCREJECT"})

    def test_gnd_coupling_is_rejected_before_writing(self):
        for model in ("MDO3054", "MSO44"):
            with self.subTest(model=model):
                self.responses["*IDN?"] = f"TEKTRONIX,{model},B000000,FV:1.0"
                with self.assertRaises(ValueError):
                    scope_commands.set_channel("192.0.2.1", "CH1", enabled=True, coupling="GND")
        self.scope.write.assert_not_called()

    def test_mdo_two_channel_models_reject_ch3_and_ch4(self):
        self.responses["*IDN?"] = "TEKTRONIX,MDO3012,B000000,FV:1.0"
        for operation in (
            lambda: scope_commands.set_channel("192.0.2.1", "CH3", enabled=True),
            lambda: scope_commands.get_waveform("192.0.2.1", "CH4"),
            lambda: scope_commands.get_measurement("192.0.2.1", "CH3"),
            lambda: scope_commands.set_trigger("192.0.2.1", source="CH4", level=1.0),
        ):
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                operation()
        self.scope.write.assert_not_called()

    def test_mdo_trigger_level_targets_requested_channel(self):
        self.responses["*IDN?"] = "TEKTRONIX,MDO3054,B000000,FV:1.0"
        scope_commands.set_trigger("192.0.2.1", source="CH3", level=1.2)
        self.assertEqual(self.scope.write.call_args_list, [
            call("TRIGger:A:TYPe EDGE"),
            call("TRIGger:A:EDGE:SOUrce CH3"),
            call("TRIGger:A:LEVel:CH3 1.2"),
        ])

    def test_mdo_level_only_uses_current_trigger_source(self):
        self.responses.update({
            "*IDN?": "TEKTRONIX,MDO3054,B000000,FV:1.0",
            "TRIGger:A:EDGE:SOUrce?": "CH2",
        })
        scope_commands.set_trigger("192.0.2.1", level=0.5)
        self.assertEqual(self.scope.write.call_args_list, [
            call("TRIGger:A:TYPe EDGE"), call("TRIGger:A:LEVel:CH2 0.5"),
        ])

    def test_mdo_level_only_rejects_non_analog_trigger_source(self):
        self.responses.update({
            "*IDN?": "TEKTRONIX,MDO3054,B000000,FV:1.0",
            "TRIGger:A:EDGE:SOUrce?": "D0",
        })
        with self.assertRaises(ValueError):
            scope_commands.set_trigger("192.0.2.1", level=0.5)
        self.scope.write.assert_not_called()

    def test_mdo_waveform_uses_xzero_for_trigger_offset(self):
        self.responses.update({
            "*IDN?": "TEKTRONIX,MDO3054,B000000,FV:1.0",
            "HORizontal:RECOrdlength?": "3",
            "WFMOutpre:XINcr?": "0.001",
            "WFMOutpre:XZEro?": "0.1",
            "WFMOutpre:YMUlt?": "0.5",
            "WFMOutpre:YZEro?": "1.0",
            "WFMOutpre:YOFf?": "2.0",
            "CURVE?": "2,4,0",
        })
        result = scope_commands.get_waveform("192.0.2.1", max_points=1000)
        self.assertEqual(result["voltage"], [1.0, 2.0, 0.0])
        self.assertAlmostEqual(result["time"][0], 0.1)
        self.assertAlmostEqual(result["time"][2], 0.102)
        self.scope.write.assert_any_call("DATa:STOP 3")
        self.responses["HORizontal:RECOrdlength?"] = "10000000"
        scope_commands.get_waveform("192.0.2.1", max_points=2_000_000)
        self.scope.write.assert_any_call("DATa:STOP 1000000")
        self.scope.query.assert_any_call("CURVE?", max_bytes=32_000_000)

    def test_malformed_idn_is_rejected_before_writing(self):
        self.responses["*IDN?"] = "invalid response"
        with self.assertRaises(ScopeCommandError):
            scope_commands.set_channel("192.0.2.1", "CH1", enabled=True)
        self.scope.write.assert_not_called()

    def test_acquisition(self):
        for action, commands in (
            ("run", ["ACQuire:STOPAfter RUNSTop", "ACQuire:STATE ON"]),
            ("stop", ["ACQuire:STATE OFF"]),
            ("single", ["ACQuire:STOPAfter SEQuence", "ACQuire:STATE ON"]),
        ):
            with self.subTest(action=action):
                self.scope.write.reset_mock()
                result = scope_commands.run_acquisition("192.0.2.1", action)
                self.assertEqual(self.scope.write.call_args_list, [call(c) for c in commands])
                self.assertEqual(result, {"action": action, "scope_errors": []})

    def test_horizontal(self):
        result = scope_commands.set_horizontal("192.0.2.1", scale=0.001, position=50)
        self.assertEqual(self.scope.write.call_args_list, [
            call("HORizontal:SCAle 0.001"), call("HORizontal:POSition 50"),
        ])
        self.assertEqual(result["applied"], {"scale": 0.001, "position": 50})

    def test_invalid_arguments_do_not_write(self):
        for operation in (
            lambda: scope_commands.get_measurement("192.0.2.1", meas_type="RMS;*RST"),
            lambda: scope_commands.get_waveform("192.0.2.1", max_points=0),
            lambda: scope_commands.get_waveform("192.0.2.1", channel="CH5"),
            lambda: scope_commands.set_channel("192.0.2.1", "CH1", enabled="ON"),
        ):
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                operation()
        self.scope.write.assert_not_called()


class ScreenshotTests(unittest.TestCase):
    def setUp(self):
        visa_patch = patch("scope_commands.pyvisa.ResourceManager")
        self.manager = visa_patch.start().return_value
        self.addCleanup(visa_patch.stop)
        self.instrument = self.manager.open_resource.return_value
        self.responses = {
            "*IDN?": "TEKTRONIX,MSO44,B000000,CF:91.1CT FV:1.0",
            "SAVE:IMAGe:FILEName?": '"original.png"',
            "SAVE:IMAGe:FILEFormat?": "BMP",
            "*OPC?": "1",
        }
        self.instrument.query.side_effect = lambda command: self.responses[command]
        self.png = scope_commands._PNG_HEADER + b"test image" + scope_commands._PNG_END
        self.instrument.read_raw.return_value = self.png + b"\n"
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.path = Path(temp_dir.name) / "screen.png"

    def test_mso_screenshot(self):
        result = scope_commands.get_screenshot("192.0.2.1", str(self.path))
        self.assertEqual(result, str(self.path))
        self.assertEqual(self.path.read_bytes(), self.png)
        self.instrument.write.assert_any_call('SAVE:IMAGe:FILEName "mcp_screenshot.png"')
        self.instrument.write.assert_any_call("SAVE:IMAGe")
        self.instrument.write.assert_any_call('FILESystem:READFile "mcp_screenshot.png"')
        self.instrument.write.assert_any_call('SAVE:IMAGe:FILEName "original.png"')
        self.instrument.close.assert_called_once()
        self.manager.close.assert_called_once()

    def test_mdo_screenshot_uses_hardcopy_and_restores_format(self):
        self.responses["*IDN?"] = "TEKTRONIX,MDO3054,B000000,FV:1.0"
        scope_commands.get_screenshot("192.0.2.1", str(self.path))
        self.assertEqual(self.path.read_bytes(), self.png)
        self.manager.open_resource.assert_called_once_with("TCPIP0::192.0.2.1::inst0::INSTR")
        self.assertEqual(self.instrument.write.call_args_list, [
            call("HEADer OFF"), call("SAVE:IMAGe:FILEFormat PNG"),
            call("HARDCopy STARt"), call("SAVE:IMAGe:FILEFormat BMP"),
        ])
        self.instrument.close.assert_called_once()
        self.manager.close.assert_called_once()

    def test_mdo_screenshot_read_failure_restores_format(self):
        self.responses["*IDN?"] = "TEKTRONIX,MDO3054,B000000,FV:1.0"
        self.instrument.read_raw.side_effect = scope_commands.pyvisa.errors.VisaIOError(-1073807339)
        with self.assertRaises(ScopeConnectionError):
            scope_commands.get_screenshot("192.0.2.1", str(self.path))
        self.instrument.write.assert_called_with("SAVE:IMAGe:FILEFormat BMP")
        self.instrument.close.assert_called_once()
        self.manager.close.assert_called_once()
        self.assertFalse(self.path.exists())

    def test_screenshot_stops_if_opc_is_not_confirmed(self):
        for model in ("MDO3054", "MSO44"):
            with self.subTest(model=model):
                self.instrument.read_raw.reset_mock()
                self.responses["*IDN?"] = f"TEKTRONIX,{model},B000000,FV:1.0"
                self.responses["*OPC?"] = "0"
                with self.assertRaises(ScopeCommandError):
                    scope_commands.get_screenshot("192.0.2.1", str(self.path))
                self.instrument.read_raw.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_unknown_idn_does_not_delete_remote_files(self):
        self.responses["*IDN?"] = "invalid"
        with self.assertRaises(ScopeCommandError):
            scope_commands.get_screenshot("192.0.2.1", str(self.path))
        self.instrument.write.assert_not_called()
        self.instrument.close.assert_called_once()
        self.manager.close.assert_called_once()

    def test_incomplete_png_is_rejected(self):
        self.instrument.read_raw.return_value = scope_commands._PNG_HEADER + b"truncated"
        with self.assertRaises(ScopeCommandError):
            scope_commands.get_screenshot("192.0.2.1", str(self.path))
        self.assertFalse(self.path.exists())
        self.instrument.close.assert_called_once()
        self.manager.close.assert_called_once()

    def test_visa_failure_closes_resources(self):
        self.instrument.read_raw.side_effect = scope_commands.pyvisa.errors.VisaIOError(-1073807339)
        with self.assertRaises(ScopeConnectionError):
            scope_commands.get_screenshot("192.0.2.1", str(self.path))
        self.assertFalse(self.path.exists())
        self.instrument.close.assert_called_once()
        self.manager.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
