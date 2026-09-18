# Interfaces — 계약 v1.2 초안 (2026-09-18)

> **v1.2 (9/18 확정):** `WeighHeld` Action 신설, `Deviation.kind` 에 `VERIFY_MISMATCH`·`BATCH_OUT_OF_SPEC`·`WRONG_TOOL` 추가 (I-007 해소). 그 외 — 불필요한 `RecipeItem.grade/scoop_id`, `Pour.target_station`, `WeighContainer.container_station`, `QaDecision.batch_id`를 제거하고, `Grip` → `SetGripper`, `Scoop` 실행 관측 필드와 `ScoopCycle` 학습 기록을 추가한다.
> 현재 조장 1차 승인 상태다. **팀 채널 공유와 영향 담당 최소 2명 승인 전에는 확정 계약이 아니다.**

정의 원본은 `ros2_ws/src/gmp_interfaces`. 이 문서는 의도·규칙·확정 값을 설명한다.

> v1.1은 현재 구현 기준이고, 이 문서의 v1.2 변경분은 승인 대기 중인 통합 초안이다.
> **변경 절차:** 계약을 바꿔야 하면 **먼저 팀 채널에 알리고**, `gmp_interfaces` 와 이 문서를 **같은 커밋에서** 고친다. 리뷰는 영향받는 담당 전원, 최소 2명 승인 (PM 없음 — AGENTS 교차검수).

---

## 1. 메시지·서비스·액션 (gmp_interfaces)

