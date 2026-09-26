# GMP 조제 칭량 셀 시스템 설계서 (SDD)

**프로젝트:** auto-pharmacist

**날짜:** 2026-09-26

**버전:** 1.0

**상위 요구사항:** `GMP_Dispense_BRD_v1.0.md`
**기준 원장:** `PROJECT_RULES.md`, `docs/SOT.md`, `docs/interfaces.md` 계약 v1.8

## 1. 목적과 설계 원칙

본 문서는 BRD 요구사항을 실제 ROS 2 패키지, 상태 전이, 설정 및 검증 항목에 연결한다. 값과 동작이 충돌하면 규칙은 `PROJECT_RULES.md`, 설계 결정은 `docs/SOT.md`, 노드 간 계약은 `docs/interfaces.md`, 현행 구현은 코드와 설정 파일을 기준으로 판정한다.

- 로봇을 직접 제어하는 노드는 `gmp_skills/skill_node` 하나뿐이다.
- `DSR_ROBOT2` 호출은 단일 워커 스레드에서만 수행한다.
- 공정 판단은 `gmp_process`, 투입량 계산은 `gmp_dosing`의 ROS 비의존 코어가 담당한다.
- DB 쓰기는 `gmp_hmi/record_node`만 수행하고 이벤트는 append-only로 기록한다.
- 힘 상한, 충돌 감지, 힘제어 해제 및 안전 자세 복귀는 HMI 없이도 성립해야 한다.

## 2. 시스템 구성

```text
웹 HMI ── 주문·판정·인터락 ──> gmp_process/process_node
                                      │
                                      │ Action·Service
                                      v
                              gmp_skills/skill_node
                                      │
                               DSR_ROBOT2 / DIO

skill/process/hmi ── CellEvent·계량·결과 ──> gmp_hmi/record_node ──> SQLite
```

| 패키지 | 책임 | 직접 로봇 제어 |
|---|---|---|
| `gmp_interfaces` | Action, Service, Message 정의 | 금지 |
| `gmp_skills` | 이동, 파지, 스쿱, 붓기, 계량, 안전 복구 | 유일하게 허용 |
| `gmp_dosing` | 계량 유효성, 스쿱 판단, 잔량 계산 | 금지 |
| `gmp_process` | 배치 FSM, 재시도, QA·인터락 조정 | 금지 |
| `gmp_hmi` | 웹 UI, 기록, 조회 | 금지 |
| `gmp_bringup` | launch와 YAML 설정 결합 | 해당 없음 |

## 3. 실행 및 동시성

`skill_node`의 Action/Service 콜백은 명령을 큐에 넣고 결과를 기다린다. 단일 워커가 큐를 순서대로 소비하며 모든 DSR 호출을 수행한다. 취소·예외 경로에서도 `release_force`와 `release_compliance_ctrl`을 `finally`에서 짝지어 실행한다.

`process_node`는 `ProcessFSM`의 상태와 컨텍스트를 보유하고 스킬 계약만 호출한다. HMI 요청과 기록 처리는 로봇 제어와 분리하여, UI 또는 DB 장애가 로봇 안전 정지를 방해하지 않게 한다.

## 4. 공정 상태 설계

현행 상태와 전이의 구현 원본은 `ros2_ws/src/gmp_process/gmp_process/core/process_fsm.py`이다.

```text
SELF_CHECK → PICK_CONTAINER → TARE
→ PICK_SCOOP → SCOOP_TARE → SCOOP → WEIGH_SCOOP
→ RETURN_MATERIAL 또는 POUR → WEIGH_RESIDUAL → RETURN_SCOOP
→ 다음 원료 또는 VERIFY → FINISH → NUDGE_WAIT → DONE
```

| 상태군 | 주요 처리 | 실패·분기 |
|---|---|---|
| 준비 | 자가진단, 빈 용기 이송, TARE | 실패 시 정리 후 ERROR |
| 스쿱 | 전용 스쿱 파지, 빈 스쿱 기준, 고정 스쿱 수행 | 계량 무효·파지 실패 처리 |
| 사전 판정 | 든 양 계량 및 도징 판단 | 초과는 `RETURN_MATERIAL`, 허용 시 `POUR` |
| 투입 후 | 잔량 계량, 실제 투입량 누적, 스쿱 반환 | 투입 후 계량 무효는 QA 경로 |
| 종료 | 용기 순량 `VERIFY`, 완성품 반송 | 규격 이탈은 `DEVIATION` |
| 마감 | `NUDGE_WAIT`에서 회수 터치 대기 | 승인된 미측정 종료는 `DONE_UNMEASURED` |

`PAUSED`, `DEVIATION`, `CLEANUP`은 정상 직선 흐름 바깥의 제어 상태다. `WEIGH_INVALID`는 투입 전이면 정리 후 오류, 투입 후이면 QA 판정으로 분리한다. 재시도 상한은 설정값을 따른다.

## 5. 로봇 동작과 스테이션

스테이션 좌표와 경로는 `ros2_ws/src/gmp_bringup/params/stations.yaml`, 속도·힘·계량 설정은 `ros2_ws/src/gmp_bringup/params/common.yaml`이 원본이다. 각 이동은 스테이션의 `above` 접근점과 작업점, 필요한 경우 `exit` 이탈점을 사용한다. 경로 의미를 바꾸면 생성기 `tools/make_process_drawio.py`와 공정 문서를 함께 갱신한다.

