# smoke_test.py
# MyChip_FP_MAC MPW 칩 간단 동작 확인 (Raspberry Pi Pico / MicroPython)
#
#  피코 -> 74HCT245(5V) -> 74HC14(반전) -> 칩 입력 5개
#  피코 <- 74LVC245A(3.3V) <------------ 칩 출력 2개
#
#  칩 입력은 HC14 를 거치며 반전되므로, 이 코드는 "칩이 받는 값" 기준으로 쓰고
#  실제 핀에는 반전해서 내보낸다 (INVERT_INPUTS = True).
#
#  테스트 단계 (각 단계: 받은 값 기록 + 예상값 비교 -> PASS/FAIL)
#   T0 리셋 후 출력 확인          MISO=0, done=0
#   T1 NOP(0xE000)               rx=0x0000, done 0 유지
#   T2 COMPUTE(0x4000)  ★핵심    done -> 1   (SPI 수신 -> IR -> CONTROL 동작)
#   T3 NOP(0xE000)               done -> 0   (새 명령에 클리어)
#   T4 ACC 없이 READ             무시되어 done 0 유지, rx=0x0000
#   T5 최소 MAC (1.0 x 1.0 x 9)  결과 0x0051 (E4M3 9.0)   = 골든 bin 3
#   T6 원소 k 하나만 2.0x2.0     결과 0x0048 (E4M3 4.0) x 9 = 골든 bin 4
#
#  실행: Thonny 에서 열고 Run, 또는  mpremote run smoke_test.py
#  로그: 화면 출력 + 피코 내부 파일 log_<CHIP_ID>.txt (append)

from machine import Pin, PWM
import time

# =========================== 설정 ===========================
CHIP_ID          = "chip01"    # 로그 파일 이름/헤더에 사용
CLK_HZ           = 1_000_000     # i_clk 주파수. 통과 후 1_000_000 으로 올려서 재확인
INVERT_INPUTS    = True        # HC14 반전 보상 (칩 입력 5개)
STOP_ON_CRITICAL = True        # T0/T2 실패 시 뒤 단계 생략
DONE_TIMEOUT_MS  = 20          # done=1 을 기다리는 최대 시간
DONE_HOLD_MS     = 5           # done=0 '유지'를 확인하는 시간
LOG_TO_FILE      = True

# 칩 SPI 동기화 조건: SCLK 반주기 >= i_clk 4주기, 프레임 간격 >= i_clk 8주기 (여유 2배 이상)
_CYC_US   = 1_000_000 / CLK_HZ
T_HALF_US = max(50, int(8 * _CYC_US) + 1)
GAP_US    = max(100, int(32 * _CYC_US) + 1)
RST_US    = max(1000, int(16 * _CYC_US) + 1)

# =========================== 핀 ===========================
GP_CLK, GP_RSTN, GP_SSN, GP_MOSI, GP_SCLK = 20, 21, 17, 19, 18   # -> HCT245 -> HC14 -> 칩
GP_MISO, GP_DONE = 16, 22                                         # <- LVC245A <- 칩
GP_OE = 15                                                        # HCT245 OE (active low, 반전 없음)

oe = Pin(GP_OE, Pin.OUT, value=1)          # 버퍼 끈 상태로 시작 (칩은 풀 저항으로 안전 상태)


class ChipIn:
    """칩 입력 핀. value 는 '칩이 받는 논리값' 기준."""
    def __init__(self, gp, chip_level):
        self.pin = Pin(gp, Pin.OUT, value=self._phys(chip_level))

    @staticmethod
    def _phys(v):
        return (v ^ 1) if INVERT_INPUTS else v

    def __call__(self, v):
        self.pin.value(self._phys(v))


rstn = ChipIn(GP_RSTN, 0)   # 리셋 유지
ssn  = ChipIn(GP_SSN, 1)    # 선택 안 됨
sclk = ChipIn(GP_SCLK, 0)   # mode 0 idle low
mosi = ChipIn(GP_MOSI, 0)
miso = Pin(GP_MISO, Pin.IN)
done = Pin(GP_DONE, Pin.IN)
clk  = None

# =========================== 명령 워드 ===========================
CMD_LOAD_W, CMD_LOAD_I, CMD_COMPUTE, CMD_ACC, CMD_READ, CMD_NOP = 0, 1, 2, 3, 4, 7
E4M3_ONE, E4M3_TWO, E4M3_ZERO = 0x38, 0x40, 0x00


def word(cmd, data8=0, addr=0, mode=0):
    # [15:13] cmd | [12:5] data8 | [4:1] addr | [0] mode
    return ((cmd & 7) << 13) | ((data8 & 0xFF) << 5) | ((addr & 0xF) << 1) | (mode & 1)


