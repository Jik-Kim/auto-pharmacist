# Interfaces — 계약 v1.1 (2026-09-16)

> **v1.1 (9/16 밤): `Deviation.operator_id` 추가**(판정자 — 감사 추적), **`/cell/record_summary` 폐지**(HMI 가 DB 를 직접 읽는다), **7절 DB 스키마 신설.** HMI 는 PyQt5 → **웹(Flask)** 으로 (SOT D-16·D-17). 나머지 이름·타입은 v1.0 그대로.

정의 원본은 `ros2_ws/src/gmp_interfaces`. 이 문서는 의도·규칙·확정 값을 설명한다.

> **이 문서는 9/17 병렬 구현의 전제다.** 여기 적힌 이름·타입·값을 기준으로 각 파트가 독립적으로 만들고, 통합 때 그대로 붙는다.
> **변경 절차:** 계약을 바꿔야 하면 **먼저 팀 채널에 알리고**, `gmp_interfaces` 와 이 문서를 **같은 커밋에서** 고친다. 리뷰는 영향받는 담당 전원, 최소 2명 승인 (PM 없음 — AGENTS 교차검수).

---

## 1. 메시지·서비스·액션 (gmp_interfaces)

| 타입 | 용도 | 비고 |
|---|---|---|
| `msg/RecipeItem` | 원료 1종의 목표량·허용 오차·등급·전용 스쿱 | 등급 `ACTIVE`(주성분 ±1 %) / `EXCIPIENT`(부형제 ±5 %) — 값은 레시피 yaml |
| `msg/Recipe` | 배치 1건 = 원료 목록. **배열 순서가 투입 순서** | 순서 위반은 일탈이 아니라 **버그**다 — 상태기계가 순서를 바꾸지 않는다 |
| `msg/CellState` | 공정 상태 (모드·배치·스텝·현재 스테이션) | `process_node` 단독 발행, 2 Hz + 변화 시 |
| `msg/WeightReading` | 1회 계량 결과 (총량·풍량·순량·표준편차·표본 수·유효) | `valid=false` 면 값을 쓰지 않는다 — 정착 실패·힘 조회 실패 |
| `msg/DispenseResult` | 원료 1종 분주 결과 (목표·실측·오차·판정·시도 횟수) | 판정 `OK/UNDER/OVER`. **`OVER` 는 되돌릴 수 없으므로 일탈** |
| `msg/Deviation` | 일탈 1건 (종류·상세·판정 필요 여부·판정·**판정자**) | 자동 복구된 것도 기록한다 — 지속성 평가의 근거. `operator_id` 는 QA 판정 후 process 가 채운다 (v1.1) |
| `msg/CellEvent` | 로그 이벤트 (레벨·코드·문장) | 배치 기록의 원천. 모든 노드가 발행 가능 |
| `msg/GripperState` | 폭·busy·파지 추론·안전 스위치·명령 파지력 | `skill_node` 10 Hz. 파지는 **추론**이다 (SOT D-05) |
| `srv/SubmitOrder` | HMI → process. 레시피 접수 | 실행 중이면 거부 (`accepted=false`, 사유) |
| `srv/QaDecision` | HMI → process. 일탈 판정 `APPROVE/DISCARD` | 대기 중인 일탈이 없으면 거부 |
| `srv/InterlockRequest` | HMI → process → skill. `ENTER`(사람 투입) / `EXIT`(재개) | `ENTER` 는 로봇이 안전 자세에 **도달한 뒤** `granted=true` |
| `srv/Grip` | process → skill. 폭·힘·열기/닫기 | 응답에 정지 폭과 파지 추론 |
| `srv/MeasureForce` | process → skill. 정지 상태 외력 평균 | 로봇이 움직이는 중이면 `valid=false` |
| `srv/SafePose` | process → skill. 안전 자세로 후퇴 | 인터락·에러 공통 |
| `action/MoveToStation` | 스테이션 이동 (`ABOVE` 접근점 / `AT` 작업점) | 좌표는 `stations.yaml` 단일 출처 |
| `action/Scoop` | 원료통에서 퍼올리기 | 원료면 접촉 감지 포함 |
| `action/Pour` | 칭량 용기에 붓기 (`fraction<1` 이면 털어내기) | 기울임 각·속도는 파라미터 |
| `action/WeighContainer` | 용기를 들어 계량하고 내려놓기 (복합 스킬) | 결과는 `WeightReading`. **그리퍼가 비어 있어야 한다** — TARE 와 배치 끝 VERIFY 에서만 (D-22) |
| **`action/WeighHeld`** (v1.2 예정, 미합의) | **들고 있는 것(스쿱)을 그대로** 계량 자세로 가져가 재기 — 파지·내려놓기 없음 | D-22 의 `weigh_scoop`. `WeighContainer` 에 `mode` 필드로 넣는 안도 가능 — A 와 합의 (I-007). `Deviation.kind` 에 `VERIFY_MISMATCH` 도 v1.2 |
| `action/RunBatch` | HMI/CLI → process. 배치 실행 | 피드백 `CellState` + 마지막 `DispenseResult` |

