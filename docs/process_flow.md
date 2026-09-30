# gmp_process — 로직·입출력 흐름

2026-09-30 현재 브랜치 구현과 [계약 v1.11](interfaces.md) 기준입니다.
코드 원본은 `ros2_ws/src/gmp_process/gmp_process/core/process_fsm.py`와
`nodes/process_node.py`입니다. 구현 경로와 실물 검증 완료 여부는 구분합니다.

## 1. 노드와 통신

모든 애플리케이션 노드는 작업 PC 1대의 `/cell` 네임스페이스에서 실행합니다.
`gmp_dosing`은 노드가 아니라 계량·도징 라이브러리입니다.

| 요청·데이터 | 방향 | 주요 내용 |
|---|---|---|
| `run_batch` Action | HMI → process | Goal `recipe`; Feedback `state`, `last_result`; Result `success`, `result`, `message` 및 집계 |
| `submit_order` Service | 클라이언트 → process | 레시피 접수 응답; 실행 완료 추적·예약은 RunBatch 사용 |
| `qa_decision` Service | HMI → process | `deviation_id`, `decision`, `operator_id` → `accepted` |
| `interlock` Service | HMI → process | ENTER/EXIT → `granted`, `message` |
| `request_safety_recovery` Service | HMI → process → skill `recover_safety` | 요청 ID·작업자·예상 로봇 상태·확인 → 복구 결과; 배치 재개와 별개 |
| `emergency_stop` Service | HMI → skill 직접 | `std_srvs/Trigger`; 안전 잠금·현재 Job 취소·대기 큐 제거, 워커가 이동 정지 처리 |
| `safe_pose` Service | HMI → skill 직접 또는 process → skill | HMI 사유 `HMI_SAFE_POSE`; 배치 진행·일시정지 중 HMI 요청 거부 |
| `state`, `weight`, `scoop_cycle`, `dispense_result`, `deviation`, `event` Topic | process → HMI·record | 상태·계량·시도·원료 결과·일탈·이벤트 |
| `gripper_state`, `event` Topic | skill → process·HMI, event → record | DIO 완료·안전·NUDGE 관측 |
| SQLite 배치 기록 | record → DB → HMI 조회 | 배치 기록 단일 작성자; JSON은 내보내기 사본 |

process 콜백은 ROS 요청·응답과 동기화 상태를 처리합니다. 인터락·안전복구 콜백은
스킬 서비스를 호출하므로 「콜백은 값만 저장한다」는 설명은 정확하지 않습니다.
장치 직접 호출은 skill_node의 단일 DSR 워커만 담당합니다.

## 2. FSM 요청과 스킬 호출

| kind | 호출·처리 | 결과 의미 |
|---|---|---|
| `safe` | `SafePose(reason)`; BATCH_START이면 `RestoreGrip(expected_payload=empty)` 추가 확인 | 실패 시 후속 동작 차단 |
| `measure` | `MeasureForce(scale.samples, scale.settle_s)` | valid·fz_mean_n·fz_std_n; SELF_CHECK는 응답 확인, valid 단독 게이트 아님 |
| `carry` | 출발 ABOVE→AT→닫기→ABOVE→도착 ABOVE→AT→열기→ABOVE | MoveToStation 6회·SetGripper 2회; 파지 실패 시 목적지 이송 중단 |
| `move` | `MoveToStation(station_id, approach, vel_scale)` | 실제 도착·성공 확인 |
| `grip` | `SetGripper(close, width_mm, force_n, timeout_s)` | 현행 DIO는 개폐·DI 완료; 폭·파지력 요청 미적용 |
| `weigh_scoop` | `WeighHeld(tare_g)` | 원료별 material_N 자세의 gross·net·std·valid·subject |
| `weigh` | `WeighContainer(tare_g)` | workbench 용기를 들어 계량·내려놓기; 빈 그리퍼 필요 |
| `scoop` | `Scoop(material_id, attempt, depth_fraction)` | 고정 경로는 1.0; 접촉 false·힘/깊이 0은 미측정 |
| `pour` | `Pour(fraction=1.0)` | 전량 붓기 성공; 실제 배출량 측정과 다름 |
| `return_material` | `ReturnMaterial(material_id)` | 시작→끝 TCP 직선 이동·BASE 주기 털기·끝 자세 확인 |
| `wait_qa` | QA 이벤트 대기 | APPROVED / DISCARDED |
| `wait_interlock` | 안전 자세·파지 복구 조건 충족 후 EXIT 대기 해제 | carry 중단 재개는 거부 |
| `wait_nudge` | 세트 끝 NUDGE 대기 | SET_NEXT; 이어 SafePose 성공 후 배치 종료 |