NOP = word(CMD_NOP)          # 0xE000
COMPUTE = word(CMD_COMPUTE)  # 0x4000
ACC = word(CMD_ACC)          # 0x6000
READ_E4M3 = word(CMD_READ)   # 0x8000


def e4m3_to_float(b):
    s, e, m = (b >> 7) & 1, (b >> 3) & 0xF, b & 7
    if e == 15 and m == 7:
        return float("nan")
    v = (m / 8) * 2 ** -6 if e == 0 else (1 + m / 8) * 2 ** (e - 7)
    return -v if s else v

# =========================== 로그 / 판정 ===========================
_logf = None
results = {}      # test -> [n_pass, n_check]


def log(msg):
    print(msg)
    if _logf:
        _logf.write(msg + "\n")


def check(test, label, tx, rx, exp_rx, d, exp_d):
    """한 프레임(또는 한 관찰)의 결과를 기록하고 예상값과 비교."""
    ok = True
    if exp_rx is not None and rx != exp_rx:
        ok = False
    if exp_d is not None and d != exp_d:
        ok = False
    r = results.setdefault(test, [0, 0])
    r[1] += 1
    r[0] += 1 if ok else 0
    tx_s = "  ----" if tx is None else "0x%04X" % tx
    rx_s = "0x%04X" % rx
    erx_s = "  ----" if exp_rx is None else "0x%04X" % exp_rx
    ed_s = "-" if exp_d is None else str(exp_d)
    line = "%-3s %-14s tx=%s  rx=%s (exp %s)  done=%d (exp %s)  %s" % (
        test, label, tx_s, rx_s, erx_s, d, ed_s, "PASS" if ok else "FAIL")
    if not ok and exp_rx is not None and rx != exp_rx:
        line += "\n      rx  bin %s\n      exp bin %s" % (_bin16(rx), _bin16(exp_rx))
    log(line)
    return ok


def _bin16(v):
    s = ""
    for b in range(15, -1, -1):
        s += "1" if (v >> b) & 1 else "0"
        if b == 8:
            s += "_"
    return s


def passed(test):
    r = results.get(test)
    return r is not None and r[0] == r[1]

# =========================== 칩 제어 ===========================


def start_clock():
    global clk
    clk = PWM(Pin(GP_CLK))
    clk.freq(CLK_HZ)
    clk.duty_u16(32768)


def stop_all():
    """테스트 종료: 칩을 리셋 상태로 두고 버퍼 isolation."""
    rstn(0)
    ssn(1)
    sclk(0)
    mosi(0)
    time.sleep_us(RST_US)
    oe.value(1)
    if clk:
        clk.deinit()


def reset():
    rstn(0)
    ssn(1)
    sclk(0)
    mosi(0)
    time.sleep_us(RST_US)
    rstn(1)
    time.sleep_us(RST_US)


def frame(w):
    """16비트 SPI mode 0, MSB first. 보낸 워드 w, 받은 MISO 16비트를 반환."""
    rx = 0
    ssn(0)
    time.sleep_us(T_HALF_US)                 # 칩이 SSN 하강 감지 + TBUF 로드
    for b in range(15, -1, -1):
        mosi((w >> b) & 1)                   # SCLK low 구간: MOSI 세팅
        time.sleep_us(T_HALF_US)
        rx = (rx << 1) | miso.value()        # SCLK 상승 직전에 MISO 샘플
        sclk(1)                              # 칩이 상승엣지에서 MOSI 샘플
        time.sleep_us(T_HALF_US)
        sclk(0)                              # 칩이 하강엣지에서 다음 MISO 비트 시프트
    time.sleep_us(T_HALF_US)
    mosi(0)
    ssn(1)                                   # SSN 상승 -> 명령 실행
    time.sleep_us(GAP_US)
    return rx


def wait_done(expect):
    """expect=1: timeout 안에 1이 되면 1.  expect=0: HOLD 동안 한 번이라도 1이면 1."""
    if expect:
        t0 = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), t0) < DONE_TIMEOUT_MS:
            if done.value():
                return 1
        return 0
    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < DONE_HOLD_MS:
        if done.value():
            return 1
    return 0


def send(test, label, w, exp_rx, exp_d):
    rx = frame(w)
    d = wait_done(exp_d) if exp_d is not None else done.value()
    return check(test, label, w, rx, exp_rx, d, exp_d), rx


def load_all(test, W, I, mode=0, exp_rx=0):
    ok = True
    for a in range(9):
        ok &= send(test, "LOAD_W[%d]" % a, word(CMD_LOAD_W, W[a], a, mode), exp_rx, 1)[0]
    for a in range(9):
        ok &= send(test, "LOAD_I[%d]" % a, word(CMD_LOAD_I, I[a], a, mode), exp_rx, 1)[0]
    return ok


