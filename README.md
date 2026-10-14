# simple_function_test — MyChip_FP_MAC 간단 동작 확인 (Raspberry Pi Pico)

폴더의 대규모 테스트 벡터(`test_vector`) 대신, 피코가 SPI 프레임 몇 개만 보내서
**칩이 살아 있는지 → 데이터 경로가 맞는지**를 단계별로 확인하는 스모크 테스트.

```
simple_function_test/
├─ smoke_test.py               피코에 올려서 실행하는 파일 (이것만 있으면 됨)
├─ expected_log_100kHz.txt     게이트 넷리스트 공동 시뮬에서 나온 기대 출력 (비교용)
└─ cosim/                      PC 검증용: smoke_test.py 를 칩 넷리스트와 연결해 미리 돌려보는 도구
   ├─ cosim.py
   └─ tb_pipe.v
```

## 1. 하드웨어 전제

```
피코(3.3V) ──▶ 74HCT245(5V) ──▶ 74HC14(5V, 반전) ──▶ 칩 입력 5개
피코(3.3V) ◀── 74LVC245A(3.3V) ◀───────────────────── 칩 출력 2개
```

| 신호 | 피코 GPIO (물리 핀) | 비고 |
|---|---|---|
| i_clk | GP20 (26) | PWM, HC14 반전 |
| i_rstn | **GP16 (21)** | HC14 반전 |
| i_ssn | **GP18 (24)** | HC14 반전 |
| i_mosi | GP19 (25) | HC14 반전 |
| i_sclk | **GP21 (27)** | HC14 반전 |
| o_miso | **GP22 (29)** | LVC245A, 반전 없음 |
| o_cmd_done | **GP17 (22)** | LVC245A, 반전 없음 |
| HCT245 OE | GP15 (20) | 10kΩ 풀업(3.3V), 반전 없음 |

(2026-10-14 보드 배선에 맞춰 rstn/sclk/ssn/done/miso 의 GPIO 를 변경함.
출력 핀 GP16·GP18·GP21 은 HCT245 입력(A)에, 입력 핀 GP17·GP22 는 LVC245A 출력(B)에 연결돼 있어야 한다.)

코드는 **칩이 받는 값 기준**으로 작성돼 있고, 칩 입력 5개는 핀에 반전해서 내보낸다
(`INVERT_INPUTS = True`).

## 2. 실행

1. 피코에 MicroPython 펌웨어 설치
2. Thonny 에서 `smoke_test.py` 를 열고 **Run** (또는 PC 에서 `mpremote run smoke_test.py`)
3. 화면에 프레임마다 결과가 찍히고, 마지막에 SUMMARY 가 나온다
4. 피코 내부에 `log_<CHIP_ID>.txt` 로 같은 내용이 **추가 기록**된다
   (Thonny 파일 창에서 PC 로 내려받기 / `mpremote cp :log_chip01.txt .`)

칩을 바꿀 때마다 `CHIP_ID` 를 바꿔 주면 칩별 로그가 따로 쌓인다.

### 설정 (파일 맨 위)

| 변수 | 기본값 | 설명 |
|---|---|---|
| `CHIP_ID` | `"chip01"` | 로그 파일 이름 |
| `CLK_HZ` | `100_000` | i_clk. 통과하면 `1_000_000` 으로 올려 재확인 |
| `INVERT_INPUTS` | `True` | HC14 반전 보상 |
| `STOP_ON_CRITICAL` | `True` | T0/T2 실패 시 뒤 단계 생략 |
| `DONE_TIMEOUT_MS` | `20` | done=1 대기 최대 시간 |
| `DONE_HOLD_MS` | `5` | done=0 유지 확인 시간 |

SCLK 반주기·프레임 간격은 `CLK_HZ` 로부터 자동 계산된다
(칩의 2단 동기화 조건: 반주기 ≥ i_clk 4주기 → 8주기로 여유 확보).

## 3. 테스트 단계와 예상값

프레임 포맷: `[15:13] cmd | [12:5] data8 | [4:1] addr | [0] mode`, SPI mode 0, MSB first.
READ 결과는 **다음 프레임**의 MISO 로 `0x00RR` 형태로 나온다.