## 2. 확정된 값 — 더 논의하지 않는다

| 항목 | 확정 | 근거 |
|---|---|---|
| **단위** | 무게 **g**, 힘 **N**, 길이 **mm**, 각도 **deg**, 시간 **s**. 메시지 필드명에 단위 접미사 (`_g`, `_mm`, `_n`, `_s`) | 두산 API 가 mm·deg 라 맞춘다. 단위 없는 숫자는 통합일에 10배 오차로 드러난다 |
| **힘 → 그램** | `g = −Fz_base / 9.80665 × 1000`, 영점은 **빈 그리퍼로 같은 자세에서** 잰 값 | 중력 성분은 자세에 따라 달라 **계량 자세를 하나로 고정**한다 (`stations.yaml` `scale`) |
| **판정** | `error_pct = (actual − target) / target × 100`. `|error| ≤ tol` → `OK`, `actual < target` → `UNDER`(보정 투입), `actual > target(1+tol)` → **`OVER` = 일탈** | 초과는 되돌릴 수 없다 — 회수 동작을 만들지 않는다 |
| **재시도 상한** | 보정 투입 `max_attempts` 3 (파라미터). 넘으면 `Deviation(kind=TIMEOUT)` 로 QA 판정 | 무한 루프가 무인 운전을 죽인다 |
| **파지 추론** | 정지 폭 > `목표 폭 + grip_margin_mm(2.0)` → 잡음. 폭 변화가 `slip_mm(1.5)` 넘으면 미끄러짐 | RG2 백래시 0.3 + 반복 0.2 mm 의 3배 |
| **시각** | 모든 기록은 ROS 시각 | 배치 기록·CSV 를 나중에 합친다 |
| **시간 상수** | 초 단위, 파라미터 | 주기를 바꿔도 의미가 안 변한다 |
| **스테이션 ID** | 문자열. `magazine`, `scale`, `material_1`~`material_4`, `scoop_rack`, `output_tray`, `passbox`, `reject_bin`, `safe` | 열거형 메시지 상수를 쓰지 않는다 — 티칭 중 스테이션이 늘어도 재빌드 없이 yaml 만 고친다 |

## 3. 노드·토픽 계약

우리 노드는 launch 가 `namespace:=cell` 을 붙인다. 코드는 **상대 이름**, 문서는 절대 이름.

| 토픽 | 송신 | 수신 | 타입 | QoS | 비고 |
|---|---|---|---|---|---|
| `/cell/state` | process_node | hmi, record | CellState | RELIABLE, **TRANSIENT_LOCAL**, depth 1 | 늦게 뜬 HMI 도 마지막 상태를 받는다 |
| `/cell/weight` | process_node | hmi, record | WeightReading | RELIABLE, depth 20 | 계량할 때마다. 그래프 원천 |
| `/cell/dispense_result` | process_node | hmi, record | DispenseResult | RELIABLE, depth 50 | 원료 1종 끝날 때마다 |
| `/cell/deviation` | process_node | hmi, record | Deviation | RELIABLE, TRANSIENT_LOCAL, depth 10 | 판정 대기 중인 일탈을 HMI 가 재접속해도 본다 |
| `/cell/event` | 모든 노드 | record | CellEvent | RELIABLE, depth 100 | 배치 기록 원천 |
| `/cell/gripper_state` | skill_node | hmi, process | GripperState | BEST_EFFORT, depth 1 | 10 Hz |
| ~~`/cell/record_summary`~~ | — | — | — | — | **v1.1 폐지.** HMI 가 DB 를 읽는다 (7절) |

**서비스·액션 이름** (전부 `/cell/` 아래): `submit_order`, `qa_decision`, `interlock`, `grip`, `measure_force`, `safe_pose`, `move_to_station`, `scoop`, `pour`, `weigh_container`, `run_batch`.

**외부 계약 (우리가 정하지 않는다)**

| 이름 | 소유 | 우리 쪽 사용 |
|---|---|---|
| `/dsr01/*` 서비스 | doosan-robot2 | `DSR_ROBOT2` 함수로만 (movej/movel/get_tool_force/task_compliance_ctrl/…). 직접 호출 금지 |
| `/onrobot/sendCommand` (`SetCommand`) | onrobot-ros2 · 가상 노드 | 실물: 폭 1/10 mm 정수 문자열, `'c'`/`'o'`, `'i'`/`'d'`(힘 ±2.5 N). 가상: `'c'`/`'o'`/rad 실수 문자열 — **의미가 다르다**, 어댑터가 가른다 |
| `/onrobot_joint_states` (`JointState`) | onrobot-ros2 (real) | `finger_joint` rad → 폭 mm 환산 (RG2 기구 상수, 어댑터에 있음). 50 Hz |
| `/dsr01/gripper_joint_states` | gripper_virtual_node (virtual) | 같은 환산 |

## 4. 파라미터 계약

값은 `gmp_bringup/params/` 가 **단일 출처**다. 노드는 상수로 하드코딩하지 않는다.