| 타입 | 용도 | 비고 |
|---|---|---|
| `msg/RecipeItem` | 원료 1종의 ID·목표량·허용 오차 | `grade`, `scoop_id`는 제거. 허용 오차는 레시피가 직접 주고, 전용 스쿱은 셀 설정의 `material_id` 매핑으로 정한다 |
| `msg/Recipe` | 배치 1건 = 원료 목록. **배열 순서가 투입 순서** | 순서 위반은 일탈이 아니라 **버그**다 — 상태기계가 순서를 바꾸지 않는다 |
| `msg/CellState` | 공정 상태 (모드·배치·스텝·현재 스테이션) | `process_node` 단독 발행, 2 Hz + 변화 시 |
| `msg/WeightReading` | 1회 계량 결과 (총량·풍량·순량·표준편차·표본 수·유효·**대상**) | `valid=false` 면 값을 쓰지 않는다 — 정착 실패·힘 조회 실패. **v1.2 에서 `subject`(`scoop`/`container`) 추가** — 스쿱도 용기도 `workbench` 에서 재므로 `station` 으로는 구분되지 않는다 |
| `msg/ScoopCycle` | 스쿠핑 1회 시도의 동작·계량·붓기 결과를 묶은 학습 원본 | `process_node`가 성공·실패로 시도가 종료될 때 1건 발행. `Scoop.Feedback`을 학습 기록으로 쓰지 않는다 |
| `msg/DispenseResult` | 원료 1종 분주 결과 (목표·실측·오차·판정·시도 횟수) | 판정 `OK/UNDER/OVER`. **`OVER` 는 되돌릴 수 없으므로 일탈** |
| `msg/Deviation` | 일탈 1건 (종류·상세·판정 필요 여부·판정·**판정자**) | 자동 복구된 것도 기록한다 — 지속성 평가의 근거. `operator_id` 는 QA 판정 후 process 가 채운다 (v1.1). **v1.2 에서 `VERIFY_MISMATCH`(계측 신뢰성)·`BATCH_OUT_OF_SPEC`(제품 규격)·`WRONG_TOOL`(폭 지문) 추가** |
| `msg/CellEvent` | 로그 이벤트 (레벨·코드·문장) | 배치 기록의 원천. 모든 노드가 발행 가능 |
| `msg/GripperState` | 폭·busy·파지 추론·안전 스위치·명령 파지력 | `skill_node` 10 Hz. 파지는 **추론**이다 (SOT D-05) |
| `srv/SubmitOrder` | HMI → process. 레시피 접수 | 실행 중이면 거부 (`accepted=false`, 사유) |
| `srv/QaDecision` | HMI → process. `deviation_id`의 일탈을 `APPROVE/DISCARD` | `batch_id`는 제거. 대기 중인 일탈이 없거나 ID가 다르면 거부 |
| `srv/InterlockRequest` | HMI → process → skill. `ENTER`(사람 투입) / `EXIT`(재개) | `ENTER` 는 로봇이 안전 자세에 **도달한 뒤** `granted=true` |
| `srv/SetGripper` | process → skill. 열기/닫기와 폭·힘 설정 | `/cell/set_gripper`. 응답에 정지 폭과 파지 추론 |
| `srv/MeasureForce` | process → skill. 정지 상태 외력 평균 | 로봇이 움직이는 중이면 `valid=false` |
| `srv/SafePose` | process → skill. 안전 자세로 후퇴 | 인터락·에러 공통 |
| `action/MoveToStation` | 스테이션 이동 (`ABOVE` 접근점 / `AT` 작업점) | 좌표는 `stations.yaml` 단일 출처 |
| `action/Scoop` | 원료통에서 퍼올리기 | Feedback은 단계·접촉력·삽입 깊이, Result는 최종 접촉 여부·최대 힘·깊이 |
| `action/Pour` | 고정 칭량 위치의 용기에 붓기 (`fraction<1` 이면 털어내기) | 목적지는 skill 설정의 `workbench`; `target_station`은 제거 |
| `action/WeighContainer` | 고정 `workbench`의 용기를 들어 계량하고 내려놓기 (복합 스킬) | `container_station`은 제거. 결과는 `WeightReading`. **그리퍼가 비어 있어야 한다** — TARE 와 배치 끝 VERIFY 에서만 (D-22) |
| **`action/WeighHeld`** (v1.2) | **들고 있는 것(스쿱)을 그대로** 계량 자세로 가져가 재기 — 파지·내려놓기 없음 | D-22 의 `SCOOP_TARE`·`WEIGH_SCOOP`·`WEIGH_RESIDUAL` 세 단계가 **이 요청 하나**를 쓴다 (차이는 process 가 결과를 어디에 담느냐뿐). **계량 후 계량 자세에 머문다**(복귀 없음) · **빈 그리퍼면 `success=false`**. phase 는 `LIFT`/`SETTLE`/`MEASURE` — `WeighContainer` 와 달리 `GRIP`·`PLACE` 가 없어 `mode` 필드로 합치지 않았다 (9/18 확정, I-007) |
| `action/RunBatch` | HMI/CLI → process. 배치 실행 | 피드백 `CellState` + 마지막 `DispenseResult` |

### 1.1 `Scoop`과 `ScoopCycle`의 책임 경계

- `Scoop.Feedback`은 화면 표시와 실행 감시용 실시간 값이다. 전송 중 일부가 유실될 수 있으므로 학습 원본으로 사용하지 않는다.
- `Scoop.Result`는 퍼올리기 동작이 끝난 시점의 기계적 결과다. 아직 붓기와 잔량 계량 전이므로 실제 투입량을 담지 않는다.
- `ScoopCycle`은 `process_node`가 `Scoop.Result`, 빈 스쿱·붓기 전·붓기 후 계량, `Pour` 명령을 합쳐 만드는 **시도 1회의 완결 기록**이다. 실패한 시도는 확정 즉시 발행하고 수집하지 못한 계량을 `valid=false`로 둔다.
- 원료 1종의 모든 재시도가 끝난 최종 판정은 기존 `DispenseResult`가 담당한다.
- `scoop_id`는 따로 보내지 않는다. 전용 스쿱은 **원료통 아래에 원료별로** 두고(9/18 확정, `scoop_rack` 폐지) `stations.yaml` 의 `scoop_1`~`scoop_4` 가 `material_id` 로 짝을 이룬다.

