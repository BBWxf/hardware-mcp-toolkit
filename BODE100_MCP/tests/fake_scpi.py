"""仅用于测试的文本 SCPI 服务，不模拟真实电气测量。"""

import socketserver
import threading


class Instrument:
    def __init__(self):
        self.commands = []
        self.errors = []
        self.reject = set()
        self.identity = "OmicronLab, Bode 100, TEST001, 3.50"
        self.lock_granted = True
        self.condition = 1024
        self.read_started = threading.Event()
        self.release_reply = threading.Event()
        self.values = {
            ":CALC:PAR:DEF": "GAINphase", ":CALC:FORM": "SLIN",
            ":CALC:ZPAR:DEF": "Z", ":SENS:Z:METH": "P1R",
            ":SENS:FREQ:STAR": "10", ":SENS:FREQ:STOP": "1000000",
            ":SENS:SWE:POIN": "3", ":SENS:SWE:TYPE": "LOG", ":SENS:BAND": "300",
            ":SENS:SWE:DWEL": "0", ":SOUR:POW": "-20", ":OUTP:AOFF": "1",
            ":TRIG:SOUR": "BUS", ":INIT:CONT": "0",
            ":SENS:AVER:STAT": "0", ":SENS:AVER:COUN": "10",
            ":SENS:CORR:IMP": "50", ":SENS:CORR:LOAD": "50", ":SENS:CORR:SDEL": "0",
        }
        for channel in ("CH1", "CH2"):
            for setting, value in (("ATT", "20"), ("IMP", "1000000"), ("GAIN", "1")):
                self.values[f":INP:{setting}:{channel}"] = value
        for scope in ("FULL", "USER"):
            for setting in ("ENAB", "ACT", "AVAI"):
                self.values[f":SENS:CORR:{scope}:{setting}"] = "0"
        self.frequencies = "10,100,1000"
        self.data = "1,2,3,-10,-20,-30"

    def respond(self, command):
        self.commands.append(command)
        if command in self.reject:
            self.errors.append('-221,"Settings conflict"')
            return None
        if command == "*IDN?":
            return self.identity
        if command == ":SYST:LOCK:REQ?":
            return "1" if self.lock_granted else "0"
        if command == ":SYST:LOCK:REL?":
            return "1"
        if command == ":SYST:LOCK:OWN?":
            return '"ScpiServer TEST localhost"'
        if command == ":SYST:ERR:ALL?":
            response = ",".join(self.errors) or '0,"No error"'
            self.errors.clear()
            return response
        if command == ":STAT:OPER:COND?":
            return str(self.condition)
        if command == "*OPC?":
            return "1"
        if command == ":SENS:SWE:TIME?":
            return "0.25"
        if command == ":SENS:FREQ:DATA?":
            return self.frequencies
        if command == ":CALC:DATA:SDAT?":
            return self.data
        limits = {":SERV:SWE:FREQ:MIN?": "1", ":SERV:SWE:FREQ:MAX?": "40000000",
                  ":SERV:SWE:POIN?": "100000", ":SERV:SWE:POW:MIN?": "-30",
                  ":SERV:SWE:POW:MAX?": "13"}
        if command in limits:
            return limits[command]
        if command == ":TEST:BLOCK?":
            self.read_started.set()
            self.release_reply.wait(3)
            return "late"
        if command == ":TEST:BINARY?":
            return "#18abcdefgh"
        if command.endswith("?"):
            return self.values.get(command[:-1], "0")
        if " " in command:
            header, value = command.split(" ", 1)
            if header in self.values:
                value = value.split(",")[0]
                self.values[header] = "GAINphase" if value == "GAIN" else value
            return None
        return None


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            for line in self.rfile:
                command = line.decode("ascii").strip()
                reply = self.server.instrument.respond(command)
                if reply is not None:
                    self.wfile.write((reply + "\n").encode("ascii"))
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # 超时测试会有意在假设备回包前关闭 TCP 会话。
            return


class FakeServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, instrument=None):
        self.instrument = instrument or Instrument()
        super().__init__(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.instrument.release_reply.set()
        self.shutdown()
        self.server_close()
        self.thread.join(timeout=3)
