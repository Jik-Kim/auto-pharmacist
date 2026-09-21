# 저울 영점 재작업 프로토콜 (2026-09-21~, B 도징)

9/18·9/19 G1 측정 근거는 폐기했다 (`records/deprecated/scale_20260921/`). **영점(offset)부터 다시 잡는다.**
이전 측정은 6세트 × 30회였는데 한 번 돌리는 데 시간이 많이 걸려 조건을 바꿔 가며 확인하기 어려웠다.
이번에는 **페이즈당 5회**로 짧게 끊어 빠르게 돌리고, 값이 수상하면 그 페이즈만 다시 뜬다.

## 자세: `material_1` 고정

`material_1` (원료통 A, `posx [344.0, -298.0, 200.0, 90.0, -180.0, -90.0]`) 에서 잰다.
tool_force 는 JTS 기반이라 **자세가 바뀌면 편향이 같이 바뀐다.** `weigh_held` 가 실제로 재는 자세에서 떠야
보정값이 운영과 맞는다 — `workbench` 는 자세가 달라 옮겨가지 않는다.
이전 측정은 `material_3` 에서 떴으므로, 같은 값이 나오리라 가정하지 않는다.

## 페이즈 구성

한 페이즈 = **무게 한 점 × 5회 측정**(회차마다 표본 10개). 약 45초 걸린다.

| 페이즈 | 무게 | 목적 |
|---|---|---|
| 0 | 빈 그리퍼 | `reset_workpiece_weight` + 기준값 — 영점 그 자체. 도구가 자동으로 찍는다 |
| 1 | 빈 스쿱 (실측 32 g) | 영점 직선의 첫 점 |
| 2 | 스쿱 + 원료 중간량 | 두 번째 점 — 여기까지면 gain 직선이 선다 |
| 3 | 스쿱 + 원료 많이 | 세 번째 점 — **잔차가 의미를 가지려면 3점이 필요하다** (`calib.fit_gain` 경고) |

무게는 매번 **실제 저울로 재서** `--actual-g` 에 넣는다. 눈대중 값을 넣으면 그게 그대로 offset 오차가 된다.

## 실행

터미널 1 — 로봇 + 그리퍼 드라이버 (`cell.launch.py` 아님, skill_node 가 로봇을 잡으면 안 된다):

```
source tools/env.sh && ros2 launch m0609_rg2_bringup new_bringup.launch.py mode:=real host:=192.168.1.100
```

터미널 2 — 페이즈 1 (빈 스쿱, 영점 포함). 첫 페이즈만 `--goto-station` 과 영점을 잡는다:

```
source tools/env.sh && python3 ros2_ws/src/gmp_dosing/calibration/measure_g1.py \
    --actual-g 32 --object scoop --goto-station material_1 --gripper \
    --sets 1 --trials 5 --samples 10 --period 0.1 \
    --out records/g1_rezero_0921_material1.csv
```

페이즈 2·3 — **같은 파일에 이어 쓰고, 영점은 세션에 한 번만 잡는다** (`--no-reset`). 자세도 그대로 유지:

```
source tools/env.sh && python3 ros2_ws/src/gmp_dosing/calibration/measure_g1.py \
    --actual-g <저울로 잰 값> --object scoop --gripper --no-reset \
    --sets 1 --trials 5 --samples 10 --period 0.1 \
    --out records/g1_rezero_0921_material1.csv
```

요약:

```
python3 -m gmp_dosing.core.calib records/g1_rezero_0921_material1.csv --method tool_force
```

`--period 0.1` 인데 실제 간격이 0.8 초쯤 되는 것은 `get_workpiece_weight` 호출이 0.7 초 걸리기 때문이다.
그 덕에 표본이 독립이 된다 — **`--no-workpiece` 로 빼려면 `--period` 를 0.9 이상으로 직접 올려야 한다.**

## 페이즈마다 확인할 것

- **빈 그리퍼 기준값이 0 근처인가** — 아니면 등록 툴 무게·CoG 가 실제와 다르다. 영점이 여기서 이미 틀어진다.
- **표본 중 서로 다른 값 비율** — 100 % 가 아니면 표본 간격이 센서 갱신보다 짧다. σ 가 실제보다 작게 나온다.
- **회차 평균 3σ** — 이 값이 `min_resolvable_g` 의 바닥이다. 레시피는 tol 5 % 라 100 g 이면 허용 폭이 ±5 g 인데,
  3σ 가 그보다 크면 **이 저울로는 합격 판정을 못 한다.** 폐기 전 측정이 12.6~18 g 이었으므로 이번에도 같은
  수준이면 목표량·허용오차·측정 경로 중 하나를 바꾸는 논의가 필요하다 (조장·A).
- **회차 내부 σ p95** — `max_std_g` 의 바닥. 이보다 낮게 잡으면 정상 계량이 `valid=false` 로 떨어진다.

## 측정 결과 (채워 넣는다)

| 페이즈 | 실제 [g] | offset [g] | 회차 평균 3σ [g] | 회차 내부 σ p95 [g] | 표본 독립 | 비고 |
|---|---|---|---|---|---|---|
| 1 | | | | | | |
| 2 | | | | | | |
| 3 | | | | | | |

3점 직선: gain ____ · offset_g ____ · 잔차 최대 ____ g

확정되면 (1) CSV 를 `calibration/` 으로 복사해 커밋 (2) `config/scale_reference.yaml` 을 `status: measured` 로
채우고 (3) `common.yaml` 의 `scale.*` 갱신을 조장·A 에게 요청 (4) `test/test_dosing.py` 에 CSV→yaml 정합
검사를 되살린다.