| `ScoopCycle` 필드 | 의미 |
|---|---|
| `header` | 시도가 성공·실패로 종료된 시각 |
| `batch_id`, `material_id`, `attempt` | 배치·원료·1부터 시작하는 시도 번호 |
| `target_g`, `actual_before_g` | 원료 전체 목표량과 이번 시도 전 누적 투입량 [g] |
| `scoop_tare`, `pre_pour`, `post_pour` | 빈 스쿱, 붓기 전, 붓기 후의 `WeightReading`. 세 측정은 같은 계량 자세·파지 조건을 쓰며 pre/post의 `tare_g`는 빈 스쿱 기준값으로 동일해야 한다. 각 헤더·표준편차·표본 수·유효성을 보존한다 |
| `commanded_pour_fraction` | `Pour`에 전달한 명령 비율 [0~1] |
| `delivered_g` | FSM과 같이 `max(0, pre_pour.net_g - post_pour.net_g)`로 계산한 투입량 [g]. 음수 원시차는 두 reading으로 복원한다 |
| `weigh_method` | `UNKNOWN`, `WORKPIECE`, `TOOL_FORCE` 중 스쿱 계량에 사용한 방식 |
| `weigh_pose_id`, `tool_name`, `tcp_name` | `stations.yaml`의 전체 위치·자세 ID와 컨트롤러 툴·TCP 등록명. 보정 조건이 다른 샘플을 구분한다 |
| `contact_detected`, `max_contact_force_n`, `insertion_depth_mm` | `Scoop.Result`에서 받은 접촉 및 삽입 결과 |
| `grip_width_mm` | `SetGripper` 성공 후 스쿱 손잡이의 정지 폭 [mm] |
| `*_wrench`, `*_wrench_std`, `*_wrench_samples` | 대응하는 `WeightReading.header`의 표본 구간과 같은 고정 계량 자세, `DR_BASE` 기준 `[Fx,Fy,Fz,Mx,My,Mz]`; 힘 [N], 모멘트 [N·m]의 평균·표준편차·표본 수. CoG 변화·JTS 편향 재분석용 통계 특징 |
| `*_wrench_valid` | 해당 6축 통계를 실제로 취득했는지. 미구현·조회 실패 시 false |
| `reference_delivered_g`, `reference_std_g`, `reference_source`, `reference_valid` | JTS와 다른 기준의 값·불확실성·출처. 취득 프로토콜이 확정된 교정 실험에서만 `reference_valid=true` |
| `outcome`, `valid`, `duration_s` | 완료·빈 스쿱·계량 무효·붓기 실패·중단 결과, 학습 사용 가능 여부, 소요 시간 [s] |

JTS에서 계산한 `delivered_g`만 정답으로 다시 학습하면 같은 계측 편향을 재학습한다. 정상 운전에서는 `reference_valid=false`로 두고, 독립 기준 취득 프로토콜이 있는 교정 샘플만 정확도 평가·감독학습에 쓴다. 같은 JTS로 재는 배치 끝 `VERIFY`는 독립 정답이 아니다.

### 1.2 인터페이스별 방향과 필드

메시지는 토픽으로 독립 전송되거나 서비스·액션 안에 포함된다.

