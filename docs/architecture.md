# Architecture

> 아래 v1.2 인터페이스와 `scoop_cycle` 경로는 통합 목표다. 현재 A의 계약·서버 변경은 반영됐고, process·HMI·record 적용은 `docs/interfaces.md` 8절 인계가 남아 있다.

## 배치 (PC 1대 + 로봇 + 그리퍼)

한 PC 에서 전부 띄운다. 벤더 브링업(`m0609_rg2_bringup new_bringup.launch.py`, ns `dsr01`) 위에 우리 노드 4개(ns `cell`)를 얹는다.

```text
[벤더, ns /dsr01]  ros2_control + dsr_controller2 (모션·힘 서비스)   OnRobotRGControllerServer (/onrobot/sendCommand, /onrobot_joint_states)
                   └ virtual: DRCF 에뮬레이터(docker) + gripper_virtual_node

[우리, ns /cell]   skill_node ──(DSR_ROBOT2 · /onrobot/*)──▶ 로봇·그리퍼
                        ▲ Action/Service
                   process_node ── state · weight · scoop_cycle · dispense_result · deviation · event ──▶ record_node ──▶ SQLite(cell.db)
                        ▲ submit_order · qa_decision · interlock (Service)                                       │ 읽기
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
| 3 | `PICK_CONTAINER` | **carry**: `MoveToStation(magazine, slot)` → `SetGripper(close, cup)` → `MoveToStation(scale)` → `SetGripper(open)` | 사람이 매거진에 넣어 둔 빈 약통을 로봇이 칭량 위치로 가져온다 (D-18). `grip_inferred=false` → `GRIP_FAIL` 재시도 ≤ 3 |
| 4 | `TARE` | `WeighContainer(tare_g=0)` | 빈 용기 풍량 기록 |
| 5 | `PICK_SCOOP` | `MoveToStation(scoop_rack)` → `SetGripper(close, scoop_width)` | `grip_inferred=false` → `Deviation(GRIP_FAIL)` 재시도 ≤ 3 |
| 6 | `SCOOP_TARE` | **`weigh_scoop`**(빈 스쿱, 든 채로) | 스쿱 풍량 — 원료마다 1회 (D-22) |
| 7 | `SCOOP` | `Scoop(material_id)` | `contact_detected=false` → `SCOOP_EMPTY` → 재시도, 연속 3회 → `MATERIAL_EMPTY` → 인터락 보충 요청 |
| 8 | `WEIGH_SCOOP` | `weigh_scoop`(붓기 전) | 퍼낸 양 = gross − 스쿱 풍량. **붓기 비율 = min(1, 부족량/퍼낸 양)** — 초과 예방 (1차 폐루프) |
| 9 | `POUR` | `Pour(fraction)` — 목적지는 고정 `scale` | |
| 10 | `WEIGH_RESIDUAL` | `weigh_scoop`(붓기 후) → `dosing.decide()` | 잔량 = gross − 스쿱 풍량, **투입량 += 퍼낸 양 − 잔량**. 시도 1건을 `ScoopCycle`로 발행. `OK` → 11 / `UNDER` → 7 (보정, ≤3) / `OVER` → `Deviation(OVERFILL, requires_decision)` → `DEVIATION` |
| 11 | `RETURN_SCOOP` | `MoveToStation(scoop_rack)` → `SetGripper(open)` | 원료별 전용 스쿱 반납 (교차오염 방지) |
| 12 | 다음 원료 → 5 | | |
| 13 | `VERIFY` | `WeighContainer(tare_g)` — **용기를 들어** 계량 (그리퍼 비어 있음) | **두 가지를 본다** (9/17 조장 합의). ① **제품 판정** `\|net − Σtarget\| > Σ(target×tol)` → `Deviation(BATCH_OUT_OF_SPEC)` → QA (폐기 권고) ② **계측 신뢰성** `\|net − Σ투입량\| > min_resolvable_g` → `Deviation(VERIFY_MISMATCH)` → QA. **①이 규격 판정이다** — 원료가 전부 같은 방향으로 치우치면 net 과 Σ투입량이 함께 낮아 ②로는 안 잡힌다 |
| 14 | `FINISH` | **carry**: `scale` → `output_tray(slot)` … `SafePose` | 완료품을 용기째 트레이로. `DONE` 발행, 기록 종료 |
| E | `DEVIATION` | (로봇 대기) | `QaDecision` APPROVE → 다음 원료(VERIFY 였으면 FINISH) / DISCARD → 스쿱 반납 → **carry** `scale` → `reject_bin` → `DISCARDED` |
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
