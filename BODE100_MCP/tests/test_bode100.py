import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from bode100 import Bode100, BodeError, error_entries, floats, number, validate_command
from fake_scpi import FakeServer, Instrument


class ValidationTests(unittest.TestCase):
    def test_rejects_nonfinite_and_boolean_numbers(self):
        for value in (float("nan"), float("inf"), float("-inf"), True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                number(value, "value")

    def test_single_command_and_query_validation(self):
        for command, query in (("*IDN?\n*RST", True), ("*RST;*RST", False),
                               ("*IDN?;*IDN?", True), ("*IDN?", False), ("*RST", True),
                               (':MMEM:STOR:CORR "unterminated', False), ("*IDN?\x00", True)):
            with self.subTest(command=command), self.assertRaises(ValueError):
                validate_command(command, query=query)
        self.assertEqual(validate_command(':MMEM:STOR:CORR "a;b?.mcalx"', query=False),
                         ':MMEM:STOR:CORR "a;b?.mcalx"')

    def test_rejects_binary_format_switch(self):
        for command in (":FORM REAL", "FORMAT:DATA REAL32", ":FORM:DATA REAL"):
            with self.subTest(command=command), self.assertRaises(ValueError):
                validate_command(command, query=False)
        self.assertEqual(validate_command(":FORM ASC", query=False), ":FORM ASC")

    def test_errors_with_commas_and_empty_queue(self):
        self.assertEqual(error_entries('0,"No error"'), [])
        self.assertEqual(error_entries('-221,"Settings conflict, detail",-100,"Command error"'),
                         [{"code": -221, "message": "Settings conflict, detail"},
                          {"code": -100, "message": "Command error"}])
        with self.assertRaises(BodeError):
            error_entries("-221")

    def test_nonfinite_array_preserves_positions(self):
        values = floats("1,NaN,Inf,-Inf,2")
        self.assertEqual(values, [1, None, None, None, 2])
        json.dumps(values, allow_nan=False)


class DeviceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = FakeServer()
        cls.server.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.server.__exit__()

    def setUp(self):
        self.instrument = self.server.instrument = Instrument()
        self.device = Bode100("127.0.0.1", self.server.server_address[1], 0.5)
        self.device.connect()

    def tearDown(self):
        self.device.disconnect()

    def complete_bus_measurement(self):
        self.device.configure_trigger("BUS", False)
        self.device.initiate()
        self.device.trigger()
        result = self.device.wait()
        self.assertTrue(result["measurement_confirmed"])

    def test_connect_only_identifies_locks_and_sets_text_format(self):
        self.assertEqual(self.instrument.commands,
                         ["*IDN?", ":SYST:LOCK:REQ?", ":FORM ASC", ":SYST:ERR:ALL?"])
        with self.assertRaises(BodeError) as caught:
            self.device.connect()
        self.assertEqual(caught.exception.code, "already_connected")

    def test_wrong_device_closes_without_taking_lock(self):
        self.device.disconnect()
        self.instrument.commands.clear()
        self.instrument.identity = "OmicronLab,Bode 500,SERIAL,3.50"
        with self.assertRaises(BodeError) as caught:
            self.device.connect()
        self.assertEqual(caught.exception.code, "wrong_device")
        self.assertFalse(self.device.connection_status()["connected"])
        self.assertEqual(self.instrument.commands, ["*IDN?"])

    def test_denied_lock(self):
        self.device.disconnect()
        self.instrument.lock_granted = False
        with self.assertRaises(BodeError) as caught:
            self.device.connect()
        self.assertEqual(caught.exception.code, "device_locked")
        self.assertFalse(self.device.connection_status()["connected"])

    def test_failed_connect_releases_acquired_lock(self):
        self.device.disconnect()
        self.instrument.reject.add(":FORM ASC")
        with self.assertRaises(BodeError):
            self.device.connect()
        self.assertEqual(self.instrument.commands[-1], ":SYST:LOCK:REL?")
        self.assertFalse(self.device.connection_status()["connected"])

    def test_limits_are_read_from_instrument(self):
        result = self.device.get_limits()
        self.assertEqual(result["frequency_max_hz"], 40000000)
        self.assertEqual(result["points_max"], 100000)
        count = len(self.instrument.commands)
        self.device.get_limits()
        self.assertEqual(len(self.instrument.commands), count)

    def test_sweep_validation_happens_before_any_write(self):
        for parameters in ({"start_hz": 0}, {"stop_hz": 50000000}, {"points": 100001},
                           {"start_hz": 1000, "stop_hz": 100}, {"points": 1.5},
                           {"dwell_seconds": 61}, {"bandwidth_hz": float("nan")}, {}):
            self.instrument.commands.clear()
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                self.device.configure_sweep(**parameters)
            self.assertFalse(any(not c.endswith("?") for c in self.instrument.commands))

    def test_upward_and_downward_frequency_order(self):
        self.device.configure_sweep(start_hz=2000000, stop_hz=3000000)
        commands = self.instrument.commands
        self.assertLess(commands.index(":SENS:FREQ:STOP 3000000"), commands.index(":SENS:FREQ:STAR 2000000"))
        self.instrument.commands.clear()
        self.device.configure_sweep(start_hz=10, stop_hz=100)
        commands = self.instrument.commands
        self.assertLess(commands.index(":SENS:FREQ:STAR 10"), commands.index(":SENS:FREQ:STOP 100"))

    def test_measurement_defaults_are_explicit(self):
        self.device.configure_measurement("GAIN")
        self.assertIn(":CALC:PAR:DEF GAIN", self.instrument.commands)
        self.device.configure_measurement("Z", "TSH", True)
        self.assertIn(":CALC:PAR:DEF Z,DEF", self.instrument.commands)
        self.assertIn(":SENS:Z:METH TSH,DEF", self.instrument.commands)
        with self.assertRaises(ValueError):
            self.device.configure_measurement("GAIN", "TSH")

    def test_aoff_is_not_immediate_output_control(self):
        self.device.configure_source(-10, True)
        self.assertIn(":SOUR:POW -10", self.instrument.commands)
        self.assertIn(":OUTP:AOFF 1", self.instrument.commands)
        self.assertNotIn(":OUTP ON", self.instrument.commands)
        with self.assertRaises(ValueError):
            self.device.configure_source(14)

    def test_input_gain_is_linear_and_settings_are_separate(self):
        result = self.device.input_settings("CH1", 10, 1000000, 5)
        self.assertEqual(result["gain_factor"], 5)
        self.assertIn(":INP:GAIN:CH1 5", self.instrument.commands)
        self.assertIn(":INP:ATT:CH1 10", self.instrument.commands)
        for bad in (0, 1000):
            with self.assertRaises(ValueError):
                self.device.input_settings("CH1", impedance_ohm=bad)

    def test_scpi_error_reports_partial_application_without_retry(self):
        self.instrument.reject.add(":SENS:BAND 99")
        with self.assertRaises(BodeError) as caught:
            self.device.configure_sweep(points=5, bandwidth_hz=99)
        self.assertEqual(caught.exception.code, "scpi_error")
        self.assertEqual(caught.exception.details["errors"][0]["code"], -221)
        self.assertEqual(self.instrument.values[":SENS:SWE:POIN"], "5")
        self.assertEqual(self.instrument.commands.count(":SENS:BAND 99"), 1)

    def test_bus_sequence_and_noninterleaved_result_layout(self):
        self.complete_bus_measurement()
        result = self.device.read_measurement()
        self.assertEqual(result["frequency_hz"], [10, 100, 1000])
        self.assertEqual(result["primary"], [1, 2, 3])
        self.assertEqual(result["secondary"], [-10, -20, -30])
        self.assertTrue(result["configuration_match_confirmed"])
        self.assertFalse(result["has_more"])

    def test_single_point_and_pagination(self):
        self.complete_bus_measurement()
        result = self.device.read_measurement(offset=1, limit=1)
        self.assertEqual(result["frequency_hz"], [100])
        self.assertEqual(result["primary"], [2])
        self.assertEqual(result["secondary"], [-20])
        self.assertEqual(result["total_points"], 3)
        self.assertTrue(result["has_more"])
        self.instrument.frequencies, self.instrument.data = "10", "1,-45"
        result = self.device.read_measurement()
        self.assertEqual(result["primary"], [1])
        self.assertEqual(result["secondary"], [-45])

    def test_stale_data_is_not_silently_accepted(self):
        with self.assertRaises(BodeError):
            self.device.read_measurement()
        self.complete_bus_measurement()
        self.device.configure_source(-5)
        with self.assertRaises(BodeError) as caught:
            self.device.read_measurement()
        self.assertEqual(caught.exception.code, "unverified_results")
        result = self.device.read_measurement(allow_unverified=True)
        self.assertFalse(result["configuration_match_confirmed"])

    def test_format_change_does_not_require_new_measurement(self):
        self.complete_bus_measurement()
        self.device.configure_result("SLOG")
        self.assertTrue(self.device.read_measurement()["configuration_match_confirmed"])

    def test_waiting_bus_init_alone_does_not_confirm_measurement(self):
        self.device.initiate()
        self.assertFalse(self.device.wait()["measurement_confirmed"])

    def test_internal_init_can_confirm_measurement(self):
        self.device.configure_trigger("INT", False)
        self.device.initiate()
        self.assertTrue(self.device.wait()["measurement_confirmed"])

    def test_calibration_completion_is_not_measurement_completion(self):
        self.device.execute_calibration("FULL", "THRU")
        self.assertIn(":SENS:CORR:FULL:THRU", self.instrument.commands)
        self.assertFalse(self.device.wait()["measurement_confirmed"])
        self.assertEqual(self.device.calibration_status("FULL"),
                         {"range": "FULL", "enabled": False, "active": False, "available": False})

    def test_busy_and_continuous_auto_modes_reject_results(self):
        self.instrument.condition = 1024 | 8
        with self.assertRaises(BodeError) as caught:
            self.device.read_measurement(allow_unverified=True)
        self.assertEqual(caught.exception.code, "device_busy")
        self.instrument.condition = 1024
        self.device.configure_trigger("INT", True)
        with self.assertRaises(BodeError):
            self.device.read_measurement(allow_unverified=True)

    def test_result_lengths_and_nonfinite(self):
        self.complete_bus_measurement()
        self.instrument.data = "1,2"
        with self.assertRaises(BodeError) as caught:
            self.device.read_measurement()
        self.assertEqual(caught.exception.code, "protocol_error")
        self.instrument.data = "1,NaN,3,-10,Inf,-30"
        result = self.device.read_measurement()
        self.assertEqual(result["primary"], [1, None, 3])
        self.assertEqual(result["secondary"], [-10, None, -30])
        json.dumps(result, allow_nan=False)

    def test_raw_write_does_not_append_error_queries(self):
        self.instrument.commands.clear()
        result = self.device.raw_write("*CLS")
        # write 只确认发送，查询作为收包屏障，不用计时等待来猜服务端是否已处理。
        self.device.raw_query("*IDN?")
        self.assertEqual(self.instrument.commands, ["*CLS", "*IDN?"])
        self.assertFalse(result["device_acceptance_checked"])

    def test_raw_queries_invalidate_confirmation(self):
        self.complete_bus_measurement()
        self.assertIn("Bode 100", self.device.raw_query("*IDN?")["response"])
        with self.assertRaises(BodeError):
            self.device.read_measurement()

    def test_calibration_file_paths_and_no_implicit_wait(self):
        path = r"C:\calibration\my gain.mcalx"
        self.device.calibration_file("save", path)
        self.assertIn(f':MMEM:STOR:CORR "{path}"', self.instrument.commands)
        self.device.calibration_file("load", path, "USER")
        self.assertIn(f':MMEM:LOAD:CORR:USER "{path}"', self.instrument.commands)
        self.assertNotIn("*OPC?", self.instrument.commands)
        for bad in ("relative.mcalx", "C:relative.mcalx", r"\relative.mcalx", "C:/x.txt", "C:/x\n.mcalx"):
            with self.subTest(path=bad), self.assertRaises(ValueError):
                self.device.calibration_file("save", bad)

    def test_short_delay_and_load_limits(self):
        result = self.device.calibration_model(load_ohm=100, short_delay_seconds=50e-12)
        self.assertEqual(result["load_ohm"], 100)
        self.assertAlmostEqual(result["short_delay_seconds"], 50e-12)
        for parameters in ({"load_ohm": 0}, {"load_ohm": 1001}, {"short_delay_seconds": 0.001}):
            with self.assertRaises(ValueError):
                self.device.calibration_model(**parameters)

    def test_abort_and_disconnect_do_not_change_continuous_or_output(self):
        self.instrument.commands.clear()
        self.device.abort()
        self.device.disconnect()
        self.assertEqual(self.instrument.commands, [":ABOR", ":SYST:ERR:ALL?", ":SYST:LOCK:REL?"])

    def test_timeout_discards_connection_without_retry(self):
        with self.assertRaises(BodeError) as caught:
            self.device.raw_query(":TEST:BLOCK?", 0.1)
        self.assertEqual(caught.exception.code, "timeout")
        self.assertFalse(self.device.connection_status()["connected"])
        self.assertEqual(self.instrument.commands.count(":TEST:BLOCK?"), 1)
        self.instrument.release_reply.set()
        self.device.connect()
        self.assertIn("Bode 100", self.device.raw_query("*IDN?")["response"])

    def test_binary_reply_closes_connection(self):
        with self.assertRaises(BodeError) as caught:
            self.device.raw_query(":TEST:BINARY?")
        self.assertEqual(caught.exception.code, "protocol_error")
        self.assertFalse(self.device.connection_status()["connected"])

    def test_same_connection_queries_are_serialized(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            replies = list(pool.map(lambda _: self.device.raw_query("*IDN?")["response"], range(12)))
        self.assertTrue(all("Bode 100" in reply for reply in replies))


class ConnectionFailureTests(unittest.TestCase):
    def test_startup_has_no_socket_side_effect(self):
        with patch("socket.create_connection") as connect:
            device = Bode100()
            self.assertFalse(device.connection_status()["connected"])
            device.disconnect()
            connect.assert_not_called()

    def test_refused_connection_is_structured(self):
        with patch("socket.create_connection", side_effect=ConnectionRefusedError()):
            device = Bode100()
            with self.assertRaises(BodeError) as caught:
                device.connect()
        self.assertEqual(caught.exception.code, "connection_error")

    def test_fragmented_reply_is_assembled(self):
        with patch("socket.create_connection") as connect:
            sock = connect.return_value
            sock.recv.side_effect = [b"OmicronLab, Bo", b"de 100, SERIAL, 3.50\r", b"\n", b"1\n", b'0,"No error"\n']
            device = Bode100()
            self.assertTrue(device.connect()["connected"])

    def test_extra_reply_is_not_reused_for_next_query(self):
        with patch("socket.create_connection") as connect:
            connect.return_value.recv.return_value = b"OmicronLab, Bode 100, S, V\nlate\n"
            device = Bode100()
            with self.assertRaises(BodeError) as caught:
                device.connect()
            self.assertEqual(caught.exception.code, "protocol_error")
            self.assertFalse(device.connection_status()["connected"])


if __name__ == "__main__":
    unittest.main()