def mac(test, W, I, exp_result, exp_rx_before=0):
    """W/I 18개 로드 -> ACC -> READ(E4M3) -> NOP 로 결과 읽기."""
    load_all(test, W, I, 0, exp_rx_before)
    send(test, "ACC", ACC, exp_rx_before, 1)
    send(test, "READ", READ_E4M3, exp_rx_before, 1)
    ok, rx = send(test, "RESULT(NOP)", NOP, exp_result, 0)
    log("      result8=0x%02X -> E4M3 %s   (expected 0x%02X -> %s)" % (
        rx & 0xFF, e4m3_to_float(rx & 0xFF), exp_result & 0xFF, e4m3_to_float(exp_result & 0xFF)))
    return ok

# =========================== 테스트 단계 ===========================


def t0():
    log("\n[T0] 리셋 후 출력 확인")
    reset()
    time.sleep_ms(1)
    return check("T0", "after reset", None, miso.value(), 0, done.value(), 0)


def t1():
    log("\n[T1] NOP -> 응답 0, done 0 유지")
    return send("T1", "NOP", NOP, 0x0000, 0)[0]


def t2():
    log("\n[T2] COMPUTE -> done 1 (칩 생존 확인)")
    return send("T2", "COMPUTE", COMPUTE, 0x0000, 1)[0]


def t3():
    log("\n[T3] NOP -> done 0 으로 클리어")
    return send("T3", "NOP", NOP, 0x0000, 0)[0]


def t4():
    log("\n[T4] ACC 없이 READ -> 무시")
    send("T4", "READ", READ_E4M3, 0x0000, 0)
    return send("T4", "NOP", NOP, 0x0000, 0)[0]


def t5():
    log("\n[T5] 최소 MAC: 1.0 x 1.0 x 9 = 9.0 -> 0x0051")
    reset()
    return mac("T5", [E4M3_ONE] * 9, [E4M3_ONE] * 9, 0x0051, 0x0000)


def t6():
    log("\n[T6] 원소 k 하나만 2.0 x 2.0 = 4.0 -> 0x0048 (k=0..8)")
    reset()
    n_ok = 0
    for k in range(9):
        log("  -- k=%d" % k)
        W = [E4M3_ZERO] * 9
        I = [E4M3_ZERO] * 9
        W[k] = E4M3_TWO
        I[k] = E4M3_TWO
        # k>=1 이면 LOAD 프레임 MISO 에 직전 결과(0x0048)가 반복 출력되는 게 정상 -> 비교 안 함
        before = 0x0000 if k == 0 else None
        tag = "T6k%d" % k
        if mac(tag, W, I, 0x0048, before) and passed(tag):
            n_ok += 1
    results["T6"] = [n_ok, 9]
    return n_ok == 9

# =========================== 메인 ===========================


def summary():
    log("\n========== SUMMARY (%s, i_clk %d Hz) ==========" % (CHIP_ID, CLK_HZ))
    for t in ("T0", "T1", "T2", "T3", "T4", "T5", "T6"):
        r = results.get(t)
        if r is None:
            log("  %s  SKIP" % t)
        elif t == "T6":
            log("  %s  %s  (%d/%d k PASS)" % (t, "PASS" if r[0] == r[1] else "FAIL", r[0], r[1]))
        else:
            log("  %s  %s  (%d/%d checks)" % (t, "PASS" if r[0] == r[1] else "FAIL", r[0], r[1]))


def main():
    global _logf
    if LOG_TO_FILE:
        _logf = open("log_%s.txt" % CHIP_ID, "a")
    log("\n##### MyChip_FP_MAC smoke test  chip=%s  i_clk=%d Hz  T_half=%d us  gap=%d us  invert=%s #####"
        % (CHIP_ID, CLK_HZ, T_HALF_US, GAP_US, INVERT_INPUTS))
    try:
        # 전원 순서: OE=1(버퍼 끔) 상태에서 칩 기본값 설정 -> 클럭 시작 -> OE=0 -> 리셋
        start_clock()
        time.sleep_ms(1)
        oe.value(0)
        time.sleep_ms(1)

        if not t0() and STOP_ON_CRITICAL:
            log("!! T0 실패: 리셋/전원/출력 경로를 먼저 확인하세요.")
            return
        t1()
        if not t2() and STOP_ON_CRITICAL:
            log("!! T2 실패: 칩 핀에서 idle 레벨(ssn=5V, sclk=0V, rstn=5V)과 i_clk 를 확인하세요.")
            return
        t3()
        t4()
        t5()
        t6()
    finally:
        summary()
        stop_all()
        if _logf:
            _logf.close()
            _logf = None


if __name__ == "__main__":
    main()
