# Architecture

> 계약 **v1.7 (9/22)** 기준(v1.6 안전 복구 확정, v1.7 충돌 감도 자가진단 추가 — `interfaces.md`). v1.2 `WeighHeld`·v1.3 `ReturnMaterial`(전량 붓기, 초과는 반환)·v1.5 `Scoop.depth_fraction` 은 A 서버·C `process_node`·D HMI 에 반영됐다. **9/22 #216**: `Scoop` 이 접촉 측정 → 높이 보정 spline → 털기로 실제 스쿠핑을 하며, `stations.yaml` `scooping.<원료>.calibrated=true` 전에는 이동 전 거부된다(원료 A 만 경로 존재, 실물 미검증). v1.4 안전 복구(`RecoverSafety`, HMI→C→A 중계)는 A·C·D 구현 완료, 계약 확정 대기. **반환 뒤 재스쿱 연결 경로는 미구현**이라 실물 `skill_node` 가 후속 `Scoop` 을 거부한다 (v1.5.1) — 그때까지 반환이 나온 배치는 실물에서 `ERROR` 로 끝난다.

## 배치 (PC 1대 + 로봇 + 그리퍼)

한 PC 에서 전부 띄운다. 벤더 브링업(`m0609_rg2_bringup new_bringup.launch.py`, ns `dsr01`) 위에 우리 노드 4개(ns `cell`)를 얹는다.

```text
[벤더, ns /dsr01]  ros2_control + dsr_controller2 (모션·힘 서비스)   OnRobotRGControllerServer (/onrobot/sendCommand, /onrobot_joint_states) — real 은 `gmp_skills rg2_status_driver` 로 교체해 /onrobot/status(gSTA 비트)도 발행 (`robot.launch.py`, 9/20)
                   └ virtual: DRCF 에뮬레이터(docker) + gripper_virtual_node

[우리, ns /cell]   skill_node ──(DSR_ROBOT2 · /onrobot/*)──▶ 로봇·그리퍼
                        ▲ Action/Service
                   process_node ── state · weight · scoop_cycle · dispense_result · deviation · event ──▶ record_node ──▶ SQLite(cell.db)
                        ▲ submit_order · qa_decision · interlock · request_safety_recovery (Service)             │ 읽기
                        ▲ run_batch (Action — HMI 주문 경로. PR #163 로 process_node 서버 추가, 9/21. submit_order 와 같은 예약 슬롯·FSM 공유, 피드백 CellState+마지막 DispenseResult, 취소는 진행 중 스킬 응답 대기 뒤 ABORTED)
                        └──────────────── hmi_web_node (Flask :5000) ◀── 브라우저 (로봇 PC · 셀 밖 QA 기기) ◀───┘
```

## skill_node 내부 — 스레드 구조 (SOT D-02)

```text
[Main Thread]  MultiThreadedExecutor.spin(skill_node)      ← Action/Service 콜백, gripper_state 10 Hz 타이머
                   │ 콜백은 Job 을 큐에 넣고 Event 를 기다린다 (취소 요청은 플래그)
                   ▼
[Worker Thread] while: job = queue.get() → DsrArm/Rg2Gripper 호출 (블로킹) → 결과 Event.set()
                   │
                   ▼
[DR_init 노드, ns dsr01]  executor 에 넣지 않는다. DSR_ROBOT2 가 호출마다 spin_until_future_complete 로 직접 spin
```

교육 자료 「두산 ROS2 동작 Sequence」 3장과 같은 구조다. 워커가 하나이므로 **로봇 명령은 항상 직렬**이고, 두 스킬이 동시에 팔을 움직이는 일이 구조적으로 없다.

## 공정 사이클 ↔ 노드

`process_node` 의 상태기계(`core/process_fsm.py`)가 아래 순서로 스킬을 부른다. 원료마다 5~11 을 반복한다. **로봇이 저울**이므로 스쿱을 든 채 재는 것(`weigh_scoop`)이 가장 싸고, 용기 계량(`WeighContainer`)은 그리퍼가 비어야 해서 배치 끝 VERIFY 한 번만 한다 (D-22). 용기 반송(carry)은 `MoveToStation`+`SetGripper` 의 조합이라 별도 Action 이 없다.