Action 실패·응답 불확실성·취소는 정상 결과와 분리해 처리합니다.
RunBatch 취소 접수는 물리 즉시 정지 완료를 뜻하지 않습니다.

## 3. 정상 공정

```text
RunBatch 수락 (또는 세트 끝 1건 예약 → 직전 넛지 종료 후 시작)
  → SELF_CHECK: SafePose(BATCH_START) → RestoreGrip(empty) → MeasureForce 응답
  → PICK_CONTAINER: passbox_empty → workbench
  → TARE: 빈 그리퍼 영점 기준 → 빈 용기 계량
  → 원료마다:
      PICK_SCOOP → SCOOP_TARE → SCOOP → WEIGH_SCOOP
        ├─ 빈 스쿱 → 재시도 / MATERIAL_EMPTY 보충
        ├─ 반환 조건 → RETURN_MATERIAL → 재스쿱 / 반환 한도 QA
        └─ 투입 가능 → POUR → 붓기 전 순량 추정 누적·decide
                             ├─ 추가 스쿱 → material AT → SCOOP
                             ├─ 일탈 → QA
                             └─ 원료 종료 → RETURN_SCOOP
  → VERIFY: 같은 자세 영점 재확인 → 최종 용기 계량·총량 판정
  → FINISH: workbench → passbox_done
  → NUDGE_WAIT: nudge_wait → 회수 후 NUDGE → SafePose
  → DONE / BATCH_END → 예약 주문 시작 가능
```

현행 운영값은 `tare_agree_g=0`으로 빈 스쿱을 1회 계량합니다.
코드에서 `tare_agree_g>0`으로 설정할 때 유효값 2회가 허용 차 이내면 평균,
아니면 3번째 유효값까지 모아 중앙값을 씁니다. `tare_agree_g=0`이면 1회입니다.
기준에서 원료별 `scoop_tare_bias`를 차감합니다. 무효 재측정 횟수와 기준 일치
확인을 위한 추가 계량은 다른 조건입니다.

붓기 성공 후 `actual_g += scooped_g`로 **추정** 누적합니다. 잔량 계량은 없고
`POUR_ESTIMATE`를 남깁니다. ScoopCycle의 post는 미측정(`valid=false`),
`delivered_g=0`이며 이를 실측 투입량으로 읽지 않습니다.

## 4. 판단·예외 분기

| 지점 | 조건 | 후속 처리 |
|---|---|---|
| TARE | 무효 재시도 상한 초과 | 정리 없이 SafePose → ERROR |
| SCOOP_TARE | 무효 재시도 상한 초과 | 빈 스쿱 반납 → SafePose → ERROR |
| WEIGH_SCOOP | 무효 재시도 상한 초과(현행 3회 무효) | WEIGH_INVALID/RETRY 기록 → 반환·재스쿱 |
| WEIGH_SCOOP | 유효 순량 ≤ empty_scoop_g | SCOOP_EMPTY 3회 재시도, 4회째 MATERIAL_EMPTY → REFILL |
| WEIGH_SCOOP | accept_next=True | 유효·빈 스쿱 검사 통과 후 초과·보충 불가 검사 없이 전량 붓기 |
| WEIGH_SCOOP | 순량 > 남은 목표 + 허용폭 | 붓기 전 반환 |
| WEIGH_SCOOP | 부으면 하한 미달이고 최소 보충 시 상한 초과 | 붓기 전 반환 (`_short_without_remedy`) |
| RETURN_MATERIAL | 반환 실패 | 강제 종료·안전 처리; 반환 한도 TIMEOUT과 구분 |
| RETURN_MATERIAL | 성공, returns < max_returns | 기존 tare 유지·반환 끝에서 material 계량 자세로 연결 후 재스쿱 |
| RETURN_MATERIAL | 성공, returns ≥ max_returns | TIMEOUT → QA; 승인 시 accept_next를 세우고 한 스쿱 더 퍼 붓기 |
| POUR | 정상 누적 후 추가 스쿱 가능 | material AT → SCOOP; 스쿱을 반납·재파지하지 않음 |
| POUR | QA 한 스쿱 승인 건 | verdict를 기록하고 원료 종료; 동일 사실로 QA 반복 안 함 |
| VERIFY 영점 | 오염 의심 반복 → QA 승인 | 최종 용기 계량을 수행; FINISH로 건너뛰지 않음 |
| VERIFY 계량·규격 | 무효 또는 총량 규격 이탈 → QA | 승인·폐기를 기록; 원료별 조성 검증을 뜻하지 않음 |
| QA 폐기 | 스쿱 보유 시 먼저 반납 | 용기 reject_bin 반송 → SafePose(DISCARD_PARK) → safe→nudge_wait |
| 세트 끝 | 회수 후 NUDGE | SafePose 성공 후 DONE/DISCARDED; 반송·대기 중 완료 발행 안 함 |

