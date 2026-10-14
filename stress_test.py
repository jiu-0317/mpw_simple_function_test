# stress_test.py
# SPI 명령 전달 안정성 측정 (smoke_test.py 와 같은 폴더/피코에 두고 실행)
#  COMPUTE(0x4000) -> done 1 이어야 함   : 안 오르면 miss  (명령 유실)
#  NOP(0xE000)     -> done 0 이어야 함   : 오르면  extra (없는 명령 발생)
#  i_clk 주파수별로 반복해서 어느 신호 문제인지 좁힌다.
#
#  실행: Thonny 에서 열고 Run  (smoke_test.py 가 피코에 저장돼 있어야 함)

import smoke_test as s
import time

N = 200                                   # 주파수당 반복 횟수
FREQS = (37_500_000, 10_000, 100_000, 1_000_000)      # 시험할 i_clk

def stress(n):
    miss = extra = 0
    for _ in range(n):
        s.frame(0x4000)                   # COMPUTE
        if not s.wait_done(1):
            miss += 1
        s.frame(0xE000)                   # NOP
        if s.wait_done(0):
            extra += 1
    return miss, extra


def main():
    print("stress test: %d pairs per frequency" % N)
    try:
        s.start_clock()
        time.sleep_ms(1)
        s.oe.value(0)
        for f in FREQS:
            s.clk.freq(f)
            s.clk.duty_u16(32768)
            s.T_HALF_US = max(50, int(8_000_000 / f) + 1)   # SCLK 반주기 >= i_clk 8주기
            s.GAP_US = 4 * s.T_HALF_US
            s.reset()
            t0 = time.ticks_ms()
            miss, extra = stress(N)
            dt = time.ticks_diff(time.ticks_ms(), t0) / 1000
            print("i_clk %8d Hz   miss %3d / %d   extra %3d / %d   (%.1f s)"
                  % (f, miss, N, extra, N, dt))
    finally:
        s.stop_all()


main()