| 단계 | 상태 | 스킬 호출 | 판정·분기 |
|---|---|---|---|
| 1 | `ACCEPTED` | — | `SubmitOrder` 수락, batch_id 발급 |
| 2 | `SELF_CHECK` | `MeasureForce`(빈 그리퍼) · 툴/TCP 확인 | 실패 → `ERROR` |
| 3 | `PICK_CONTAINER` | **carry**: `MoveToStation(passbox_empty, slot)` → `SetGripper(close, cup)` → `MoveToStation(workbench)` → `SetGripper(open)` | 사람이 Pass Box 「빈통」 칸에 넣어 둔 빈 약통을 로봇이 `workbench` 로 가져온다 (D-18·D-24, 매거진 폐지). `grip_inferred=false` → `GRIP_FAIL` 재시도 ≤ 3 |
| 4 | `TARE` | `MeasureForce` (빈 그리퍼 영점 기준, workbench ABOVE) → `WeighContainer(tare_g=0)` | 빈 용기 풍량 기록. 영점은 VERIFY 직전 대조 기준이 된다 — **같은 자세에서 재야 성립한다** |
| 5 | `PICK_SCOOP` | `MoveToStation(scoop_N)` → `SetGripper(close, scoop_width)` | `grip_inferred=false` → `Deviation(GRIP_FAIL)` 재시도 ≤ 3 |
| 6 | `SCOOP_TARE` | **`weigh_scoop`**(빈 스쿱, 든 채로) | 스쿱 풍량 — 원료마다 1회 (D-22) |
| 7 | `SCOOP` | `Scoop(material_id, depth_fraction)` — 깊이 비율은 v1.5, 실제 Z 변환은 A 실물 뒤 | `contact_detected=false` → `SCOOP_EMPTY` → 재시도 ×3, 4회째 → `MATERIAL_EMPTY` → 인터락 보충 요청 |
| 8 | `WEIGH_SCOOP` | `weigh_scoop`(붓기 전) | 퍼낸 양 = gross − 스쿱 풍량. **퍼낸 양 > 남은 목표 + target×tol** 이면 8a 반환, 아니면 9 전량 붓기 — 초과 예방 (1차 폐루프, v1.3) |
| 8a | `RETURN_MATERIAL` | `ReturnMaterial(material_id)` — `return_start_posx` 직선 → `return_end_posj` 관절, 끝 자세 유지 (v1.5.1) | 성공 → 7 재스쿱 (깊이 = 직전 × 남은량/퍼낸 양, 하한 `min_fraction`), returns ≥ `max_returns`(기본 3 — 붓기 상한 `max_attempts` 와 **분리**돼 있다, #189) → `Deviation(TIMEOUT)`. 실패 → `safe` 후 `ERROR`. **연결 경로 구현 전까지 실물 skill_node 는 후속 Scoop 을 거부한다** |
| 9 | `POUR` | `Pour(fraction=1.0)` — 전량 붓기, 목적지는 고정 `workbench` | 부분 붓기·`amove_periodic` 털어내기는 폐기 (v1.3). 초과는 8a 가 막는다 |
| 10 | `WEIGH_RESIDUAL` | `weigh_scoop`(붓기 후) → `dosing.decide()` | 잔량 = gross − 스쿱 풍량, **투입량 += 퍼낸 양 − 잔량**. 시도 1건을 `ScoopCycle`로 발행. `OK` → 11 / `UNDER` → 7 (보정, `dosing.max_attempts` 8 까지 — 초과 반환 상한 `max_returns` 3 과 별개, #189) / `OVER` → `Deviation(OVERFILL, requires_decision)` → `DEVIATION` |
| 11 | `RETURN_SCOOP` | `MoveToStation(scoop_N)` → `SetGripper(open)` | 원료별 전용 스쿱 반납 — **스쿱은 그 원료통 아래에 둔다** (9/18 확정, `scoop_rack` 폐지). 교차오염 경로를 끊고 이동 거리도 줄인다 |
| 12 | 다음 원료 → 5 | | |
| 13 | `VERIFY` | `MoveToStation(workbench, ABOVE)` → `MeasureForce` (빈 그리퍼 영점 재확인) → `WeighContainer(tare_g)` — **용기를 들어** 계량 (그리퍼 비어 있음) | **들기 전 영점 재확인**: TARE 때와 **같은 자세(workbench ABOVE)** 로 옮긴 뒤 `MeasureForce` 결과를 TARE 의 영점과 대조해 (`tool_force` 는 자세 의존이라 다른 자세끼리 비교하면 자세 차이가 영점 이동으로 둔갑한다) `|이동| > scale.zero_drift_limit_n`(기본 0.1 N — 같은 자세 반복 산포 기준, 0 이면 끔)이면 재측정, `max_invalid_retries` 도달 시 `Deviation(WEIGH_INVALID)` → QA. NUDGE 는 정지·재개 장치일 뿐 계량 유효성과 연결돼 있지 않아 오염된 값이 그냥 장부에 들어가던 구멍을 막는다. 통과하면 계량한다. **① 제품 판정 하나만 한다** (9/22 사용자·조장 확정, 종전 ② 폐지). `\|net − Σtarget\| > Σ(target×tol)` → `Deviation(BATCH_OUT_OF_SPEC)` → QA (폐기 권고). ~~② 계측 신뢰성 `\|net − Σ투입량\|`~~ 은 **판정하지 않고 관측만** 한다 — 값은 `verify_detail` 에 담겨 `CellEvent(INFO, VERIFY)` 로 나간다. ② 를 끈다는 것은 **배치 기록 교차검증을 포기한다**는 뜻이다 (제품은 규격 안인데 원료별 투입 기록이 틀린 배치를 검출할 수단이 없어진다) |
| 14 | `FINISH` | **carry**: `workbench` → `passbox_done` → `MoveToStation(nudge_wait)` | 완료품을 용기째 Pass Box 「완성품」 칸으로 (D-24) — QA 가 회수한다 (D-23). 이어 15 |
| 15 | `NUDGE_WAIT` | `nudge_wait` AT 에서 대기 (mode `PAUSED`, 주문 거부) | **세트 경계 (D-23)** — 사람이 회수하고 로봇을 건드리면(NUDGE, D-21) `DONE`/`DISCARDED` 로 끝나고 다음 주문을 받는다. 폐기도 여기로 온다 |
| E | `DEVIATION` | (로봇 대기) | `QaDecision` APPROVE → 다음 원료(VERIFY 였으면 FINISH) / DISCARD → 스쿱 반납 → **carry** `workbench` → `reject_bin` → 15 → `DISCARDED`. **`WRONG_TOOL`은 예외**(PR #165) — APPROVE 시 원료를 건너뛰지 않고 같은 원료를 이어간다: `PICK_CONTAINER`는 4 `TARE`, `PICK_SCOOP`는 6 `SCOOP_TARE`로 |
| E | `CLEANUP` | `ReturnMaterial`(WEIGH_SCOOP 일 때만) → `MoveToStation(material_N, AT)` → `MoveToStation(scoop_N, AT)` → `SetGripper(open)` | **투입 전 계량 무효의 정리 경로** (#213). 진입은 셋이고 손에 뭐가 있느냐로 갈린다 — `TARE`(빈 그리퍼, 용기는 workbench) 는 **정리 없이** 바로 `safe → ERROR`, `SCOOP_TARE`(빈 스쿱) 는 반환 없이 `material_N`(AT) 부터, `WEIGH_SCOOP`(원료 든 스쿱) 은 `ReturnMaterial` 먼저. 중간 경유 `material_N.posx`(AT) 는 원료통 위 충돌 회피 자세다. **재스쿱하지 않는다** (#64). 끝나면 `safe → ERROR`. 일탈은 **정리 시작 전에** `WEIGH_INVALID`(action `FORCED`, detail 에 정리 경로)로 남기고, 정리 중 반환이 실패하면 `FORCE_LIMIT`(FORCED)를 별건으로 더 남긴다 |
| E | `PAUSED` | `SafePose` | `InterlockRequest(ENTER)` → 안전 자세 도달 후 granted / `EXIT` → 이전 상태 재개 |

**도징 결정은 `gmp_dosing/core/dosing.py` 가 한다** (순수 함수: 목표·실측·이력 → 다음 행동). 상태기계는 그 결정을 스킬 호출로 옮길 뿐이다.
그래서 도징 정책은 로봇 없이 `pytest` 로 검증한다.

## 계층 원칙

- `core/` 는 ROS·장치 비의존 순수 함수(도징 정책, 힘→그램, 상태기계, 레시피 파싱, 기록 포맷). `nodes/` 가 메시지 변환과 호출 순서만 담당. `adapters/` 가 `DSR_ROBOT2`·`/onrobot/*` 를 감싼다.
- 안전은 `skill_node` + 두산 컨트롤러(충돌 감지·힘 상한)만으로 성립한다. process·hmi·record 가 죽어도 `SafePose` 는 된다.
- 기록은 `record_node` 하나가 쓴다. HMI 는 DB 를 읽기만 한다 — 쓰는 쪽이 둘이면 감사 추적이 성립하지 않는다.
- 로봇에 명령을 내는 노드는 `skill_node` 하나다. **발행자가 둘이면 로그만 보고 누가 움직였는지 가릴 수 없다.**

## 그림

시스템 흐름도·상태 전이도는 `docs/diagrams/` (9/24~28 휴강 중 작성, SDD 2·5장에 사용).
