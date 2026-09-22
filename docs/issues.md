# 개발 이슈 리스트

> **동결 (2026-09-21).** 이 파일은 더 고치지 않는다. 이슈의 정본은 **[GitHub Issues](https://github.com/Jik-Kim/auto-pharmacist/issues)** 다 — `I-0xx` 항목 전부가 같은 제목으로 옮겨졌고 해소된 것은 Closed 다 (조장 승인, `AGENTS.md` 「이슈 · 진행」). 여기 남긴 것은 9/21 시점 스냅샷이다.

발견 즉시 여기에 적고 팀 채널에 알린다. 담당이 정해지지 않은 이슈는 조장이 배정한다. 등록 규칙은 `AGENTS.md` 「이슈 · 진행」.
**상태는 아래 표에만 적는다. 내용과 조치는 상세 절에만 적는다.**

## 열린 이슈

| ID | 심각도 | 상태 | 담당 | 제목 |
|---|---|---|---|---|
| [I-001](#i-001) | **상** | **열림** | A + B | 외력 분해능 측정 완료 — 0.82초 실물 재측정·3점 보정 검증이 남아 있다 |
| [I-002](#i-002) | 중 | 열림 | A | 실물 그리퍼 드라이버가 `grip`·안전스위치 비트를 토픽으로 내지 않는다 — 파지는 폭 추론 |
| [I-003](#i-003) | 하 | 열림 | A | (선택) 드라이버 포크 — `OnRobotRGInput` 발행 추가·파지력 수치 설정. 벤더 패키지는 안 고친다 |
| [I-004](#i-004) | 중 | 열림 (실물 검증 대기) | A | 비동기 이동 취소·MoveStop 구현 — 실물 감속 정지·인터락 응답 확인 필요 |
| [I-005](#i-005) | 중 | 열림 | A | 가상 모드에서 `get_tool_force`/`get_workpiece_weight` 가 값을 주는지 미확인 (Q-07) |
| [I-007](#i-007) | **상** | ~~해소~~ (9/18 v1.2) | A + C | **계약 v1.2 — `weigh_scoop`(들고 있는 스쿱 계량) Action 과 `Deviation.kind VERIFY_MISMATCH`** — D-22 6단계 흐름이 이 계약에 걸려 있다 |
| [I-008](#i-008) | 중 | 열림 | C + 조장 | `ScoopCycle` 의 6축 wrench 통계를 채울 경로가 없다 — 계량 스킬이 `WeightReading` 만 돌려준다 |
| [I-009](#i-009) | 중 | ~~해소~~ (9/18) | 조장 + C·D | 계약 파일의 상수가 응답 절에 있어 `InterlockRequest.ENTER` 가 `AttributeError` 였다 |

## 해결된 이슈

| ID | 담당 | 제목 |
|---|---|---|
| [I-006](#i-006) | C | **9/19 해소 — 스쿱 A/B/C 손잡이 폭 15.5/18/28 mm 확정** |

---

### I-001
외력 분해능 측정 완료·보정 검증 진행 중 · 심각도 상 · A + B · 등록 9/16
- **최초 문제**: 툴 1.320 kg에서 30 g(0.3 N) 변화를 JTS 기반 추정으로 구분할 수 있는지 측정 근거가 없었다. `get_workpiece_weight`와 `get_tool_force`는 같은 JTS에서 나온다.
- **9/18 G1 결과 (tool_force, 스쿱+원료 총 133 g (빈 스쿱 32 g))**: 회차 평균 σ 6.0 g · 3σ 18.0 g → `min_resolvable_g 19`, `max_std_g 5`, `offset_g 260.2`(tool_force 전용, gain 1 임시). 원시 CSV·재계산 모듈을 `gmp_dosing/calibration/`·`core/calib.py` 에 넣었다 (PR #22, C 가 이어서). 판정 근거는 Q-11 로.
- **9/19 재측정 (C, `calibration/measure_g1.py`, 두 경로 동시)**: 32 g·132 g 각 3세트×30회. tool_force 3σ **12.6 g** (표본 간격 0.82 s 로 독립), 두 점 gain 0.886·offset 247.1 → `common.yaml` method tool_force·gain·offset·max_std_g 10 반영. **workpiece 탈락** — reset 이 success 인데 빈 그리퍼 869 g, 무게를 올리면 값이 내려감 (D-07 확정). 실물 첫 PC 에 `pymodbus` 가 없어 그리퍼 드라이버가 죽었던 것도 이날 발견 (T0 에 추가).
- **완료 범위**: `scale.period_s=0.82`를 MeasureForce·WeighHeld·WeighContainer에 연결하고, 표본 사이 NUDGE 관측값이 계량 통계에 섞이지 않도록 분리했다. 관련 단위 테스트 60건이 통과했다. 현재 `min_resolvable_g=19`는 유지한다.
- **남은 것**: (a) 0.82초 설정으로 실물 재측정해 표본 중복·3σ·계량 소요시간·NUDGE 관측을 확인한 뒤 `min_resolvable_g` 19→14 적용 판단 (b) 3점째(≈86 g)로 gain 직선 확인. 측정 자체는 완료됐지만 보정값 확정 전이므로 **열림** 유지.

### I-002
드라이버가 `grip` 비트를 토픽으로 내지 않는다 · 중 · A · 9/16
- **내용**: `comModbusTcp.getStatus()` 는 `grip`·`s1_t`·`s2_t`·`safety` 를 dict 로 돌려주지만 `OnRobotRGControllerServer` 는 `JointState` 만 발행한다 (`/onrobot_joint_states`, 50 Hz). `_OnRobotRGStatusListener` 가 구독하는 `OnRobotRGInput` 은 아무도 발행하지 않는다.
- **조치**: 계약 2절 폭 추론으로 판정한다. `PROJECT_RULES.md` 3-3 의 "`gsta` bit1 직접 통보" 는 정정.

### I-003
(선택) 드라이버 포크 · 하 · A
- 서버의 `getStatus()` 타이머에 `OnRobotRGInput` 발행 10줄과 `sendCommand` 에 `f<N>` 파지력 수치 명령을 더하면 I-002 와 D-06 스텝 우회가 사라진다. 벤더 패키지 대신 **포크 패키지**(`gmp_skills/adapters` 에서 선택)로. 9/17 G2 결과를 보고 할지 정한다.

### I-004
비동기 이동 취소·감속 정지 · 중 · A
- **최초 문제 (9/18)**: 블로킹 `DSR_ROBOT2.movel`이 끝나기 전에는 같은 워커가 취소를 처리하지 못해 인터락 `ENTER` 응답이 늦어졌다. Python 래퍼에 `stop()`이 없어 비동기 이동과 별도 정지 경로가 필요했다.
- **반영한 구현 (9/20 확인)**: `adapters/dsr_arm.py`의 `movej_cancellable()`·`movel_cancellable()`은 `amovej/amovel` 실행 후 `wait_motion_cancellable()`에서 `check_motion`과 취소·시간 초과를 감시한다. 취소·시간 초과 시 `stop_motion()`이 `dsr_controller2/motion/move_stop`에 `MoveStop(DR_SSTOP)`을 요청하고 서비스 응답을 확인한다. DSR 전용 노드 호출은 단일 워커에서 직렬 처리한다.
- **적용 범위**: `MoveToStation`·`SafePose`뿐 아니라 `skill_node`가 연결한 `arm.cancel_requested`를 통해 Scoop·Pour·ReturnMaterial·Weigh 내부 `DsrArm.movel()`도 취소 가능한 경로를 사용한다. 모든 장치 호출·힘제어·계량 자체의 즉시 취소를 의미하지는 않는다.
- **검증 근거와 남은 조치**: `test/test_dsr_arm.py`에 감속 정지 요청·사전 취소·내부 직선 이동 취소 단위 테스트가 있다. 서비스 성공 응답은 실제 완전 정지의 증거가 아니므로 사용자가 실물에서 이동 중 취소·시간 초과 시 감속/완전 정지, 후속 작업 차단, 인터락 `ENTER` 응답 지연을 확인한 뒤 이슈를 닫는다.

### I-005
가상 모드 힘값 · 중 · A
- 에뮬레이터가 힘을 시뮬레이션하지 않을 가능성이 크다(매뉴얼 6.1 주의: "시뮬레이션 환경에서 정상 동작하지 않을 수 있음"). 9/16 밤 확인. 안 주면 `scale.simulated:=true` 로 도징·상태기계는 가상에서 계속 개발한다.
- **9/20 통합 확인**: 격리 도메인에서 에뮬레이터·`skill_node` 자가진단과 `MoveToStation(safe)`는 성공했다. 다만 가상 그리퍼가 명령 폭에 정확히 닫혀 폭 추론식 `final_width > target + margin`이 항상 거짓이므로 `PICK_CONTAINER`가 `GRIP_FAIL` 4회 뒤 ERROR로 끝난다. `scale.simulated=true`의 계량값도 `valid=false`라 진짜 C+A 레시피 완주에는 별도의 가상 물체/계량 모델이 필요하다.

### I-006
스쿱 치수 · 하 · C
- ✅ **해소 (9/19)**: A/B/C 손잡이 폭을 각각 **15.5/18/28 mm**로 확정해 `common.yaml`·`stations.yaml`에 반영했다. `SetGripper.final_width_mm` 정밀도 실측과 WRONG_TOOL 연동은 구현 TODO로 별도 유지한다.

### I-007
`weigh_scoop` 계약 · 상 · A + C · 등록 9/17
- **내용**: 로봇이 저울이라 `WeighContainer`(용기 파지 → 들어 올림 → 읽기 → 내려놓기)는 그리퍼가 비어야 한다. 종전 SCOOP→POUR→WEIGH 는 스쿱을 든 채 용기를 잡는 모순. D-22 로 원료마다 스쿱을 든 채 세 번(빈 스쿱·붓기 전·붓기 후) 재고, 용기는 배치 끝 VERIFY 한 번 재는 흐름으로 바꿨다 (`process_fsm.py`, 테스트 9건 통과).
- **필요한 계약**: (a) 들고 있는 것을 그대로 재는 스킬 — `WeighHeld` Action 신설 또는 `WeighContainer` 에 `mode: held|container` 필드. 입력 `tare_g`, 결과 `WeightReading`. (b) `Deviation.kind` 에 `VERIFY_MISMATCH` **와 `BATCH_OUT_OF_SPEC`** — 9/17 조장 합의로 `VERIFY` 를 **계측 신뢰성**(Σ투입량 대조)과 **제품 판정**(레시피 총량 대조, 허용치 `Σ(target×tol)`) 둘로 나눴다. (c) `WeightReading` 을 스쿱 계량에도 쓰므로 `weight` 토픽에 무엇을 잰 것인지 구분 필드(`subject: scoop|container`)가 있으면 HMI 그래프가 편하다 (D 와).
- **임시**: 계약 확정 전 process_node 는 `weigh_scoop` 를 `measure_force`(정지 외력) 로 흉내 내거나 `weigh_container` 를 그대로 부른다 (가상에서는 값이 없으므로 `scale.simulated` 로 충분).
- **조치·남은 것**: `_pour_fraction` 은 B 의 `dosing.py` 로 이관 (별건).
- ✅ **해소 (9/18)**: `action/WeighHeld.action` 신설 — 계량 후 **계량 자세에 머물고**, 빈 그리퍼면 `success=false`. phase 는 `LIFT`/`SETTLE`/`MEASURE` 라 `WeighContainer` 와 `GRIP`·`PLACE` 가 달라 `mode` 로 합치지 않았다. `Deviation.kind` 에 `VERIFY_MISMATCH`(9)·`BATCH_OUT_OF_SPEC`(10)·`WRONG_TOOL`(11) 추가. (c) `WeightReading` 에 **`subject`(`scoop`/`container`) 추가**. 처음에는 요청 종류로 갈린다고 봤으나, 액션 종류는 `process_node` 안에서만 알고 `weight` 토픽으로 나가는 순간 사라진다 — D 의 `record_node`·HMI 는 구분할 방법이 없었다. 배치 1건에 스쿱 9회(3원료×3)·용기 2회(TARE·VERIFY)라 섞이면 그래프가 못 읽힌다. `station` 은 둘 다 `workbench` 이라 쓸 수 없다. `schema.sql` `weights.subject`·`record_node` 도 같이 반영.

### I-008
`ScoopCycle` 6축 wrench 통계를 채울 경로가 없다 · 중 · C + 조장 · 등록 9/18
- **내용**: `ScoopCycle` 은 학습 원본으로 계량 3회마다 `*_wrench`·`*_wrench_std`·`*_wrench_samples` (각 6축)를 요구한다. 그런데 그 값을 만드는 계량 스킬 `WeighHeld`·`WeighContainer` 는 **`WeightReading` 만** 돌려준다 — wrench 가 결과에 없다. 6축을 주는 것은 `MeasureForce` 서비스뿐인데, 계량 중에 따로 부르면 **같은 표본 구간이 아니게 되어** 메시지 주석("각 WeightReading.header와 같은 표본 구간")을 어긴다.
- **지금 조치**: `process_node` 가 `tare_wrench_valid`·`pre_pour_wrench_valid`·`post_pour_wrench_valid` 를 **false** 로 채운다. 0 을 그냥 두면 학습 단계에서 진짜 0 과 구분되지 않는다. `ScoopCycle.valid` 는 계량 3건으로만 판정하므로 이 때문에 false 가 되지는 않는다.
- **선택지**: (a) `WeighHeld`/`WeighContainer` 결과에 `float32[6] wrench`·`wrench_std`·`uint16 wrench_samples` 추가 — 스킬이 이미 표본을 들고 있으니 비용이 가장 싸다. (b) `ScoopCycle` 에서 wrench 를 빼고 `weights` 테이블로 옮긴다. (c) G1 이후 실제로 안 쓰면 필드를 지운다.
- **곁가지**: `DispenseResult.verdict` 에는 `OK/UNDER/OVER` 뿐이라 QA 가 승인한 `INVALID` 판정을 담을 값이 없다 — 지금은 `OK` 로 떨어진다. (a) 를 할 때 같이 본다.
- **남은 것**: G1(I-001) 로 wrench 가 실제로 쓸모 있는지 보고 (a)/(c) 를 고른다. **그 전에는 계약을 또 흔들지 않는다.**

### I-009
계약 상수가 응답 절에 있었다 · 중 · 조장 + C·D · 등록 9/18
- **내용**: `.action`/`.srv` 는 `---` 로 절이 갈리고 **상수는 적힌 절에만** 생성된다. `MoveToStation.action` 의 `ABOVE`/`AT` 와 `InterlockRequest.srv` 의 `ENTER`/`EXIT` 가 마지막 `---` **뒤**에 있어서 각각 `Feedback`·`Response` 에만 붙었다. 정작 그 상수가 설명하는 필드(`Goal.approach`, `Request.request`)에서는 못 쓴다.
- **드러난 자리**: `hmi_web_node.py` 의 `InterlockRequest.ENTER` 는 **호출될 때마다 `AttributeError`** 였다 — HMI 인터락 버튼이 눌린 적이 없어 안 걸렸다. `skill_node` 는 상수를 못 쓰니 `approach == 0` 이라고 숫자로 비교하고 있었다.
- ✅ **해소 (9/18)**: 두 파일의 상수를 **요청 절로 옮겼다**. 전송 형식은 바뀌지 않는다(상수는 선 위로 안 나간다). 참조는 `MoveToStation.Goal.ABOVE`·`InterlockRequest.Request.ENTER` 로 쓴다. `hmi_web_node.py` 의 깨진 참조도 같이 고쳤다.
- **남은 것**: `skill_node` 의 `approach == 0` 숫자 비교를 `MoveToStation.Goal.ABOVE` 로 바꾸는 것 (A, 급하지 않음). 새 계약 파일을 쓸 때 **상수는 그 상수가 설명하는 절에** 적는다.
