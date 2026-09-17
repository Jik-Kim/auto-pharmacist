# PROJECT

## 한 줄 정의

배치 레시피에 따라 원료를 **정밀 칭량·분주**하고, 허용 오차를 **스스로 검증**하며, 일탈이 생기면 **셀 밖의 QA 에게 판정을 요청**하는
협동로봇(M0609 + RG2) 조제 칭량 셀. 평상시는 무인이고, 사람은 패스박스와 HMI 로만 셀과 만난다. — 규칙 원장 `PROJECT_RULES.md` R1~R23

## 기술 방향

- Ubuntu 24.04, ROS 2 Jazzy, Python 3.12, `rclpy`, `rosidl` — **Python 전용** (R9)
- Doosan M0609 (`doosan-robot2`, `DSR_ROBOT2` 파이썬 API, 네임스페이스 `dsr01`) + OnRobot RG2 (`onrobot-ros2`, Modbus TCP)
- 기존 워크스페이스 `~/ws_cobot_pjt/ws_dsr` 를 **언더레이**로 두고 이 저장소의 `ros2_ws` 를 오버레이로 얹는다 (`docs/setup.md`)
- 비전 없음 (R2). 감지는 로봇 외력(`get_tool_force`)·그리퍼 폭·힘 조건만으로 한다
- HMI 는 **웹(Flask)** — 셀 밖 QA 원격 승인 (SOT D-16). 배치 기록은 **SQLite** + 감사 추적 (D-17). 공정 오케스트레이션은 `core/` 의 **순수 Python 상태기계** (D-03)

## 역할

| 담당 | 패키지 | 영역 |
|---|---|---|
| **조장 고희태** (A 겸임) | `docs/`, `gmp_interfaces`, `gmp_bringup` | 일정·통합·막힐 때의 판단. **A~D 중 한 영역을 겸한다.** PM 은 없다 — 리뷰는 교차검수 (`AGENTS.md`) |
| **A 고희태** [스킬] | `gmp_skills` | DSR_ROBOT2 어댑터, RG2 어댑터, 스테이션 이동·파지·스쿱·붓기·계량 Action/Service — **로봇을 만지는 유일한 노드** |
| **B 김민준** [도징] | `gmp_dosing` | 힘→그램 환산·영점·보정(`core/scale.py`), 이중 폐루프 도징 정책(`core/dosing.py`) — ROS 비의존 라이브러리 |
| **C 김병직** [공정] | `gmp_process` | 레시피 실행 상태기계, 일탈 분기, 인터락, `RunBatch` Action 서버 |
| **D 서동권** [HMI·기록] | `gmp_hmi` | 웹 HMI(주문·상태·원격 QA 승인·인터락·이력), 배치 기록 DB(SQLite·감사 추적) |

4명 전원이 A~D 한 영역씩 (9/16 확정). 부담당 짝 A↔C (고희태↔김병직), B↔D (김민준↔서동권). **주제는 9/16 강사 승인으로 최종 확정** (`PROJECT_RULES.md` R26). 세부 결정과 미결 사항은 `docs/SOT.md`,
제약·근거의 원장은 `PROJECT_RULES.md`, 요구사항(BRD)·설계(SDD)는 `docs/spec/`.