| 파일 | 담는 것 | 담당 |
|---|---|---|
| `common.yaml` | `robot.*`(id·모델·툴·TCP·속도), `gripper.*`(백엔드·폭·힘·마진), `scale.*`(표본·정착·환산·영점), `dosing.*`(시도 상한·털어내기 비율), `safety.*`(힘 상한·충돌 감도), `interlock.*`, 타임아웃 | 조장 (값은 담당이 제안) |
| `stations.yaml` | 스테이션 ID → `posx`(mm·deg) 접근점/작업점, 계량 자세. **데이터 yaml** — 런치가 경로만 넘기고 `skill_node` 가 직접 읽는다 | A (티칭) |
| `recipes/*.yaml` | 배치 레시피. **스키마·검증은 `gmp_process/core/recipe.py` 가 단일 출처** — D 의 HMI 는 `recipe.load()` 로 읽어 `SubmitOrder` 로 보낸다 (인라인 파싱 금지). 값은 G1 결과로 조장이 확정 (D-08) | **C** (스키마·검증) |

## 5. QoS

- 명령·상태·결과·서비스: **RELIABLE**. 상태·일탈·요약은 **TRANSIENT_LOCAL** (늦게 뜬 구독자가 마지막 값을 받는다)
- 고주기 관측(`gripper_state`): **BEST_EFFORT**, depth 1

## 6. 지표 산출 계약

**지표 하나는 셋이 다 정해져야 숫자가 된다** — ① 무엇을 1건으로 세나 ② 언제부터 언제까지 재나 ③ 어느 기록에서 읽나.

| 지표 | 1건의 단위 | 시작 → 끝 | 읽는 곳 |
|---|---|---|---|
| 칭량 정확도 | 원료 1종 분주 | 판정 시점 `error_pct` | `dispense_result` (CSV) |
| 배치 성공률 | 배치 1건 | `RunBatch` 수락 → 결과. 일탈 `DISCARD` 는 실패 | `record` JSON `result` |
| **MTBI** (강제 개입 사이 시간) | 강제 개입 1건 = 로봇이 못 해서 사람이 셀에 들어간 것 | 무인 운전 구간 합 ÷ 개입 수 | `event` 코드 `INTERVENTION_FORCED` |
| 자동 복구율 | 일탈 1건 | `decision == AUTO_RECOVERED` / 전체 일탈 | `deviation` |
| 사이클타임 | 배치 1건 | 수락 → 완료 | `record` JSON |

## 7. DB 스키마 (배치 기록)

`gmp_hmi/config/schema.sql` 이 기준. **기록 주체는 `record_node` 하나**, HMI 는 읽기만. 파일 `~/auto-pharmacist/records/cell.db` (파라미터 `db_path`).

| 테이블 | 담는 것 | 원천 토픽 | 비고 |
|---|---|---|---|
| `batches` | 배치 1건 — 시작·종료·결과·사이클타임 | `state` 의 mode 전이 | RUNNING 진입 = 시작, DONE/ERROR 진입 = 종료 |
| `items` | 원료별 분주 결과 | `dispense_result` | 목표·실측·오차·판정·시도 |
| `weights` | 계량 1회 | `weight` | 그래프·분해능 근거. `valid=0` 도 남긴다 |
| `deviations` | 일탈 — 종류·판정·**판정자·판정 시각** | `deviation` | 같은 ID 재수신 시 판정만 갱신 |
| `events` | 전 이벤트, **append-only** | `event` | MTBI(`INTERVENTION_FORCED`)·자동복구율 원천. 수정·삭제 메서드 없음 |
| `audit` | **사람의 조작만** — 누가·언제·무엇 | `event` 중 `code` 가 `HMI_*` | `text` 첫 단어가 actor. 주문·QA 승인/폐기·인터락 |

**규칙**
- 배치 종료 시 `records/<batch_id>.json` 으로 내보낸다 — **DB 가 원본, JSON 은 사본**(제출·인쇄용).
- 사람 접촉(D-21): `skill_node` 가 `CellEvent(code='NUDGE', text='<|F| N>')` 발행 → process `RUNNING→PAUSED(NUDGE)`, 다음 `NUDGE` 로 재개 (`PAUSED→이전 상태`). 일탈이 아니라 이벤트다 — MTBI 분모에 들지 않는다.
- HMI 조작은 `CellEvent(code='HMI_ORDER'|'HMI_QA_APPROVE'|'HMI_QA_DISCARD'|'HMI_INTERLOCK_ENTER'|'HMI_INTERLOCK_EXIT', text='<actor> <detail>')` 로 발행한다. actor 가 비면 `unknown` — 시연에서는 반드시 ID 를 넣는다.
- 지표(6절)는 `tools/report.py` 가 이 DB 에서만 읽는다. CSV 를 따로 두지 않는다 — 두 기록이 갈라지면 둘 다 못 믿는다.

**QA 원격 승인은 개입이 아니다** — 규정이 요구하는 절차다. **인터락 `ENTER` 는 원료 보충이면 개입이 아니고, 에러 수습이면 강제 개입이다** (`InterlockRequest.reason` 으로 가른다).

값을 만드는 쪽(process)·기록하는 쪽(record)·집계하는 쪽(`tools/report.py`, 휴강 중 작성)을 나눈다.
