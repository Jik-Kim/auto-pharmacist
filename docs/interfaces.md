# Interfaces — 계약 v1.8 (2026-09-23)

> **v1.2 (9/18 확정):** `WeighHeld` Action 신설, `Deviation.kind` 에 `VERIFY_MISMATCH`·`BATCH_OUT_OF_SPEC`·`WRONG_TOOL` 추가 (I-007 해소). 그 외 — 불필요한 `RecipeItem.grade/scoop_id`, `Pour.target_station`, `WeighContainer.container_station`, `QaDecision.batch_id`를 제거하고, `Grip` → `SetGripper`, `Scoop` 실행 관측 필드와 `ScoopCycle` 학습 기록을 추가한다.
> **v1.2.1 (9/18 팀 채널 승인):** `Deviation.decision` 에 `FORCED=4` 추가 — 강제 개입으로 끝난 일탈이 `AUTO_RECOVERED` 로 집계되던 것을 가른다. 전송 형식 불변, 새 값만 추가.
> **9/20 사용자 확인: v1.2의 나머지 변경과 v1.3까지 팀 승인 완료.** 두 버전은 확정 계약이며, 아래 v1.4의 세부 검토 상태와 구분한다.

> **v1.3 (9/19 팀 공유, 9/20 팀 승인 완료 확인·확정):** `ReturnMaterial` Action과 `ScoopCycle.RETURNED=5/RETURN_FAILED=6`을 추가한다. `Pour.fraction`은 1.0만 지원하며, 초과 스쿱은 원료통에 반환 후 다시 퍼낸다. 스쿱 계량은 원료별 `material_N.posx`로 통일한다.

> **v1.4 (9/22 사용자 전달 영향 담당 승인 확인·확정):** HMI→C→A 안전 정지 복구 경로를 추가한다. 로봇 복구 성공은 배치 재개를 의미하지 않는다.

> **v1.5 (9/20 팀 합의·A 승인):** `Scoop` Goal 에 `float32 depth_fraction`(담그기 깊이 비율)을 추가한다. 유효 범위는 `dosing.min_fraction` 이상 `1.0` 이하이고 범위 밖이면 이동 전에 거부한다. `attempt` 는 재시도 번호(기록용)로만 쓴다. v1.3 에서 `Pour.fraction` 을 1.0 으로 고정하면서 한 스쿱보다 작은 양을 넣을 수단이 사라졌고, 그 결과 남은 목표량이 스쿱 한 번보다 작아지면 반환만 반복하다 `TIMEOUT` 으로 끝났다. **`depth_fraction` → 실제 Z 좌표 변환식·보정값은 실물 scoop 시험 뒤 확정한다 (`TODO([A])`)** — 이번 판은 계약과 전달 경로까지다. 필드 추가라 메시지 해시가 바뀌므로 `gmp_interfaces` 재빌드가 필요하다.

> **v1.5.1 (9/21 반환 동작 설명 정정·사용자 승인):** ReturnMaterial은 끝 관절 자세에서 종료하며 RETURN 피드백을 내지 않는다. 검증된 재스쿱 연결 전까지 후속 Scoop을 차단한다. 메시지 필드·전송 형식은 변경하지 않는다.

정의 원본은 `ros2_ws/src/gmp_interfaces`. 이 문서는 의도·규칙·확정 값을 설명한다.

> **v1.6 (9/22 사용자 전달 영향 담당 승인 확인·확정):** 안전복구 시작에 따른 정지와 실제 새 알람을 구분한다.
> 기존 `CellEvent.text` JSON에 상관관계를 추가한다. ROS 메시지 필드와 `RecoverSafety.srv`는 변경하지 않는다.
> A/C/D 구현에 맞춰 아래 8절의 상관관계·역할·차단 해제 조건을 확정한다.