| 단계 | 보내는 워드 | 예상 MISO | 예상 done | 확인 대상 |
|---|---|---|---|---|
| T0 리셋 | (핀 읽기) | 0 | 0 | 전원·리셋·출력 경로 |
| T1 NOP | `0xE000` | `0x0000` | 0 유지 | SPI 프레이밍 |
| **T2 COMPUTE** | `0x4000` | `0x0000` | **1** | SPI 수신→IR→CONTROL (칩 생존) |
| T3 NOP | `0xE000` | `0x0000` | 0 (클리어) | 명령 펄스 |
| T4 READ(ACC 없이) | `0x8000`, `0xE000` | `0x0000` | 0 유지 | 무시 조건 |
| **T5 최소 MAC** | LOAD_W `0x0700..0x0710`, LOAD_I `0x2700..0x2710`, ACC `0x6000`, READ `0x8000`, NOP `0xE000` | 결과 **`0x0051`** (9.0) | LOAD/ACC/READ 매번 1 | 데이터 경로 전체 (= 골든 bin 3) |
| T6 원소 k 스윕 | k=0..8, W[k]=I[k]=2.0 나머지 0 | 결과 **`0x0048`** (4.0) ×9 | 매번 1 | RF 주소·FPU 그룹·스케줄 (= 골든 bin 4) |

- W/I 레지스터는 리셋이 없는 래치이므로 MAC 전에 18개를 모두 쓴다.
- T6 에서 k≥1 의 LOAD 프레임 MISO 에는 직전 결과 `0x0048` 이 반복되는 게 정상 → 비교하지 않는다.
- 예상값 출처: `test_vector/out/results_decoded.txt` (bin 3 = 0x51, bin 4 = 0x48) 와 RTL 동작.

## 4. 로그 읽는 법

```
T2  COMPUTE        tx=0x4000  rx=0x0000 (exp 0x0000)  done=1 (exp 1)  PASS
T5  RESULT(NOP)    tx=0xE000  rx=0x0051 (exp 0x0051)  done=0 (exp 0)  PASS
      result8=0x51 -> E4M3 9.0   (expected 0x51 -> 9.0)
```
FAIL 이면 받은 값과 기대값을 2진수로 함께 찍는다 → 한 비트씩 밀렸는지(샘플 타이밍),
특정 비트만 틀렸는지(데이터 경로) 구분.

정상이라면 결과는 `expected_log_100kHz.txt` 와 줄 단위로 같아야 한다.

## 5. 실패 시 확인 순서

| 증상 | 확인 |
|---|---|
| T0 에서 MISO/done = 1 | 리셋이 안 걸림 → **칩 핀**에서 i_rstn 이 0V 인지 (반전 방향) |
| **T2 done 이 안 올라옴** | 칩 핀 idle 레벨: **ssn=5V, sclk=0V, rstn=5V**, i_clk 파형. 그다음 `CLK_HZ` 유지한 채 반주기 늘려보기 |
| done 은 되는데 MISO 가 항상 0/1 | LVC245A (DIR=3.3V, OE=GND, VCC=3.3V), GP16 배선 |
| T5 결과가 한 비트 밀림 (`0x00A2`, `0x0028`) | MISO 샘플 시점 문제 |
| T5 PASS, T6 의 일부 k 만 FAIL | 해당 주소 RF / FPU 그룹 — 칩 결함 후보 |

오실로스코프는 **칩 핀(HC14 출력)** 에서 볼 것. 피코 쪽 핀은 반전된 값이다.

## 6. (선택) PC 에서 미리 검증하기 — cosim/

`smoke_test.py` 를 수정했을 때, 피코 없이 **칩 게이트 넷리스트(TOP_new.v)** 와 연결해 돌려볼 수 있다.
가짜 `machine`/`time` 모듈로 스크립트를 그대로 실행하고, 보드 동작(HCT245 OE 격리 + 풀 저항,
HC14 반전)을 흉내 내서 Icarus 시뮬레이션과 i_clk 1사이클 단위로 맞물려 돈다.
Linux 또는 WSL 필요 (named pipe 사용).

```
cd simple_function_test/cosim
TV=../../test_vector
iverilog -g2012 -Dno_power_gate -o gls.vvp tb_pipe.v $TV/netlist/TOP_new.v $TV/lib/scl_sim.v
iverilog -g2012 -DRTL           -o rtl.vvp tb_pipe.v ../../MyChip_FP_MAC/hdl/src/*.v
python3 cosim.py ../smoke_test.py gls                       # 기본 설정
python3 cosim.py ../smoke_test.py gls CLK_HZ=1_000_000      # 설정 덮어쓰기
python3 cosim.py ../smoke_test.py rtl INVERT_INPUTS=False   # 일부러 틀린 설정 -> T2 FAIL 이 나야 정상
```
(`scl_sim.v` 가 없으면 `test_vector/run_all.sh` 를 한 번 돌리거나
`python3 $TV/lib/prep_lib.py $TV/lib/scl.v $TV/lib/scl_sim.v`)

검증 결과 (작성 시점):
- 게이트 넷리스트, 100 kHz: 전 단계 PASS, 출력 X 0회
- 게이트 넷리스트, 1 MHz: 전 단계 PASS
- 반전 끄기(배선 실수 모사): T2 에서 FAIL 로 정상 검출
