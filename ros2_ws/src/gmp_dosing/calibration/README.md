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

## 걸리면 여기부터

**아무 출력 없이 멈춘다** (`_robot_id` / `_robot_model` / `_srv_name_prefix` / `_topic_name_prefix` 네 줄만 찍히고 정지)
— 브링업을 띄우자마자 실행해서 `dsr_controller2` 가 아직 활성화되기 전이다. `DSR_ROBOT2` 의 조회·설정 함수는
`wait_for_service` 없이 `call_async` 부터 하고 기다리므로, 컨트롤러가 없으면 future 가 끝나지 않고 조용히 선다.
Ctrl-C 로 끄고 컨트롤러가 뜬 뒤 다시 실행한다. 확인:

```
ros2 control list_controllers -c /dsr01/controller_manager     # dsr_controller2 가 active 여야 한다
```

2026-09-21 에 실제로 걸려서 `measure_g1.py` 에 준비 확인(`wait_controller`)을 넣었다 — 이제는 30초 기다린 뒤
무엇이 문제인지 말하고 끝난다(`--controller-timeout` 으로 조절). 멈춰 있으면 그 버전이 아닌 것이다.

**`The passed service type is invalid`** — `ros2 service call` 을 쓸 때 `dsr_msgs2` 가 안 잡힌 것이다.
`source tools/env.sh` 로 ws_dsr 언더레이까지 올린다 (`ROS_DOMAIN_ID=70` 도 여기서 설정된다).

## 페이즈마다 확인할 것

- **빈 그리퍼 기준값이 0 근처인가** — 아니면 등록 툴 무게·CoG 가 실제와 다르다. 영점이 여기서 이미 틀어진다.
- **표본 중 서로 다른 값 비율** — 100 % 가 아니면 표본 간격이 센서 갱신보다 짧다. σ 가 실제보다 작게 나온다.
- **회차 평균 3σ** — 이 값이 `min_resolvable_g` 의 바닥이다. 레시피는 tol 5 % 라 100 g 이면 허용 폭이 ±5 g 인데,
  3σ 가 그보다 크면 **이 저울로는 합격 판정을 못 한다.** 폐기 전 측정이 12.6~18 g 이었으므로 이번에도 같은
  수준이면 목표량·허용오차·측정 경로 중 하나를 바꾸는 논의가 필요하다 (조장·A).
- **회차 내부 σ p95** — `max_std_g` 의 바닥. 이보다 낮게 잡으면 정상 계량이 `valid=false` 로 떨어진다.

## 측정 결과 (채워 넣는다)

### material_1 · 2026-09-21 17:34~17:41 (`records/g1_rezero_0921_material1.csv`)

tool_force:

| 페이즈 | 실제 [g] | offset [g] | 회차 평균 3σ [g] | 회차 내부 σ p95 [g] | 표본 독립 |
|---|---|---|---|---|---|
| 1 | 32 | 41.28 | 20.66 | 23.87 | 100 % (820 ms) |
| 2 | 83 | 55.03 | 19.16 | 23.15 | 100 % (820 ms) |
| 3 | 133 | 32.25 | 31.77 | 24.01 | 100 % (820 ms) |

3점 직선: **tool_force** gain 0.8862 · offset_g 47.387 · 잔차 최대 10.83 g
        **workpiece**  gain 0.7950 · offset_g 11.275 · 잔차 최대 **2.15 g**

읽은 것:
- **gain 은 재현됐다.** 폐기한 9/19 값 0.8859 와 이번 0.8862 — 자세도(material_3→1) 날짜도 무게 점도
  다른데 0.0003 차이다. gain 은 센서 스케일이라 자세에 안 딸린다고 봐도 되겠다.
- **offset 은 완전히 다르다.** 폐기값 247.091 → 47.387. 200 g 차이다. 영점은 자세·툴 등록·세션에
  딸린 값이고, 다시 잡아야 했던 게 맞다. 무게별 단일점 offset(41/55/32)도 서로 23 g 흩어져 있다.
- **⚠️ 측정이 불안정하다 — 이게 제일 큰 문제.** 회차 내부 σ p95 가 23~24 g 으로 폐기 전(7.8 g)의 3배다.
  한 회차 10표본이 60~79 g 폭으로 움직이고, 시계열이 랜덤이 아니라 추세를 그린다
  (133 g 회차 1: 77→65→68→74→74→85→101→100→109→112 로 단조 증가).
  정착 부족·잔류 진동·파지 중 힘 변화 중 하나로 보이며, 이 상태로는 보정 직선을 논해도 의미가 얇다.
- **분해능은 여전히 부족하다.** 3σ 가 20~32 g. 레시피 tol 5 % 면 100 g 에 ±5 g 인데 못 가른다.
  세트가 1개라 다시잡기 오차가 빠진 **낙관적** 값인데도 이렇다.
- **workpiece 를 다시 볼 만하다.** 잔차가 tool_force 의 1/5(2.15 vs 10.83 g)이고, 32 g 에서 offset 7.2 로
  거의 맞았다. 9/19 에 "reset 이 먹지 않는다"며 탈락시킨 근거가 이번엔 재현되지 않았다 — 그 판단도
  폐기 대상이었던 셈이다.

확정되면 (1) CSV 를 `calibration/` 으로 복사해 커밋 (2) `config/scale_reference.yaml` 을 `status: measured` 로
채우고 (3) `common.yaml` 의 `scale.*` 갱신을 조장·A 에게 요청 (4) `test/test_dosing.py` 에 CSV→yaml 정합
검사를 되살린다.
