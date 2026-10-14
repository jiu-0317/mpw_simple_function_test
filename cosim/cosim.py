"""피코(MicroPython) 스크립트를 가짜 machine/time 모듈로 CPython 에서 실행하고,
보드(HCT245 -> HC14 반전 / LVC245A)와 칩(Icarus, 게이트 넷리스트 또는 RTL)을 lockstep 으로 연결.
사용 (cosim 폴더에서, gls.vvp/rtl.vvp 를 먼저 빌드):
  python3 cosim.py ../smoke_test.py <gls|rtl> [KEY=VALUE ...]   (스크립트 설정 덮어쓰기)
Linux/WSL 전용 (os.mkfifo).
"""
import os, sys, subprocess, types, re
import time as _rt

script, which = sys.argv[1], sys.argv[2]
overrides = dict(a.split("=", 1) for a in sys.argv[3:])
PIN_OVERHEAD_US = 3.0

for f in ("to_sim", "from_sim"):
    if os.path.exists(f): os.remove(f)
    os.mkfifo(f)
sim = subprocess.Popen(["vvp", "-n", f"{which}.vvp"], stdout=subprocess.DEVNULL)
tx = open("to_sim", "w"); rx = open("from_sim", "r")

class Board:
    now = 0.0; clk_hz = None; t_clk0 = None; ncyc = 0
    pins = {}          # gp -> physical level driven by pico
    miso = 0; done = 0
    xcount = 0
B = Board

def chip_inputs():
    if B.pins.get(15, 1) == 1:              # OE=1 -> HCT245 Hi-Z -> 풀 저항 기본값
        return 0, 1, 0, 0                   # rstn, ssn, sclk, mosi (칩 기준)
    inv = lambda gp: B.pins.get(gp, 0) ^ 1  # HC14 반전
    return inv(16), inv(18), inv(21), inv(19)   # rstn, ssn, sclk, mosi

def advance(us):
    t_end = B.now + us
    if B.clk_hz:
        P = 1e6 / B.clk_hz
        v = "".join(str(b) for b in chip_inputs())
        while B.t_clk0 + (B.ncyc + 1) * P <= t_end:
            tx.write(v + "\n"); tx.flush()
            o = rx.readline().strip()
            if "x" in o: B.xcount += 1
            B.miso = 1 if o[0] == "1" else 0
            B.done = 1 if o[1] == "1" else 0
            B.ncyc += 1
    B.now = t_end

class Pin:
    OUT, IN = 1, 0
    def __init__(self, gp, mode=0, value=None):
        self.gp = gp; self.mode = mode
        if mode == Pin.OUT: B.pins[gp] = value or 0
    def value(self, v=None):
        advance(PIN_OVERHEAD_US)
        if v is None:
            if self.gp == 22: return B.miso
            if self.gp == 17: return B.done
            return B.pins.get(self.gp, 0)
        B.pins[self.gp] = 1 if v else 0

class PWM:
    def __init__(self, pin): self.pin = pin
    def freq(self, f): B.clk_hz = f; B.t_clk0 = B.now; B.ncyc = 0
    def duty_u16(self, d): pass
    def deinit(self): advance(0); B.clk_hz = None

machine = types.ModuleType("machine"); machine.Pin = Pin; machine.PWM = PWM
ftime = types.ModuleType("time")
ftime.sleep_us = lambda n: advance(n)
ftime.sleep_ms = lambda n: advance(n * 1000)
ftime.ticks_ms = lambda: (advance(1.0), int(B.now // 1000))[1]
ftime.ticks_diff = lambda a, b: a - b
sys.modules["machine"] = machine; sys.modules["time"] = ftime

src = open(script, encoding="utf-8").read()
for k, v in overrides.items():
    src, n = re.subn(rf"^({k}\s*=\s*)[^#\n]*", rf"\g<1>{v}  ", src, flags=re.M)
    assert n == 1, k
src = src.replace('LOG_TO_FILE      = True', 'LOG_TO_FILE      = False')
g = {"__name__": "__main__"}
exec(compile(src, script, "exec"), g)
tx.close(); sim.wait(timeout=30)
print(f"\n[cosim] {which}: {B.ncyc} i_clk cycles, X outputs: {B.xcount}, virtual time {B.now/1e3:.1f} ms")