| 메시지 | 방향 | 필드 의미 |
|---|---|---|
| `RecipeItem` | HMI → process (`Recipe.items`) | `material_id`: 원료 ID, `target_g`: 목표 순량, `tol_pct`: 허용 오차율. 전용 스쿱은 메시지가 아니라 `stations.yaml` 의 `scoop_N`(`material_id` 일치)으로 찾는다 |
| `Recipe` | HMI → process (`SubmitOrder`/`RunBatch`) | `header`: 생성 시각, `batch_id`: 빈 값이면 process가 발급, `product`: 표시명, `items`: 투입 순서 그대로의 원료 배열 |
| `CellState` | process → HMI·record | `mode`: 셀 운전 모드, `batch_id`: 현재 배치, `step`: FSM 상태, `item_index`: 0 기반 원료 순번, `station`: 마지막 도착 위치, `note`: 화면용 보충 설명 |
| `WeightReading` | skill → process (`WeighContainer.Result`), process → HMI·record (`weight`) | `gross_g`: 기준 차감 전 값, `tare_g`: 동일 자세·파지의 빈 용기/스쿱 기준, `net_g`: 차감값, `std_g`·`samples`: 분산과 표본 수, `valid`: 사용 가능 여부, `station`: 계량 자세 ID |
| `ScoopCycle` | process → record | 스쿠핑 시도 한 건의 완결 기록. 세부 필드는 1.1 표를 따른다 |
| `DispenseResult` | process → HMI·record | `batch_id`·`material_id`, `target_g`·`actual_g`, `error_pct`, `verdict`(`OK/UNDER/OVER`), `attempts`, `duration_s` |
| `Deviation` | process → HMI·record | `deviation_id`: 판정 대상 ID, 배치·원료 ID, `kind`: 일탈 종류, `detail`: 설명, `requires_decision`: QA 필요 여부, `decision`: 판정, `operator_id`: 판정자 |
| `CellEvent` | 모든 노드 → record; `NUDGE`는 process도 수신 | `level`: INFO/WARN/ERROR, `code`: 기계 판독용 이벤트 코드, `text`: 사람용 상세, `batch_id`: 관련 배치 |
| `GripperState` | skill → process·HMI | `width_mm`: 현재 폭, `busy`: 동작 중, `grip_inferred`: 폭 기반 파지 추론, `safety_triggered`: 안전 입력, `force_cmd_n`: 명령 파지력, `backend`: modbus/dio/virtual |

서비스는 요청 후 즉시 단일 응답을 돌려준다.

| 서비스 | 방향 | 요청 → 응답 필드 의미 |
|---|---|---|
| `SubmitOrder` | HMI → process | `recipe` → `accepted`, process가 정한 `batch_id`, `message`. 실행 완료가 아니라 **접수 결과**다 |
| `QaDecision` | HMI → process | `deviation_id`, `decision`, `operator_id` → `accepted`, `message`. 배치는 해당 `Deviation`에서 확인한다 |
| `InterlockRequest` | HMI → process | `request`(`ENTER/EXIT`), `reason` → `granted`, `message`. ENTER는 안전 자세 도달 뒤 승인한다 |
| `SetGripper` | process → skill | `close`, `width_mm`, `force_n`, `timeout_s` → `success`, 실제 정지 폭 `final_width_mm`, 폭 기반 `grip_inferred`, `message` |
| `MeasureForce` | process → skill | `samples`, `settle_s` → `force[6]`, `fz_mean_n`, `fz_std_n`, `valid`, `message`. `force`는 `get_tool_force(DR_BASE)`의 tool 외력 wrench `[Fx,Fy,Fz,Mx,My,Mz]`; 앞 3개는 N, 뒤 3개는 N·m이며 관절 토크가 아니다. 작용점은 컨트롤러의 설정 tool/TCP 기준으로 사용하고 실물 G1에서 확인한다 |
| `SafePose` | process → skill | `reason` → `success`, `message`. 인터락·오류 시 공통 안전 자세로 후퇴한다 |

액션은 긴 동작 중 Feedback을 여러 번 보내고 종료 시 Result를 한 번 보낸다.