- `workbench`: 빈 용기 배치와 종료 용기 픽업의 기준 위치다.
- `passbox_empty`·`passbox_done`: 반입과 반출을 물리적으로 분리한다.
- `reject_bin`: 폐기 판정 때만 사용하는 별도 목적지다.
- `scoop_1~3`: 원료별 전용 스쿱으로 교차오염을 방지한다.
- `nudge_wait`: 완성품 회수 뒤 세트 마감 입력을 기다리는 안전 대기점이다.

현행 자동 스쿱은 설정된 고정 궤적을 사용한다. 원료 높이 측정은 파라미터와 단위 시험만 유지하며 `calibrated=false`인 운영 설정에서는 실행하지 않는다. 최초 접촉 정지도 힘 측정 신뢰성이 확보될 때까지 통합 흐름에서 비활성화한다.

## 6. 그리퍼 설계

기본 운전은 컨트롤러 DIO 출력으로 열기·닫기를 지시하고 DI로 완료 상태를 확인한다. DIO 경로에는 폭·힘 수치 명령이 없으므로 공정 성공 조건은 완료 입력과 스킬 결과다.

`rg2_status_driver`는 필요할 때 Modbus 상태를 ROS 토픽으로 노출하는 선택 경로다. 가상 모드는 명령과 상태 전이를 재현하지만 실제 파지력, 미끄럼 및 폭 지문은 검증하지 못한다.

## 7. 계량과 도징

계량은 고정 자세에서 `get_tool_force`의 Fz 표본을 수집하여 질량으로 환산한다. 부호, 오프셋, 표본 수, 표준편차 상한과 유효성 기준은 `common.yaml` 파라미터를 사용한다. 스쿱은 빈 기준, 원료를 든 상태, 붓고 난 잔량을 각각 측정하며 실제 투입량은 두 측정의 차이로 누적한다.

`VERIFY`는 종료 용기 순량을 레시피 규격과 비교한다. 스쿱 누적값과 용기값의 이중 계측 일치 판정은 SOT D-26에 따라 합격 조건으로 사용하지 않는다. 레시피 현행값은 `ros2_ws/src/gmp_bringup/params/recipes/`가 원본이다. D-33에 따라 각 품목의 원료 목표는 85 g 또는 170 g 단위이며 허용 오차는 ±10%다.

## 8. 안전과 복구

- 힘제어 진입과 해제는 항상 한 쌍으로 실행한다.
- 힘 상한이나 충돌 감지 시 동작을 중지하고 안전 상태를 보고한다.
- `RecoverSafety`는 원인 제거와 사용자 요청 뒤에만 수행한다.
- 출입 인터락은 안전 자세 도달을 확인한 뒤 허가한다.
- 소프트웨어 정지와 별개로 컨트롤러 및 비상정지의 하드웨어 안전 계층을 유지한다.

## 9. 인터페이스와 기록

계약의 유일한 원본은 `docs/interfaces.md` v1.8이다. 주요 흐름은 다음과 같다.

| 발신 | 수신 | 계약 |
|---|---|---|
| HMI | process | 주문, QA 판정, 인터락, 안전 복구 |
| process | skills | 이동, 스쿱, 붓기, 반환, 계량, 그리퍼 |
| skills/process | record | 상태, 계량, 사이클, 일탈, 이벤트 |
| HMI | DB | 읽기 전용 조회 |

`record_node`는 SQLite의 단일 writer다. 이벤트와 판정에는 배치 식별자와 순서를 남겨 재시작 뒤에도 감사 추적을 재구성할 수 있게 한다.

## 10. 배포와 설정

통합 기동은 `ros2_ws/src/gmp_bringup/launch/cell.launch.py`를 기준으로 한다. 공통 파라미터는 `common.yaml`, 좌표는 `stations.yaml`, 품목별 목표와 허용 범위는 `params/recipes/*.yaml`에 둔다. 확정되지 않은 값은 코드에 하드코딩하지 않는다.

## 11. 검증 전략

| 계층 | 검증 |
|---|---|
| 코어 | FSM, 도징 판단, 계량 유효성 단위 시험 |
| 노드 | Action/Service 계약, 취소·시간 초과, 기록 순서 시험 |
| 가상 통합 | 정상 배치, 반환, QA, 인터락, 복구 흐름 |
| 실물 통합 | 좌표·충돌 여유, 파지, 힘 계량, 붓기, 비상정지 |

힘 측정 정확도, 실제 파지력, 미끄럼, 충돌 감도와 안전 스위치는 실물에서만 합격 판정한다. 가상 시험 통과를 실물 검증으로 대체하지 않는다.

## 12. 요구사항 추적

| BRD 영역 | 설계 구현 |
|---|---|
| 무인 배치 조제 | `ProcessFSM`, HMI 주문, `RunBatch` |
| 로봇 단일 제어점 | `skill_node` 단일 워커 |
| 원료별 전용 스쿱 | `scoop_1~3`, 고정 스쿱 흐름 |
| Fz 계량·규격 판정 | 계량 스킬, `gmp_dosing`, `VERIFY` |
| 초과 반환 | `RETURN_MATERIAL` 전이 |
| 원격 QA | `DEVIATION`, QA 판정 계약 |
| 감사 추적 | `record_node`, SQLite, `CellEvent` |
| 출입·안전 복구 | 인터락, 안전 자세, `RecoverSafety` |

## 13. 개정 이력

| 날짜 | 버전 | 변경 내용 |
|---|---|---|
| 2026-09-26 | 1.0 | 계약 v1.8, SOT D-33 및 현행 패키지·FSM·설정 기준으로 최초 작성 (#151) |