> **상태 요약 (9/22):** v1.2·v1.2.1·v1.3·v1.4·v1.5·v1.5.1·v1.6 확정. v1.4·v1.6은 #45 검토 후 사용자가 영향 담당 승인을 확인했다. 실제 ROS 통신·실물 복구 검증은 이번 확정에 포함하지 않는다.
> **v1.7 (9/22 사용자 승인):** A 내부 읽기 전용 `GetCollisionSensitivity` 서비스를 추가한다. 벤더 원본을 보존하는 상속 플러그인이 기존 연결로 전역 충돌 감도를 조회하며, `skill_node`는 기대값 50%와 비교해 기동·복구 자가진단을 수행한다. 실물 검증은 별도다.
> **v1.8 (9/23 조장 결정, #108):** `DispenseResult.verdict` 에 **`INVALID=3`** 을 추가한다. 계량 무효를 QA 가 승인해 **투입량을 모르는 채** 끝난 원료를 위한 값이다 — `UNDER`(모자랐다)와 다르다. **상수 추가이며 필드 레이아웃은 바뀌지 않는다**(기존 구독자의 역직렬화에 영향 없음). 다만 **모르는 열거값을 받는 쪽이 어떻게 보이는지는 소비자 문제**라 D 쪽 **두 곳이 같은 시점에 머지돼야 한다**. (1) `gmp_hmi/core/db.py:15` `VERDICTS` 에 `3: 'INVALID'` — 없으면 `db.py:122` 가 배치 기록에 문자열 `'3'` 을 저장하고 `hmi_web_node:339` 가 `'?'` 를 내보낸다. **기록 문제라 이것이 먼저다.** (2) `static/hmi.js:8` `verdict()` 의 배지 분류·한국어 라벨 — (1) 이 없으면 `hmi.js` 는 `'INVALID'` 가 아니라 `'?'` 를 받으므로 **(2) 는 (1) 에 딸린다**. 재고(`core/session_inventory.py` `observe`)는 `OK`·`OVER` 만 차감하므로 **거동이 바뀌지 않는다** — 지금 `UNDER` 도 제외되고 있다. 「미측정분만큼 재고가 실제보다 많게 보인다」는 전부터 있던 문제이고 v1.8 이 만들지 않는다(별건으로 D·조장이 정할 사안).
> 배경: #213 결정 3 이 `WEIGH_RESIDUAL` 무효를 QA 로 보내면서 **「투입량을 모르는 원료」가 처음으로 도달 가능해졌고**, 그때까지 `verdict` 가 빈 채 `OK` 로 떨어지고 있었다(I-008). #225 가 임시로 `UNDER` 되매김을 넣어 막았고, v1.8 이 그것을 정확한 값으로 바꾼다. `INVALID` 는 `unmeasured > 0` 일 때만 나가며, 「안 들어갔다」(첫 사이클 TIMEOUT 등)는 그대로 `UNDER` 다.
> **변경 절차:** 계약을 바꿔야 하면 **먼저 팀 채널에 알리고**, `gmp_interfaces` 와 이 문서를 **같은 커밋에서** 고친다. 리뷰는 영향받는 담당 전원, 최소 2명 승인 (PM 없음 — AGENTS 교차검수).

---

## 1. 메시지·서비스·액션 (gmp_interfaces)

| 타입 | 용도 | 비고 |
|---|---|---|
| `msg/RecipeItem` | 원료 1종의 ID·목표량·허용 오차 | `grade`, `scoop_id`는 제거. 허용 오차는 레시피가 직접 주고, 전용 스쿱은 셀 설정의 `material_id` 매핑으로 정한다 |
| `msg/Recipe` | 배치 1건 = 원료 목록. **배열 순서가 투입 순서** | 순서 위반은 일탈이 아니라 **버그**다 — 상태기계가 순서를 바꾸지 않는다 |
| `msg/CellState` | 공정 상태 (모드·배치·스텝·현재 스테이션) | `process_node` 단독 발행, 2 Hz + 변화 시 |
| `msg/WeightReading` | 1회 계량 결과 (총량·풍량·순량·표준편차·표본 수·유효·**대상**) | `valid=false` 면 값을 쓰지 않는다 — 정착 실패·힘 조회 실패. **v1.2 에서 `subject`(`scoop`/`container`) 추가** — 스쿱은 `material_N`, 용기는 `workbench`에서 계량하며 `subject`도 함께 기록한다 |
| `msg/ScoopCycle` | 스쿠핑 1회 시도의 동작·계량·붓기 결과를 묶은 학습 원본 | `process_node`가 성공·실패로 시도가 종료될 때 1건 발행. `Scoop.Feedback`을 학습 기록으로 쓰지 않는다 |
| `msg/DispenseResult` | 원료 1종 분주 결과 (목표·실측·오차·판정·시도 횟수) | 판정 `OK/UNDER/OVER/INVALID`. **`OVER` 는 되돌릴 수 없으므로 일탈**, **`INVALID`(v1.8) 은 투입량을 모른다는 뜻이라 `actual_g`·`error_pct` 를 목표와 비교하면 안 된다** |
| `msg/Deviation` | 일탈 1건 (종류·상세·판정 필요 여부·판정·**판정자**) | 자동 복구된 것도 기록한다 — 지속성 평가의 근거. `operator_id` 는 QA 판정 후 process 가 채운다 (v1.1). **v1.2 에서 `VERIFY_MISMATCH`(계측 신뢰성)·`BATCH_OUT_OF_SPEC`(제품 규격)·`WRONG_TOOL`(폭 지문) 추가.** `decision` 은 `PENDING`(QA 대기) / `APPROVED` / `DISCARDED` / `AUTO_RECOVERED`(RETRY·REFILL) / **`FORCED`(강제 개입 종료, v1.2.1)** — FORCED 는 자동 복구도 QA 대상도 아니다 |
| `msg/CellEvent` | 로그 이벤트 (레벨·코드·문장) | 배치 기록의 원천. 모든 노드가 발행 가능 |
| `msg/GripperState` | 폭·busy·파지 추론·안전 스위치·명령 파지력 | `skill_node` 10 Hz. 파지는 **추론**이다 (SOT D-05) |
| `srv/SubmitOrder` | HMI → process. 레시피 접수 | 실행 중이면 거부 (`accepted=false`, 사유) |
| `srv/QaDecision` | HMI → process. `deviation_id`의 일탈을 `APPROVE/DISCARD` | `batch_id`는 제거. 대기 중인 일탈이 없거나 ID가 다르면 거부 |
| `srv/InterlockRequest` | HMI → process → skill. `ENTER`(사람 투입) / `EXIT`(재개) | `ENTER` 는 로봇이 안전 자세에 **도달한 뒤** `granted=true` |
| `srv/SetGripper` | process → skill. 열기/닫기와 폭·힘 설정 | `/cell/set_gripper`. 응답에 정지 폭과 파지 추론 |
| `srv/MeasureForce` | process → skill. 정지 상태 외력 평균 | 로봇이 움직이는 중이면 `valid=false` |
| `srv/SafePose` | process → skill. 안전 자세로 후퇴 | 인터락·에러 공통 |
| `srv/RecoverSafety` | HMI → process → skill. 안전 정지 복구 | A `/cell/recover_safety`. C 중계 서비스 `/cell/request_safety_recovery` 구현 완료(process_node, 9/20). D의 단일 복구 요청 버튼도 구현됐으며 실제 C/A·실물 연동 검증은 별도 |
| `action/MoveToStation` | 스테이션 이동 (`ABOVE` 접근점 / `AT` 작업점) | 좌표는 `stations.yaml` 단일 출처 |
| `action/Scoop` | 원료통에서 퍼올리기 | Goal `depth_fraction`(v1.5)이 담그기 깊이 비율. Feedback은 단계·접촉력·삽입 깊이, Result는 최종 접촉 여부·최대 힘·깊이. 수동 `height_measure_only` 모드는 높이만 `message`로 보고하고 `success=false`로 종료하므로 자동 공정과 병용하지 않는다 |
| `action/Pour` | workbench의 용기에 전량 붓기 (`fraction=1.0`만 허용) | 목적지는 skill 설정의 `workbench`; `target_station`은 제거 |
| `action/ReturnMaterial` | 전용 스쿱 원료를 동일 원료통에 반환 | `/cell/return_material`. 시작 posx·끝 posj 미티칭 또는 파지 원료 불일치 시 이동 전에 실패. 끝 자세 유지, 후속 Scoop은 연결 경로 구현 전까지 차단 |
| `action/WeighContainer` | 고정 `workbench`의 용기를 들어 계량하고 내려놓기 (복합 스킬) | `container_station`은 제거. 결과는 `WeightReading`. **그리퍼가 비어 있어야 한다** — TARE 와 배치 끝 VERIFY 에서만 (D-22) |
| **`action/WeighHeld`** (v1.2) | **들고 있는 전용 스쿱을 대응 `material_N.posx`로** 가져가 재기 — 파지·내려놓기 없음 | D-22 의 `SCOOP_TARE`·`WEIGH_SCOOP`·`WEIGH_RESIDUAL` 세 단계가 **이 요청 하나**를 쓴다 (차이는 process 가 결과를 어디에 담느냐뿐). **계량 후 계량 자세에 머문다**(복귀 없음) · **빈 그리퍼면 `success=false`**. phase 는 `LIFT`/`SETTLE`/`MEASURE` — `WeighContainer` 와 달리 `GRIP`·`PLACE` 가 없어 `mode` 필드로 합치지 않았다 (9/18 확정, I-007) |
| `action/RunBatch` | HMI/CLI → process. 배치 실행 | 피드백 `CellState` + 마지막 `DispenseResult` |

### 1.1 `Scoop`과 `ScoopCycle`의 책임 경계

- `Scoop.Feedback`은 화면 표시와 실행 감시용 실시간 값이다. 전송 중 일부가 유실될 수 있으므로 학습 원본으로 사용하지 않는다.
- `Scoop.Result`는 퍼올리기 동작이 끝난 시점의 기계적 결과다. 아직 붓기와 잔량 계량 전이므로 실제 투입량을 담지 않는다. 예외적으로 수동 `height_measure_only` 모드는 실제 스쿠핑 없이 높이를 `message`의 `HEIGHT_MEASUREMENT_ONLY` 진단 문자열로 보고하고 `success=false`로 종료한다. process는 이를 일반 실패로 해석하므로 자동 공정과 병용하지 않는다.
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
| `delivered_g` | `RETURNED`/`RETURN_FAILED`는 0. 정상은 FSM과 같이 `max(0, pre_pour.net_g - post_pour.net_g)`로 계산한 투입량 [g]. 음수 원시차는 두 reading으로 복원한다 |
| `weigh_method` | `UNKNOWN`, `WORKPIECE`, `TOOL_FORCE` 중 스쿱 계량에 사용한 방식 |
| `weigh_pose_id`, `tool_name`, `tcp_name` | `stations.yaml`의 전체 위치·자세 ID와 컨트롤러 툴·TCP 등록명. 보정 조건이 다른 샘플을 구분한다 |
| `contact_detected`, `max_contact_force_n`, `insertion_depth_mm` | `Scoop.Result`에서 받은 접촉 및 삽입 결과 |
| `grip_width_mm` | `SetGripper` 성공 후 스쿱 손잡이의 정지 폭 [mm] |
| `*_wrench`, `*_wrench_std`, `*_wrench_samples` | 대응하는 `WeightReading.header`의 표본 구간과 같은 고정 계량 자세, `DR_BASE` 기준 `[Fx,Fy,Fz,Mx,My,Mz]`; 힘 [N], 모멘트 [N·m]의 평균·표준편차·표본 수. CoG 변화·JTS 편향 재분석용 통계 특징 |
| `*_wrench_valid` | 해당 6축 통계를 실제로 취득했는지. 미구현·조회 실패 시 false |
| `reference_delivered_g`, `reference_std_g`, `reference_source`, `reference_valid` | JTS와 다른 기준의 값·불확실성·출처. 취득 프로토콜이 확정된 교정 실험에서만 `reference_valid=true` |
| `outcome`, `valid`, `duration_s` | 완료·빈 스쿱·계량 무효·붓기 실패·중단·반환 완료(5)·반환 실패(6) 결과, 학습 사용 가능 여부, 소요 시간 [s] |

JTS에서 계산한 `delivered_g`만 정답으로 다시 학습하면 같은 계측 편향을 재학습한다. 정상 운전에서는 `reference_valid=false`로 두고, 독립 기준 취득 프로토콜이 있는 교정 샘플만 정확도 평가·감독학습에 쓴다. 같은 JTS로 재는 배치 끝 `VERIFY`는 독립 정답이 아니다.

### 초과 스쿱 반환 규칙 (v1.3)

`순 스쿱량 > max(0, target_g - actual_g) + target_g × tol_pct / 100`이면 `Pour` 대신 `ReturnMaterial`을 요청한다. 경계값 이하는 전량 붓는다. 반환 성공 후 반환 한도 안에서 재스쿱하며, 실패 시 재스쿱으로 진행하지 않는다. 반환은 붓기 시도 횟수를 소모하지 않는다 — 약통에 아무것도 넣지 않았으므로 같은 시도의 연장이다. 재스쿱 깊이는 `직전 fraction × (남은 목표량 / 방금 잰 초과 스쿱량)` 이며 `min_fraction` 을 하한으로 둔다 — 비율 자체를 쓰면 이미 얕게 판 경우 깊이가 한 값에 멈춰 수렴하지 않는다. 반환은 `actual_g`에 더하지 않고 `ScoopCycle.delivered_g=0`, `valid=false`로 기록한다. 반환 후 잔량은 새로운 빈 스쿱 영점으로 숨기지 않고 기존 tare를 유지해 다음 계량에 포함한다.

### 1.2 인터페이스별 방향과 필드

메시지는 토픽으로 독립 전송되거나 서비스·액션 안에 포함된다.

| 메시지 | 방향 | 필드 의미 |
|---|---|---|
| `RecipeItem` | HMI → process (`Recipe.items`) | `material_id`: 원료 ID, `target_g`: 목표 순량, `tol_pct`: 허용 오차율. 전용 스쿱은 메시지가 아니라 `stations.yaml` 의 `scoop_N`(`material_id` 일치)으로 찾는다 |
| `Recipe` | HMI → process (`SubmitOrder`/`RunBatch`) | `header`: 생성 시각, `batch_id`: 빈 값이면 process가 발급, `product`: 표시명, `items`: 투입 순서 그대로의 원료 배열 |
| `CellState` | process → HMI·record | `mode`: 셀 운전 모드, `batch_id`: 현재 배치, `step`: FSM 상태, `item_index`: 0 기반 원료 순번, `station`: 마지막 도착 위치, `note`: 화면용 보충 설명 |
| `WeightReading` | skill → process (`WeighContainer.Result`), process → HMI·record (`weight`) | `gross_g`: 기준 차감 전 값, `tare_g`: 동일 자세·파지의 빈 용기/스쿱 기준, `net_g`: 차감값, `std_g`·`samples`: 분산과 표본 수, `valid`: 사용 가능 여부, `station`: 계량 자세 ID |
| `ScoopCycle` | process → record | 스쿠핑 시도 한 건의 완결 기록. 세부 필드는 1.1 표를 따른다 |
| `DispenseResult` | process → HMI·record | `batch_id`·`material_id`, `target_g`·`actual_g`, `error_pct`, `verdict`(`OK/UNDER/OVER/INVALID`), `attempts`, `duration_s` |
| `Deviation` | process → HMI·record | `deviation_id`: 판정 대상 ID, 배치·원료 ID, `kind`: 일탈 종류, `detail`: 설명, `requires_decision`: QA 필요 여부, `decision`: 판정, `operator_id`: 판정자 |
| `CellEvent` | 모든 노드 → record; `NUDGE`는 process도 수신 | `level`: INFO/WARN/ERROR, `code`: 기계 판독용 이벤트 코드, `text`: 사람용 상세, `batch_id`: 관련 배치 |
| `GripperState` | skill → process·HMI | `width_mm`: 현재 폭, `busy`: 동작 중, `grip_inferred`: 파지 판정 — `modbus` 는 드라이버 gSTA grip 비트(9/20 PR #38 `rg2_status_driver`), `virtual`은 폭 추론, `dio`는 DI 완료 확인(9/23, 아래 운용 주석), `safety_triggered`: 안전 상태 비트(modbus)·그 외 false, `force_cmd_n`: 명령 파지력, `backend`: modbus/dio/virtual |

서비스는 요청 후 즉시 단일 응답을 돌려준다.

| 서비스 | 방향 | 요청 → 응답 필드 의미 |
|---|---|---|
| `SubmitOrder` | HMI → process | `recipe` → `accepted`, process가 정한 `batch_id`, `message`. 실행 완료가 아니라 **접수 결과**다 |
| `QaDecision` | HMI → process | `deviation_id`, `decision`, `operator_id` → `accepted`, `message`. 배치는 해당 `Deviation`에서 확인한다 |
| `InterlockRequest` | HMI → process | `request`(`ENTER/EXIT`), `reason` → `granted`, `message`. ENTER는 안전 자세 도달 뒤 승인한다 |
| `SetGripper` | process → skill | `close`, `width_mm`, `force_n`, `timeout_s` → `success`, 실제 정지 폭 `final_width_mm`, `grip_inferred`(modbus는 grip 비트, virtual은 폭 추론, dio는 DI 완료 확인), `message` |
| `MeasureForce` | process → skill | `samples`, `settle_s` → `force[6]`, `fz_mean_n`, `fz_std_n`, `valid`, `message`. `force`는 `get_tool_force(DR_BASE)`의 tool 외력 wrench `[Fx,Fy,Fz,Mx,My,Mz]`; 앞 3개는 N, 뒤 3개는 N·m이며 관절 토크가 아니다. 작용점은 컨트롤러의 설정 tool/TCP 기준으로 사용하고 실물 G1에서 확인한다 |
| `SafePose` | process → skill | `reason` → `success`, `message`. 인터락·오류 시 공통 안전 자세로 후퇴한다 |

액션은 긴 동작 중 Feedback을 여러 번 보내고 종료 시 Result를 한 번 보낸다.

| 액션 | 방향 | Goal → Result / Feedback 필드 의미 |
|---|---|---|
| `MoveToStation` | process → skill | Goal `station_id`, `approach`(`ABOVE/AT`), `vel_scale`; Result `success`, `message`, 실제 `reached`; Feedback `phase` |
| `Scoop` | process → skill | Goal `material_id`, `attempt`, `depth_fraction`(담그기 깊이 비율); Result `success`, 최종 `contact_detected`, `max_contact_force_n`, `insertion_depth_mm`, `message`; Feedback `phase`, 현재 접촉 여부·힘·삽입 깊이. 수동 `height_measure_only`에서는 실제 스쿠핑 없이 `success=false`와 `message`의 `HEIGHT_MEASUREMENT_ONLY` 진단 문자열로 높이만 보고하며 자동 공정과 병용하지 않는다 |
| `Pour` | process → skill | Goal `fraction=1.0`(그 외 이동 전 거부); Result `success`, `message`; Feedback `phase`. 목적지는 skill 설정의 고정 `workbench`이다 |
| `ReturnMaterial` | process → skill | Goal `material_id`; Result `success`, `message`; Feedback `phase`(`APPROACH/TILT/HOLD`; `RETURN` 미발행). 동일 원료의 `return_start_posx` 직선 이동 → `return_end_posj` 관절 이동 후 끝 자세에서 종료. 반환 끝 관절 이동 시도부터 후속 Scoop은 연결 경로 구현 전까지 이동 없이 실패한다. 반환 동작 완료는 완전 배출량의 측정 보증이 아니다 |
| `WeighHeld` | process → skill | Goal `tare_g`; Result `reading`, `success`, `message`; Feedback `phase`. 파지 이력의 원료를 확인해 `material_N.posx`에서 측정. 원료를 알 수 없으면 실패 |
| `WeighContainer` | process → skill | Goal `tare_g`; Result `reading`, `success`, `message`; Feedback `phase`. 고정 `workbench`의 용기를 들어 측정하고 내려놓는다 |
| `RunBatch` | HMI/CLI → process | Goal `recipe`; Result `success`, 완료 원료 수, 일탈 수, 종료 `result`, `message`; Feedback `state`, `last_result`. 접수만 하는 `SubmitOrder`와 달리 진행·최종 결과가 필요한 클라이언트용이다 |

## 2. 확정된 값 — 더 논의하지 않는다

| 항목 | 확정 | 근거 |
|---|---|---|
| **단위** | 무게 **g**, 힘 **N**, 길이 **mm**, 각도 **deg**, 시간 **s**. 메시지 필드명에 단위 접미사 (`_g`, `_mm`, `_n`, `_s`) | 두산 API 가 mm·deg 라 맞춘다. 단위 없는 숫자는 통합일에 10배 오차로 드러난다 |
| **힘 → 그램** | `g = −(Fz_base − Fz_zero) / 9.80665 × 1000`. `Fz_zero`는 같은 자세의 빈 그리퍼 기준이고, `WeightReading.tare_g`는 같은 파지 조건의 빈 용기·빈 스쿱 기준값이다 | 중력·CoG 영향은 자세에 따라 달라 **동일 원료의 빈 스쿱·붓기 전·후 자세를 `material_N.posx`로 고정**한다. 용기는 `workbench` ABOVE |
| **판정** | `error_pct = (actual − target) / target × 100`. `|error| ≤ tol` → `OK`, `actual < target` → `UNDER`(보정 투입), `actual > target(1+tol)` → **`OVER` = 일탈** | 초과는 되돌릴 수 없다 — 회수 동작을 만들지 않는다 |
| **재시도 상한** | 둘은 **다른 상한**이다 (#189). `max_attempts` 8 — **붓기 시도**, 레시피 종속이라 `ceil(최대 목표량 ÷ 스쿱 1회량)` + 여유여야 한다 (데모 200 g ÷ 40 g → 하한 5). `max_returns` 3 — **초과 반환**, 깊이 보정 수렴을 보는 값이라 레시피와 무관하다. 둘 다 파라미터이고 넘으면 `Deviation(kind=TIMEOUT)` 로 QA 판정 | 무한 루프가 무인 운전을 죽인다. 한 상수로 묶으면 큰 레시피 때문에 붓기 상한을 올릴 때 반환 허용도 같이 올라간다 |
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
| 자동 복구율 | 일탈 1건 | `decision == AUTO_RECOVERED` / 전체 일탈 (FORCED 는 분모에만 든다) | `deviation` |
| 사이클타임 | 배치 1건 | 수락 → 완료 | `record` JSON |

## 7. DB 스키마 (배치 기록)

`gmp_hmi/config/schema.sql` 이 기준. **기록 주체는 `record_node` 하나**, HMI 는 읽기만. 파일 `~/auto-pharmacist/records/cell.db` (파라미터 `db_path`).

| 테이블 | 담는 것 | 원천 토픽 | 비고 |
|---|---|---|---|
| `batches` | 배치 1건 — 시작·종료·결과·사이클타임 | `state` 의 mode 전이 | RUNNING 진입 = 시작, DONE/ERROR 진입 = 종료 |
| `items` | 원료별 분주 결과 | `dispense_result` | 목표·실측·오차·판정·시도 |
| `weights` | 계량 1회 | `weight` | 그래프·분해능 근거. `valid=0` 도 남긴다 |
| `scoop_cycles` (v1.2, 구현 완료 — `UNIQUE(batch_id, material_id, attempt)`·배치 인덱스) | 스쿠핑 시도별 특징·결과·독립 기준값 | `scoop_cycle` | `valid=0`과 실패 outcome도 원본으로 남기고 학습 단계에서 필터링한다 |
| `deviations` | 일탈 — 종류·판정·**판정자·판정 시각** | `deviation` | 같은 ID 재수신 시 판정만 갱신 |
| `events` | 전 이벤트, **append-only** | `event` | MTBI(`INTERVENTION_FORCED`)·자동복구율 원천. 수정·삭제 메서드 없음 |
| `audit` | **사람의 조작만** — 누가·언제·무엇 | `event` 중 `code` 가 `HMI_*` | `text` 첫 단어가 actor. 주문·QA 승인/폐기·인터락 |
| `batch_recipes` | 주문 시점의 레시피 원본 (`payload_json`) | `RunBatch`/`SubmitOrder` 접수 | 배치당 1건. 계량 목표선·재기동 이어하기의 근거 (PR #42) |
| `state_checkpoints` | 상태 전이마다 mode·step·item_index·station·note | `state` | 재기동 이어하기(추가 3) 체크포인트. C 복원 경로 연결은 미완 (#42) |
| `legacy_item_duplicates` | 스키마 이관 전 `items` 중복 행 보관 (`original_id` + 원본 JSON) | — | 삭제 대신 보관 — append-only 원칙. 신규 기록에는 쓰지 않는다 |

**규칙**
- 배치 종료 시 `records/<batch_id>.json` 으로 내보낸다 — **DB 가 원본, JSON 은 사본**(제출·인쇄용).
- 사람 접촉(D-21): `skill_node` 가 `CellEvent(code='NUDGE', text='<|F| N>')` 발행 → process `RUNNING→PAUSED(NUDGE)`, 다음 `NUDGE` 로 재개 (`PAUSED→이전 상태`). 일탈이 아니라 이벤트다 — MTBI 분모에 들지 않는다. process 는 정지에 들어갈 때 `CellEvent(code='PAUSE', level=WARN, text='<이유> 정지 — …')`, 풀릴 때 `CellEvent(code='RESUME', text='<이유> 해제')` 를 낸다 (이유 `NUDGE` | `INTERLOCK`). HMI 타임라인·PAUSED 사유 표시용. **세트 경계(D-23)** 는 정지가 아니라 별도 코드다: 반송 뒤 `nudge_wait` 에서 기다리기 시작할 때 `CellEvent(code='SET_DONE', text='NUDGE_WAIT — 세트 완료, 건드리면 다음 세트')`, 사람이 건드려 배치가 끝날 때 `CellEvent(code='SET_NEXT')`. 그동안 `state.mode=PAUSED`, `step=NUDGE_WAIT`, 주문은 거부된다.
- HMI 조작은 `CellEvent(code='HMI_ORDER'|'HMI_QA_APPROVE'|'HMI_QA_DISCARD'|'HMI_INTERLOCK_ENTER'|'HMI_INTERLOCK_EXIT', text='<actor> <detail>')` 로 발행한다. actor 가 비면 `unknown` — 시연에서는 반드시 ID 를 넣는다.
- 지표(6절)는 `tools/report.py` 가 이 DB 에서만 읽는다. CSV 를 따로 두지 않는다 — 두 기록이 갈라지면 둘 다 못 믿는다.

**QA 원격 승인은 개입이 아니다** — 규정이 요구하는 절차다. **인터락 `ENTER` 는 원료 보충이면 개입이 아니고, 에러 수습이면 강제 개입이다** (`InterlockRequest.reason` 으로 가른다).

값을 만드는 쪽(process)·기록하는 쪽(record)·집계하는 쪽(`tools/report.py`, 휴강 중 작성)을 나눈다.

### 7.1 v1.2 적용 인계 (9/18 기록 — 완료분은 SOT·todo 스냅샷 참조)

- **A/조장:** `SetGripper` 서버와 계약 정의 완료. `Scoop` 본문에서 Feedback/Result 관측값을 실제 채우고, `GripperState`의 `busy/grip_inferred/safety_triggered`를 어댑터 실값에 연결하며 G1에서 `MeasureForce`의 tool/TCP 기준을 확인한다.
- **C:** ✅ **9/18 완료** — `process_node` 가 스킬 8종을 계약대로 부른다. `grade/scoop_id` 없음, 원료 → `scoop_N` 은 `core/station_map.py` 가 `stations.yaml` 에서 풀고, `Pour`·`WeighContainer` 는 station 인자 없이 부르며, `SetGripper` 사용, `QaDecision.deviation_id` 불일치는 거부하고 판정 후 **같은 ID 로 재발행**한다. `scoop_cycle` 은 정상이면 `WEIGH_RESIDUAL` 뒤, 실패면 확정 단계에서 나간다. `CellState.station/note` 도 채운다. 확인: `test/test_process_node.py`(가짜 skill_node 로 레시피 1건 완주). **남은 것** — 6축 wrench 는 채울 경로가 없어 `*_wrench_valid=false` (I-008).
- **D:** 주문 생성에서 `grade/scoop_id`, `QaDecision.Request`에서 `batch_id` 제거(웹 화면의 배치 표시는 유지), `scoop_cycle` 구독·DB 테이블·JSON 내보내기 추가.
- **승인:** 9/20 사용자가 v1.2 전체 및 v1.3의 팀 승인 완료를 확인했다. 계약은 확정하며 실물 검증·후속 연동은 별도 항목으로 관리한다.

**계약 파일 쓸 때** — `.action`/`.srv` 의 상수는 `---` 로 갈린 **그 상수가 설명하는 절**에 적는다. 뒤쪽 절에 적으면 `Feedback`·`Response` 에만 생성되어 정작 쓸 곳에서 `AttributeError` 가 난다 (I-009 에서 실제로 났다). 참조는 `MoveToStation.Goal.ABOVE` 처럼 **절 이름을 붙여** 쓴다.


## 8. 안전 정지 복구 (v1.4·v1.6 확정)

사용자가 HMI 복구 요청 운영의 팀 합의를 확인했다. A 서비스는 `RecoverSafety.srv`, `/cell/recover_safety`다. HMI가 A를 직접 호출하지 않고 C가 필수 필드를 검사한 뒤 전달한다. 사용자 인증과 operator/admin 역할 검사는 HMI가 수행한다. C 중계 엔드포인트 `/cell/request_safety_recovery`는 필드가 비었거나 `operator_confirmed` 가 아니면 skill_node 를 부르지 않고 거부하고, 그 외는 그대로 전달해 A의 응답을 돌려준다. 상태·중복 요청·경합의 최종 판단은 A가 한다. HMI `/recover` 버튼·`POST /recover`는 operator/admin의 작업자·요청 ID·기대 상태·조치 확인을 전달하고 감사 기록을 남긴다. 남은 것은 A/C/D 실물 통합 검증이다.

| 필드 | 의미 |
|---|---|
| `request_id` | 기동 세션 내 고유 문자열. 재전송은 동일한 전체 요청을 사용한다. A는 처리 중 중복도 한 번만 실행한다. 처리 중 중복/다른 요청은 즉시 success=false·manual_required=false로 응답해 콜백을 점유하지 않으며, 동일 ID로 결과를 재조회한다. 기록 상한에 도달하면 새 요청을 거부한다. |
| `operator_id` | HMI가 인증 세션에서 채운 작업자. 감사 기록과 이벤트 추적에 사용하며, 동일 `request_id`의 결과 재조회·차단 해제 권한을 작업자별로 분리하지 않는다. |
| `expected_state` | 작업자가 확인한 두산 상태 정수. 현재 상태와 다르면 명령 없이 실패하고 새 상태를 돌려준다. |
| `operator_confirmed` | 원인 제거·현장 복구 가능 조건 확인. RECOVERY에서는 필요한 자세 교정/펜던트 조치를 완료했다는 확인이다. |
| `success` | 로봇 STANDBY 재확인·힘제어 해제·필요한 기동 자가진단 후 A 차단 해제. **배치 재개·안전 자세 도착·공정 IDLE 전환은 아니다.** |
| `manual_required` | 현장 조치 또는 상태 재확인 후 새 요청 필요. 복구 모드 진입만 한 경우에도 true다. |
| `robot_state` | 두산 상태 코드, 조회 불가/가상 미지원은 -1. |
| `message` | 운영자 안내/실패 원인. 실패는 자동 재시도하지 않는다. |

### 충돌 감도 자가진단 (v1.7)

- 경로: `skill_node` 단일 워커 → `DsrArm` → `/dsr01/dsr_controller2/system/get_collision_sensitivity`.
  `robot.id` 네임스페이스를 따르며, 서버는 `gmp_dsr_controller/RobotController`다.
- `srv/GetCollisionSensitivity`: 요청 필드 없음. 응답 `bool success`, `float32 sensitivity`, `string message`.
  성공 값은 유한한 0~100 %이며 SDK의 전역 `_fCollisionSensitivity`다. 실패 값은 사용하지 않는다.
- `safety.collision_sensitivity: 50.0`이 기대값이다(9/22 사용자 확정). 실물 기동 및 명시적 안전 복구 때
  툴/TCP와 함께 확인한다. 정확히 일치해야 통과하며, 불일치·조회 실패·시간 초과는 일반 동작/복구 해제를 허용하지 않는다.
- 자동 감도 변경·별도 로봇 연결은 없다. 가상 자가진단에서는 실물 감도 확인을 생략했다고 명시한다.
  조회는 로컬 안전 구역의 감도 재정의·실제 충돌 성능을 검증하지 않는다.
- 서비스 준비와 응답 대기에 각각 `robot.startup_timeout_s`를 적용하고 자동 재시도하지 않는다.
  클라이언트 시간 초과가 서버의 SDK 호출을 취소하지는 않는다.

### 상태별 실행

- STANDBY(1): 리셋 명령 없이 상태·해제 결과 확인. 벤더가 자동 리셋했더라도 사람의 이 요청 전에는 A 차단 유지.
- SAFE_STOP(5): reset type=0(프로그램 정지) 후 control=2, STANDBY 확인.
- SAFE_OFF(3): 작업자 확인 후 control=3, STANDBY 확인. STO/전원 차단 후 필요한 마스터링 등 펜던트 조치는 작업자가 확인해야 한다. Python 래퍼에 `check_robot_mastering`은 없다.
- SAFE_STOP2(9)/SAFE_OFF2(10): control=4/5로 RECOVERY(8) 진입 확인 후 **success=false, manual_required=true**. 자동 자세 이동은 없다.
- RECOVERY(8): 사람이 원인·자세를 교정한 뒤 새 요청으로 control=7, STANDBY 확인.
- EMERGENCY_STOP(6), MOVING(2), TEACHING(4), 초기화/조회 불가 등은 복구 명령을 보내지 않는다. 무동력동작(control=6)·자동 DRL 재개도 호출하지 않는다.

서비스 success=true만으로 판단하지 않는다. 벤더가 무조건 true로 응답하는 구현이므로 기대 상태를 직접 확인한다. `safety.recovery_timeout_s`는 서비스 응답 및 전이 관측 대기 설정이며, 기존 DSR 상태 조회 자체가 응답하지 않을 때의 강제 중단까지 보장하는 시간은 아니다.

### 이벤트 및 C/D 연동

- A `CellEvent(ERROR, code='ROBOT_SAFETY_STOP')`, `text`는 JSON `{robot_state, reason, origin, safety_session, safety_revision}`. 기존 event 경로 사용. C는 ERROR 및 새 주문 차단으로 연결하고 일반 FORCE_LIMIT 1회 재시도에서 제외한다.
  - **v1.6:** `origin`, `safety_session`(A 기동별 고유 ID), `safety_revision`(세션 내 증가 정수)을 추가한다.
  - 복구 요청 자체의 재잠금에는 `origin="recovery_request"`, 요청의 `request_id`·`operator_id`를 붙인다. 실제 알람은 `robot_alarm`, 상태 감시는 `state_monitor`이며 복구 ID를 붙이지 않는다. 같은 내용의 반복 알람도 정지 번호를 올려 발행한다.
  - D는 현재 요청 ID가 일치하는 복구 시작에만 요청을 유지한다. 작업자 ID는 감사 기록이며 operator/admin 사이의 인수인계를 제한하지 않는다. 시작 자체는 해제 근거가 아니다. 실제/불명 정지는 요청을 무효화하며, 늦은 시작/성공으로 되살리지 않는다. 성공 응답 뒤 도착한 같은 요청의 시작은 완료 상태를 덮지 않는다.
  - C는 모든 정지에서 차단한다. 복구 시작의 세션·정지 번호·요청 ID가 일치하고 `success=true`, `manual_required=false`, `robot_state=1`인 결과만 차단 해제에 사용한다. 새 알람·불명 이벤트·이전 기동 세션 결과는 해제 근거가 아니다. 구버전 A의 상관관계 없는 결과도 차단 해제에 쓰지 않는다.
  - A는 정지 변경·이벤트 발행을 직렬화하고, 복구 큐 대기 중/명령 직전/완료 직후 새 정지가 생겼으면 성공으로 처리하지 않는다.
- A `CellEvent(INFO/WARN, code='ROBOT_SAFETY_RECOVERY')`, `text`는 JSON `{request_id, operator_id, safety_session, safety_revision, success, manual_required, robot_state, message}`. 동일 요청 재전송은 명령/이벤트를 반복하지 않는다. A는 배치를 소유하지 않아 `batch_id`는 빈 문자열이다.
- C `process_node._on_event`, `_srv_submit`, 실행 루프 — **구현 완료(9/20)**: `ROBOT_SAFETY_STOP` 이 `_safety_stop` 플래그를 세우면 새 주문을 거부하고, QA·인터락·NUDGE 대기를 깨워 배치를 FORCE_LIMIT 재시도 없이 바로 ERROR 로 끝낸다(일탈 기록도 남기지 않는다). 유효한 `ROBOT_SAFETY_RECOVERY`만 플래그를 내린다 — 끝난 배치는 되살리지 않고 새 주문부터 받는다. "`_srv_submit` 의 ERROR 허용 수정" 은 `mode=='ERROR'` 전체 차단이 아니라 이 플래그로 좁혔다: 일반 실패도 `mode='ERROR'` 로 끝나므로 전체를 막으면 새 주문을 영영 못 받는다.
- D `hmi_web_node.py`, `static/hmi.js`, `templates/index.html`: 인증된 작업자·요청 ID·기대 상태·조치 확인을 C에 전달하는 버튼/POST 추가, 복구 진행/수동 조치/실패 표시. HMI 감사 이벤트에 현재 배치·작업자·요청 ID·결과를 기록한다. HMI가 ROS 클라이언트 권한까지 인증하는 것은 아니므로 접근 통제는 배포 설정에도 달려 있다.
- NUDGE·Interlock EXIT·controller STANDBY 관측만으로 안전 차단을 해제하지 않는다. 새 알람 뒤에는 과거 성공 응답을 재사용할 수 없다. A 복구 후에도 위치·파지 이력은 무효이므로 다음 공정 전에 명시적 안전 자세/현장 재설정을 수행한다.

### 감지 한계

A 워커는 작업 전·유휴·이동/계량 취소 확인 구간에서 상태를 조회하며 벤더 `/{robot.id}/dsr_controller2/error`도 수신한다. 콜백은 플래그/큐만 바꾸고 DSR 호출은 워커에 맡긴다. ERROR 알람 또는 안전 제어기 WARN 이상은 보수적으로 동작 차단한다. 상태·알람이 모두 유실된 순간 정지를 검출한다고 보장하지 않으며, 벤더 자체 자동 리셋 정책은 별도 실물 확인 대상이다. 가상 모드의 복구 요청은 성공 처리하지 않는다.


### 9/23 DIO·고정 티칭 경로 운용 주석 (필드·열거값 변경 없음)

- DIO `SetGripper`는 close만 개폐에 사용하고 width_mm/force_n은 무시한다.
  `final_width_mm`/`GripperState.width_mm`은 -1(미측정), `force_cmd_n`은 0(명령 없음).
  파지는 스쿱 DI1=DI2=1, 약통 DI1=1로 판단한다. 지문 검사는 현행 설정에서 비활성이다.
- `Scoop`의 고정 경로 모드는 depth_fraction=1만 지원하며 success는 경로 완료다.
  접촉 측정은 하지 않아 contact_detected=false, 힘·깊이=0과 `TAUGHT_FIXED` 미측정
  message를 반환한다. 센서값 0이나 접촉 실패의 증거로 사용하지 않는다.
  현 C FSM은 false를 SCOOP_EMPTY로 처리하므로 자동 공정 연계는 후속 합의·수정이 필요하다.
  인계 대상과 지원 범위는 [실행 안내](setup.md)의 B/C 인계 절을 따른다.