| 액션 | 방향 | Goal → Result / Feedback 필드 의미 |
|---|---|---|
| `MoveToStation` | process → skill | Goal `station_id`, `approach`(`ABOVE/AT`), `vel_scale`; Result `success`, `message`, 실제 `reached`; Feedback `phase` |
| `Scoop` | process → skill | Goal `material_id`, `attempt`; Result `success`, 최종 `contact_detected`, `max_contact_force_n`, `insertion_depth_mm`, `message`; Feedback `phase`, 현재 접촉 여부·힘·삽입 깊이 |
| `Pour` | process → skill | Goal `fraction`; Result `success`, `message`; Feedback `phase`. 목적지는 skill 설정의 고정 `workbench`이다 |
| `WeighContainer` | process → skill | Goal `tare_g`; Result `reading`, `success`, `message`; Feedback `phase`. 고정 `workbench`의 용기를 들어 측정하고 내려놓는다 |
| `RunBatch` | HMI/CLI → process | Goal `recipe`; Result `success`, 완료 원료 수, 일탈 수, 종료 `result`, `message`; Feedback `state`, `last_result`. 접수만 하는 `SubmitOrder`와 달리 진행·최종 결과가 필요한 클라이언트용이다 |

## 2. 확정된 값 — 더 논의하지 않는다

| 항목 | 확정 | 근거 |
|---|---|---|
| **단위** | 무게 **g**, 힘 **N**, 길이 **mm**, 각도 **deg**, 시간 **s**. 메시지 필드명에 단위 접미사 (`_g`, `_mm`, `_n`, `_s`) | 두산 API 가 mm·deg 라 맞춘다. 단위 없는 숫자는 통합일에 10배 오차로 드러난다 |
| **힘 → 그램** | `g = −(Fz_base − Fz_zero) / 9.80665 × 1000`. `Fz_zero`는 같은 자세의 빈 그리퍼 기준이고, `WeightReading.tare_g`는 같은 파지 조건의 빈 용기·빈 스쿱 기준값이다 | 중력·CoG 영향은 자세에 따라 달라 **계량 자세를 하나로 고정**한다 (`stations.yaml` `workbench`) |
| **판정** | `error_pct = (actual − target) / target × 100`. `|error| ≤ tol` → `OK`, `actual < target` → `UNDER`(보정 투입), `actual > target(1+tol)` → **`OVER` = 일탈** | 초과는 되돌릴 수 없다 — 회수 동작을 만들지 않는다 |
| **재시도 상한** | 보정 투입 `max_attempts` 3 (파라미터). 넘으면 `Deviation(kind=TIMEOUT)` 로 QA 판정 | 무한 루프가 무인 운전을 죽인다 |
| **파지 추론** | 정지 폭 > `목표 폭 + grip_margin_mm(2.0)` → 잡음. 폭 변화가 `slip_mm(1.5)` 넘으면 미끄러짐 | RG2 백래시 0.3 + 반복 0.2 mm 의 3배 |
| **시각** | 모든 기록은 ROS 시각 | 배치 기록·CSV 를 나중에 합친다 |
| **시간 상수** | 초 단위, 파라미터 | 주기를 바꿔도 의미가 안 변한다 |
| **스테이션 ID** | 문자열. `workbench`, `material_1`~`material_4`, `scoop_1`~`scoop_4`, **`passbox_empty`·`passbox_done`**(Pass Box 두 칸, D-24), **`nudge_wait`**, `reject_bin`, `safe`. 스쿱은 원료통 아래 (9/18 확정, `scoop_rack` 폐지) — FSM 은 `material_id` 만 넘기고 **`process_node` 가 `stations.yaml` 에서 짝(`material_id` 일치)을 찾아** `MoveToStation(scoop_N)` 을 부른다 | 열거형 메시지 상수를 쓰지 않는다 — 티칭 중 스테이션이 늘어도 재빌드 없이 yaml 만 고친다 |

## 3. 노드·토픽 계약

우리 노드는 launch 가 `namespace:=cell` 을 붙인다. 코드는 **상대 이름**, 문서는 절대 이름.