VERIFY 판정은 `abs(net - Σtarget) ≤ Σ(target × tol_pct / 100)`입니다.
추정 누적 투입량과의 차이는 관측만 하며 별도 VERIFY_MISMATCH 판정을 하지 않습니다.

CLEANUP의 빈 스쿱 정리는 material AT → scoop AT → 열기입니다.
원료 반환 후 수납은 ReturnMaterial → scoop AT → 열기이며 별도 material AT를
끼우지 않습니다. WEIGH_SCOOP 무효는 현행 자동 흐름에서 CLEANUP이 아니라 반환·재스쿱입니다.

## 5. 인터락·안전·예약

- ENTER: 진행 스킬 취소·응답 확인 → SafePose 도달 후 granted.
- EXIT: 안전·실행 상태 확인 → RestoreGrip 성공 → 상태 재확인 후 재개 허용.
  carry 중단은 중복 파지 위험으로 재개를 거부하며 배치를 취소합니다.
- 소프트웨어 비상정지: HMI → A emergency_stop → 안전 잠금·취소·큐 제거 →
  워커 이동 정지, ROBOT_SAFETY_STOP 이벤트로 C/D가 배치 중단·표시·기록.
  물리 비상정지의 대체가 아니며 Trigger 응답은 물리 정지 완료 증명이 아닙니다.
- 복구: HMI → C → A recover_safety. 복구와 배치 재개를 분리합니다.
- SafePose: 힘·순응 해제 → 해당 용기/원료통 위치의 이탈 처리 → safe.posj 이동.
  이탈 실패 시 후속 이동하지 않습니다. 스쿱 거치대는 별도 수직 이탈 대상이 아닙니다.
- HMI 안전 자세: 복구 후 별도 요청, RUNNING/PAUSED 배치 중에는 거부합니다.
- NUDGE: 워커 유휴·계량·붓기 대기에서 관측합니다. 운전 중 정지/재개와
  세트 끝 회수 확인을 구분하며 블로킹 이동 중 관측을 보장하지 않습니다.
- 세트 끝 RunBatch 예약은 1건입니다. 넛지 없이 취소·안전 정지·오류로 종료되면
  예약은 시작하지 않고 ORDER_DROPPED로 기록합니다.

## 6. 도면 원본과 갱신

| 도면 | 페이지 | 관리 방식 |
|---|---|---|
| [process_flow.drawio](diagrams/process_flow.drawio) | 노드 입출력 / FSM / 요청 번역 / 상태별 통신 | `python3 tools/make_process_drawio.py` |
| [TL_A_function_flow.drawio](diagrams/TL_A_function_flow.drawio) | 역할 / 정상 기능 / 안전·예외 | 기존 XML·레이아웃을 직접 수정 |
| [HMI_DB_Flow.drawio](diagrams/HMI_DB_Flow.drawio) | HMI·DB 통신 / 인터페이스·범위 | 제공된 9/21 도면을 저장소 사본으로 현행화 |

Control_Server.drawio는 **표현 구조 참고**입니다. 그 프로젝트의 토큰·AMR·비전 로직을
이 셀의 요구사항으로 가져오지 않습니다. 모든 도면은 작업·판단·통신의 구분,
요청/피드백/결과 및 실제 분기 방향을 함께 표시합니다.

## 7. 구현 범위와 검증 구분

RunBatch 운영 서버·예약, 반환 뒤 고정 재스쿠핑, 폐기 뒤 safe 경유 넛지,
HMI 소프트웨어 비상정지·안전 자세 요청은 현재 소스에 있습니다.
체크포인트 조회를 자동 이어하기 구현으로, DIO 완료 입력을 폭·파지력 측정으로
표시하지 않습니다. 실물 확인 범위는 `practice/`의 해당 날짜 일지를 따릅니다.

## 8. 스킬 실패 처리

일반 스킬 실패는 `SkillError`에서 `ProcessFSM.skill_failed()`로 전달합니다.
FORCE_LIMIT 정책의 재시도와 강제 종료를 구분하며, 반환·반환 후 재스쿠핑 실패는
추가 재투입 없이 FORCED → SafePose → ERROR입니다. 안전 잠금·실행 불확실성 또는
SafePose 자체 실패는 일반 재시도로 계속 진행하지 않습니다.