| 토픽 | 송신 | 수신 | 타입 | QoS | 비고 |
|---|---|---|---|---|---|
| `/cell/state` | process_node | hmi, record | CellState | RELIABLE, **TRANSIENT_LOCAL**, depth 1 | 늦게 뜬 HMI 도 마지막 상태를 받는다 |
| `/cell/weight` | process_node | hmi, record | WeightReading | RELIABLE, depth 20 | 계량할 때마다. 그래프 원천 |
| `/cell/scoop_cycle` | process_node | record | ScoopCycle | RELIABLE, depth 50 | 스쿠핑 시도가 성공·실패로 종료될 때마다. 공정·학습 원본 |
| `/cell/dispense_result` | process_node | hmi, record | DispenseResult | RELIABLE, depth 50 | 원료 1종 끝날 때마다 |
| `/cell/deviation` | process_node | hmi, record | Deviation | RELIABLE, TRANSIENT_LOCAL, depth 10 | 판정 대기 중인 일탈을 HMI 가 재접속해도 본다 |
| `/cell/event` | 모든 노드 | record | CellEvent | RELIABLE, depth 100 | 배치 기록 원천 |
| `/cell/gripper_state` | skill_node | hmi, process | GripperState | BEST_EFFORT, depth 1 | 10 Hz |
| ~~`/cell/record_summary`~~ | — | — | — | — | **v1.1 폐지.** HMI 가 DB 를 읽는다 (7절) |

**서비스·액션 이름** (전부 `/cell/` 아래): `submit_order`, `qa_decision`, `interlock`, `set_gripper`, `measure_force`, `safe_pose`, `move_to_station`, `scoop`, `pour`, `weigh_container`, `run_batch`.

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
| `stations.yaml` | 스테이션 ID → `posx`(mm·deg) 접근점/작업점, 계량 자세, 원료통·전용 스쿱의 `material_id` 짝. **데이터 yaml** — 런치가 경로만 넘기고 `skill_node` 가 직접 읽는다 | A (티칭) |
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
| `scoop_cycles` *(v1.2 구현 필요)* | 스쿠핑 시도별 특징·결과·독립 기준값 | `scoop_cycle` | `valid=0`과 실패 outcome도 원본으로 남기고 학습 단계에서 필터링한다 |
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

## 8. v1.2 적용 인계

- **A/조장:** `SetGripper` 서버와 계약 정의 완료. `Scoop` 본문에서 Feedback/Result 관측값을 실제 채우고, `GripperState`의 `busy/grip_inferred/safety_triggered`를 어댑터 실값에 연결하며 G1에서 `MeasureForce`의 tool/TCP 기준을 확인한다.
- **C:** ✅ **9/18 완료** — `process_node` 가 스킬 8종을 계약대로 부른다. `grade/scoop_id` 없음, 원료 → `scoop_N` 은 `core/station_map.py` 가 `stations.yaml` 에서 풀고, `Pour`·`WeighContainer` 는 station 인자 없이 부르며, `SetGripper` 사용, `QaDecision.deviation_id` 불일치는 거부하고 판정 후 **같은 ID 로 재발행**한다. `scoop_cycle` 은 정상이면 `WEIGH_RESIDUAL` 뒤, 실패면 확정 단계에서 나간다. `CellState.station/note` 도 채운다. 확인: `test/test_process_node.py`(가짜 skill_node 로 레시피 1건 완주). **남은 것** — 6축 wrench 는 채울 경로가 없어 `*_wrench_valid=false` (I-008).
- **D:** 주문 생성에서 `grade/scoop_id`, `QaDecision.Request`에서 `batch_id` 제거(웹 화면의 배치 표시는 유지), `scoop_cycle` 구독·DB 테이블·JSON 내보내기 추가.
- **승인:** 팀 채널 공유 후 영향 담당 최소 1명의 추가 승인이 있어야 v1.2를 확정한다.

**계약 파일 쓸 때** — `.action`/`.srv` 의 상수는 `---` 로 갈린 **그 상수가 설명하는 절**에 적는다. 뒤쪽 절에 적으면 `Feedback`·`Response` 에만 생성되어 정작 쓸 곳에서 `AttributeError` 가 난다 (I-009 에서 실제로 났다). 참조는 `MoveToStation.Goal.ABOVE` 처럼 **절 이름을 붙여** 쓴다.
