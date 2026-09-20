# Source of Truth

> 상태: 골격 생성 시점 (2026-09-16 밤). `PROJECT_RULES.md` R1~R23 의 결정을 코드 기준으로 옮김
> 갱신: 확정 결정이 생길 때마다 조장이 갱신 (팀 합의 결과만). 규칙(강사 지침·장비 제약)의 원장은 `PROJECT_RULES.md`, 여기는 **설계·코드 결정**만

## 목표

M0609 + RG2 로 **조제 칭량 셀**을 만든다 — 레시피 1건(원료 3종)을 스쿱으로 퍼서 칭량 용기에 분주하고, 로봇 외력으로 무게를 검증하며,
허용 오차 밖이면 일탈로 처리해 HMI 의 QA 판정을 받는다. 9/23 까지 **무인 연속 배치 N회**와 **일탈 자동 처리** 영상을 확보하고, 9/29 시연·9/30 발표.

평가 기준(R12)과의 대응은 `PROJECT_RULES.md` 3-8. 이 프로젝트가 점수를 얻는 곳은 **「기능 동작 지속성」(일탈·복구)** 과 **「입출력 데이터 이해도」(배치 기록 HMI)** 다.

## 확정 사항

| ID | 항목 | 결정 |
|---|---|---|
| D-01 | 로봇 접근 | **`DSR_ROBOT2` 파이썬 API 만 쓴다** (9/16 교육 방식, `rokey/move.py`). 일반 제어는 서비스 클라이언트를 직접 만들지 않는다. **9/20 사용자 승인 예외:** A `DsrArm`의 단일 워커에서 기존 `system/set_robot_control` 복구 서비스만 직접 호출한다(기존 MoveStop 직접 호출 유지). 벤더 수정은 없다. 네임스페이스 `dsr01`, 모델 `m0609`, `DR_init` → 노드 생성 → import 순서 |
| D-02 | **DSR 호출 위치** | 교육 자료 「두산 ROS2 동작 Sequence」 3장 *Multithreaded* 구조를 따른다 — **메인 스레드는 spin, 작업 스레드가 `movej` 같은 블로킹 호출**. 한 가지를 더 조인다: `DSR_ROBOT2` 는 모든 호출에서 `rclpy.spin_until_future_complete(g_node, …)` 로 **`DR_init` 노드를 직접 spin** 하므로(`dsr_common2/imp/DSR_ROBOT2.py:635` 외 전부), 우리가 executor 로 spin 하는 노드는 **`DR_init` 노드와 별개의 노드**(`skill_node`, ns `cell`)여야 한다. `DR_init` 노드(ns `dsr01`)는 executor 에 넣지 않고 **워커 스레드 한 곳에서만 직렬 호출**한다. Action 콜백은 큐에 넣고 기다린다. 로봇을 만지는 노드는 `skill_node` 하나뿐. 스레드 매뉴얼: https://v2-manual.scroll.site/ko/v2-programming-manual/2.12.1/publish/thread |
| D-03 | 공정 오케스트레이션 | **`core/process_fsm.py` 순수 Python 상태기계.** `py_trees`(apt 에 2.5.0 있음)는 쓰지 않는다 — 5일 예산에 새 프레임워크를 더하지 않고, 상태 전이표가 그대로 SDD 5장이 된다. `PROJECT_RULES.md` R9 의 "py_trees_ros 채택" 은 이 결정으로 **대체** |
| D-04 | 그리퍼 제어 경로 | **기본 `modbus` 백엔드** — 기존 드라이버 `OnRobotRGControllerServer`(`~/ws_dsr`, 네임스페이스 `dsr01`)의 `/onrobot/sendCommand` 로 폭(1/10 mm 정수 문자열)을 보낸다. **폴백 `dio` 백엔드** — 9/16 교육의 `grip_test.py` 방식(`set_digital_output(1/2)`). 백엔드는 파라미터 `gripper.backend` 하나로 바꾼다. 가상 모드는 `gripper_virtual_node` 가 같은 서비스 이름을 받는다 |
| D-05 | **파지 판정** | **폭 추론** — 닫기 명령 후 정지 폭이 `목표 폭 + grip_margin_mm` 보다 크면 잡은 것, 목표 폭까지 닫혔으면 놓친 것. 드라이버는 Modbus 로 `grip`·`s1_t`·`s2_t` 비트를 **읽지만 토픽으로 내지 않는다** (`comModbusTcp.getStatus()` 는 dict 로 반환, 서버는 `JointState` 만 발행). 비트를 쓰려면 드라이버 패치가 필요 — I-003 선택 항목. `PROJECT_RULES.md` 3-3 의 "`gsta` bit1 직접 통보" 는 **정정** |
| D-06 | 파지력 설정 | 드라이버 `sendCommand` 는 수치 파지력을 받지 않고 `'i'`/`'d'` 로 **2.5 N 씩** 올리고 내린다(기동 시 40 N). 어댑터가 명령 파지력을 추적해 스텝 수를 계산한다. 정확도가 부족하면 I-003 패치로 해결 |
| D-07 | **무게 측정** | **9/19 확정: `get_tool_force(DR_BASE)` Fz (tool_force)**. `get_workpiece_weight` 는 탈락 — 같은 JTS 를 컨트롤러가 환산한 값이라 정확도 이점이 없고(매뉴얼 "외력으로부터 측정"), `reset_workpiece_weight` 가 success 를 돌려줘도 빈 그리퍼가 869 g 으로 남아 세션마다 다른 잔류 오차가 값을 지배했다 (32 g→1001, 132 g→723: 무게를 올리면 값이 내려감). 호출도 0.7 s. 근거 `gmp_dosing/config/scale_reference.yaml`. 아래는 확정 전 원안 — **1순위 `get_workpiece_weight()`** (매뉴얼 5.1.1, 강사 지정 절) — 세션 시작 시 빈 그리퍼·계량 자세에서 `reset_workpiece_weight()` 로 잔류 오차를 지우고, 용기를 들어 계량 자세에서 `samples` 회 읽어 평균·표준편차. M0609 는 M 시리즈(JTS)라 사용 가능(Non-FTS A 모델 불가 주의는 해당 없음). `set_workpiece_weight` 는 **부르지 않는다** — 충돌 감지 무효화가 전제라 안전 원칙에 어긋난다. **폴백 `get_tool_force(DR_BASE)` Fz 평균** — 파라미터 `scale.method: workpiece \| tool_force`. 둘 다 JTS 에서 나오므로 **분해능은 9/17 오전 실측이 확정한다** (Q-01). 그램 환산·영점·보정은 `gmp_dosing/core/scale.py` 단일 출처. 가상 모드는 힘값이 없을 가능성이 커 `scale.simulated` 로 대체 (Q-07) |
| D-08 | 도징 단위 | **실측 분해능에 따라 결정** — 30 g 을 3σ 로 가를 수 있으면 30 g, 아니면 100 g 으로 올리고 허용 오차 ±5 %. 레시피 yaml 만 바뀌고 코드는 그대로다. **G1 결과 (9/18, tool_force, 스쿱+원료 총 133 g (빈 스쿱 32 g), 6세트×30회×10표본)**: 계량 한 번(회차 평균)의 σ = 6.0 g, **3σ = 18.0 g** → `scale.min_resolvable_g = 19`. **9/19 재측정 (32 g·132 g 각 3세트×30회, 표본 간격 0.82 s 로 독립)**: 3σ = **12.6 / 12.4 g**, 두 점 직선 **gain 0.886 · offset 247.1** (100 g 차이가 센서 113 g). σ 는 표본 수가 아니라 **간격**이 정한다 — skill_node 가 0.05 s 간격(중복)을 쓰는 동안은 19 유지, 간격을 늘리면 14. 30 g 자체는 3σ 밖이라 '있다/없다' 는 가르지만, **±5 % 허용 폭(30 g 이면 ±1.5 g, 100 g 이면 ±5 g)은 한 번 계량으로 못 가른다** → 판정 근거는 Q-11. 근거 데이터·재계산: `gmp_dosing/calibration/`, `core/calib.py`, `config/scale_reference.yaml` (PR #22) |
| D-09 | 스테이션 좌표 | `gmp_bringup/params/stations.yaml` 단일 출처. 9/17 티칭한 `posx` 를 적는다. 설계 좌표(판 820×650, 베이스 (−50, 320))는 `PROJECT_RULES.md` 3-1. **코드에 좌표를 적지 않는다** |
| D-10 | 툴·TCP | `set_tool("tool_weight")` · `set_tcp("GripperDA_v1")` — 실물 컨트롤러 등록명, 파라미터 `robot.tool_name`/`robot.tcp_name`. 실측 1.320 kg · CoG (1.960, −32.760, 19.140) 은 컨트롤러에 등록 완료 (R7). 현재 TCP 오프셋은 툴 좌표계 **+Z 208 mm** (`robot.tcp_offset_mm_deg`). 가상 모드도 `GripperDA_v1`을 이 오프셋으로 등록·선택한다. 실물·가상 모두 툴/TCP 설정 전 수동 모드로 전환하고 `finally`에서 자동 모드로 복귀한다. |
| D-11 | 워크스페이스 | `~/ws_cobot_pjt/ws_dsr` 언더레이(벤더, read-only) + 이 저장소 `ros2_ws` 오버레이. 벤더 패키지는 고치지 않는다 (I-003 패치도 포크 형태로) |
| D-12 | 네임스페이스·이름 | 우리 노드는 launch 가 `namespace:=cell` 을 붙인다 → `/cell/…`. 코드는 상대 이름. DSR 클라이언트 노드만 `dsr01`. 그리퍼 서비스 `/onrobot/sendCommand` 는 드라이버가 절대 이름으로 만든다 |
| D-13 | 협업·안전 | 사람 상주 없음 (R23). 개입은 **HMI 인터락 요청 → 로봇 안전 자세 → 사람 투입 → 재개** 와 **QA 원격 승인** 둘뿐. 물리 접촉은 두산 충돌 감지(PFL)가 막는다 — 임계값은 `safety.collision_sensitivity` |
| D-15 | 판 좌표계 | **권장:** 판 위 3점을 티칭해 `set_user_cart_coord` 로 사용자 좌표계를 만들고, `stations.yaml` 의 좌표를 그 좌표계(설계 좌표 820×650, `PROJECT_RULES.md` 3-1)로 적는다. 판이 밀려도 3점만 다시 찍으면 전 스테이션이 따라온다. 9/17 오전 티칭 시간이 부족하면 베이스 좌표 `posx` 로 시작하고 D2 에 전환 |
| D-16 | **HMI = 웹 (Flask)** | 강사 확인 "웹으로 해도 상관없다" → **웹 채택**. 근거: 「입출력 데이터 이해도」의 기능 요구가 *원격 QA 승인*(R23)이라 다른 기기에서 접속되는 UI 가 요구에 적합하고, 시연에서 "셀 밖에서 승인 누르는 장면"이 생긴다. Kn1 `mro_fleet` 의 Flask+rclpy 스레드 패턴 재사용. PyQt5(R8) 는 **폐기** — rclpy 이벤트 루프 충돌 위험까지 제거 |
| D-17 | **배치 기록 = SQLite** | GMP 배치 기록 의무·추적성·감사 추적을 파일(JSON)로는 못 채운다. `record_node` 가 **단일 기록자**, HMI 는 읽기만, `events` 는 append-only, `audit` 에 사람의 조작(actor·시각). JSON 은 배치 종료 시 내보내는 **사본**. 계약 7절 |
| D-18 | **용기 반송** (9/16 팀 합의) | 사람은 빈 **약통**을 매거진 슬롯에 넣어 두기만 하고(패스박스 반입), 로봇이 `PICK_CONTAINER` 에서 칭량 위치로 가져와 조제한 뒤 `FINISH` 에서 용기째 완료품 트레이(같은 슬롯 번호)로, DISCARD 면 폐기함으로 옮긴다. 배치마다 사람이 용기를 놓으면 무인 연속 운전이 아니다. 매거진은 **낱개 슬롯**(겹쳐 쌓기·디네스팅 금지 — 그리퍼로는 신뢰성이 없다), `stations.yaml` `slots`/`slot_pitch_mm`. 반송은 상태기계 요청 `carry` 하나이고 process_node 가 `MoveToStation`+`SetGripper` 으로 조합한다. 용기는 컵쌓기 세트가 아니라 **구매 약통** — 조건은 Q-08. 컵쌓기 컵은 최후 폴백 |
| D-19 | **팀·조장** (9/16) | 조장 고희태(A 스킬 겸임) · B 김민준 · C 김병직 · D 서동권. 부담당 짝 A↔C, B↔D |
| D-20 | **추가 기능** (9/16 강사 선택) | **채택: 1 폭 지문 식별(스쿱·약통 규격을 정지 폭으로 검증, 일탈 `WRONG_TOOL`) · 3 재기동 이어하기(DB 상태로 중단 지점 재개) · 5 원료 잔량 추정(누적 투입량 + 접촉 높이, 예방 보충 권고).** 7 사람 근접 협동은 방식 미정(Q-10). 2·4·6·8 보류. 담당·마감은 `docs/todo.md`. 계약 영향: `Deviation.kind` 에 `WRONG_TOOL` 추가 → **v1.2** (조장) |
| D-21 | **사람 접촉 반응 = "건드리면 정지, 다시 건드리면 재개"** (9/16, 추가 기능 7) | 강사 제안 `wait_nudge()` 는 DRL 전용이라 파이썬 API 에 없다 → **`get_tool_force` 폴링**으로 동등 구현. `skill_node` 워커가 **유휴 구간과 스킬의 대기 루프(계량 settle·붓기)** 에서 100 ms 마다 외력을 읽어 `safety.nudge_force_n`(8 N) 을 `nudge_window_s`(0.2 s) 넘으면 `CellEvent(NUDGE)`. process 는 `RUNNING→PAUSED(NUDGE)`, 다음 NUDGE 로 이전 상태 재개. **블로킹 `movel` 중에는 감지하지 않는다** (I-004 — 그동안은 두산 충돌 감지가 담당) → 시연은 인터락 중이거나 계량 대기 중에 건드린다. DSR 호출은 워커 스레드 한 곳 원칙(D-02) 유지 — 별도 감시 스레드를 만들지 않는다. **process 쪽 구현 완료 (9/18)** — `event` 구독 → 토글 → 루프 게이트. 정지는 **그 자리에 서는 것**이고 안전 자세로 가지 않는다 (들어오려면 인터락 ENTER 를 따로 누른다). **skill_node 쪽 감지는 아직** 이고 가상은 `scale.simulated` 라 NUDGE 를 내지 않으므로 실물 확인은 G1 과 함께 한다 |
| D-22 | **원료 1종 = 6단계, 스쿱을 든 채 계량** (9/17 C 제안·팀 합의) | 로봇이 저울이라(`get_workpiece_weight` 는 잡고 들어야 값이 나온다) **용기 계량은 그리퍼가 비어야** 한다 — 종전 SCOOP→POUR→WEIGH 는 스쿱을 든 채 용기를 잡는 모순이 있었다. 확정 흐름: `PICK_SCOOP → SCOOP_TARE(빈 스쿱) → SCOOP → WEIGH_SCOOP(붓기 전, 퍼낸 양 → 붓기 비율 = min(1, 부족량/퍼낸 양) — 1차 폐루프·초과 예방) → POUR → WEIGH_RESIDUAL(붓기 후, 잔량 → 투입량 += 퍼낸 양 − 잔량 → decide) → RETURN_SCOOP`. **스쿱에 남은 원료는 투입량이 아니다.** 정상 시도는 `WEIGH_RESIDUAL` 뒤 세 계량값·Scoop 결과·Pour 명령·6축 wrench를 `ScoopCycle` 1건으로 묶고, 중간 실패 시도도 실패 확정 시점에 미수집 값을 무효로 표시해 남긴다. 용기 계량은 원료가 다 끝난 뒤 `VERIFY` 한 번 — `|용기 순량 − Σ투입량| > min_resolvable_g` 면 `VERIFY_MISMATCH` → QA (2차 검증). 계약 영향: `weigh_scoop`(들고 있는 것 그대로 재기) Action 과 `Deviation.kind VERIFY_MISMATCH`·`BATCH_OUT_OF_SPEC` 는 후속 합의가 필요하다 (I-007). **VERIFY 는 두 가지를 본다** (9/17 조장 합의) — ① 제품 판정 `|net − Σtarget| > Σ(target×tol)` → `BATCH_OUT_OF_SPEC` ② 계측 신뢰성 `|net − Σ투입량| > min_resolvable_g` → `VERIFY_MISMATCH`. 원료가 전부 같은 방향으로 치우치면 net 과 Σ투입량이 함께 낮아 ②로는 안 잡히므로 ①이 규격 판정이다. 분해능(Q-01)이 스쿱 1회량(≈40 g) 을 못 가르면 스쿱 계량은 초과 예방용 대략치가 되고 판정 근거는 VERIFY 로 옮긴다 (Q-11) |
| D-23 | **회수·넛지 운영** (9/17 팀 합의) | 무균실을 가정한다. **패스박스·폐기함이 차서 출구를 막는 것은 QA 가 직접 치운다** — 로봇은 비우지 않는다. 완성품과 폐기물은 **패스박스로 회수**하며, 예상 주기는 1통 채울 때마다다. **한 세트가 끝나면 글러브박스로 로봇을 툭 건드려(NUDGE, D-21) 다음 세트를 이어간다** — 즉 이 셀은 사람이 붙어 있는 **반자동(semi-attended)** 운전이며 세트 경계의 대기는 예외가 아니라 설계다. **미정**: (a) 가득참 판단 — 비전이 없으므로 **카운트**밖에 없다, `passbox_done.capacity`·`reject_bin.capacity` 필요 (b) QA 가 비운 것을 시스템이 아는 방법 — HMI 확인 버튼(감사 추적에 누가 언제 회수했는지 남음)이 유력 (c) "한 세트"의 정의(배치 1건 / 레시피 1건 / 용기 N통). `docs/todo.md` 9/18 확정 항목. **(c) 구현 (9/19, PR #23 리뷰 반영)**: 세트 = **배치 1건**. 완성품(`passbox_done`)·폐기(`reject_bin`) 반송 뒤 FSM 이 `NUDGE_WAIT` 로 가 `nudge_wait`(D-24) 에서 서고, NUDGE 가 와야 배치가 끝나고 다음 주문을 받는다. 대기 중 mode 는 PAUSED, 이벤트 `SET_DONE`/`SET_NEXT` |
| D-24 | **작업공간 배치 확정** (9/18) | 판 **450 × 450 mm**, 볼트 격자 가로 15/15/10/5 · 세로 15/15/15. **기준 원점 = [X:0, Y:45] 고정 홀**(좌하단) — 판 위 물리 치수를 재는 기준점이다. **로봇 좌표는 BASE 기준으로 간다 (9/18 확정)** — D-15 의 판 좌표계(`set_user_cart_coord`)는 쓰지 않는다. `stations.yaml` 의 `frame: base` 를 유지하고 `posx` 를 그대로 티칭한다. 로봇(M0609, 반경 900 mm · 가반 6 kg)은 판 **좌측 28 cm 이격**(중심 기준), 도달 커버리지 작업영역 100 %. **판 중앙 300 × 300 mm 는 「로봇 작업 구역」으로 배치 불가(Keep Clear)** — 관절 회전·조제 간섭 방지. 물건을 두지 않는다. 판 위: 하단 중앙 **작업 계량 구역**(`workbench` — 9/18 `scale` 에서 개명. 로봇이 저울이라(D-22) 그 자리에 저울이란 물건이 없고, `common.yaml` 의 `scale.*`(힘→그램 환산 설정)·코드의 `WeightModel` 과 이름이 겹쳐 셋을 구분하기 어려웠다), 우상단 **넛지 대기 위치**(D-23 세트 경계에서 사람이 건드리는 자리 — `safe` 와 별개), 우측 중앙 **Pass Box**(칸 2개: 완성품 · 빈통), 우하단 **폐기 위치**(`reject_bin`). 판 **바깥 아래**에 원료 A·B·C 구역과 **전용 스쿱**(각 원료통 아래, D-22). **넛지 대기 위치는 새 스테이션**이다 — 세트 완료 후 로봇이 여기로 이동해 사람이 건드리기를 기다린다 (ID `nudge_wait`, 9/18 확정). `material_4`(예비)는 그림에 없으므로 담당자가 `stations.yaml` 에서 제거한다. Pass Box 매핑은 **Q-12 에서 해소** — `passbox_empty`(빈통)·`passbox_done`(완성품) |
| D-25 | **스쿱 거치대 인출** (9/19) | `PICK_SCOOP` 파지 성공 후 첫 `WeighHeld` 시작에 현재 BASE pose에서 **+Y 150 mm**를 먼저 이동해 스쿱을 거치대에서 뺀다. 거리는 `common.yaml`의 `gripper.scoop_extract_y_mm`가 단일 출처다. `SetGripper`는 파지 서비스 책임만 유지하고 인출 이동은 로봇 스킬 워커가 수행한다. 실물 궤적·간섭 검증 전까지 로봇 구동은 보류한다 |
| D-14 | 일정 | 실물 5일 9/17·18·21·22·23. **9/23 이 실물 마지노선.** 9/24~28 휴강(영상·PPT·문서), 9/29 시연, 9/30 발표 (R11) |

## 스테이션 간 관절 이송 (9/19 승인, 티칭 대기)

- `workbench → passbox_done`(약통)은 `직선 이탈 → amovej → 목적지 ABOVE → 요청이 AT일 때 직선 접근`이다.
- `passbox_done → nudge_wait`(빈 그리퍼)는 **놓기 후 ABOVE 직선 후퇴 완료 상태**에서만 시작한다.
  `ABOVE → EXIT 직선 이탈 → amovej로 nudge_wait AT 직접 도착`이며 최종 하강은 없다.
  내부 경로 설정은 `start_from: above`, `arrival: at`이고 MoveToStation 요청은 `approach: 1`이다.
- `stations.yaml: transfers`가 출발·도착 조합과 파지 조건, 티칭 관절각의 단일 출처다.
  두 경로는 아직 **비활성**이다. **실물에서는** 해당 이동을 거부하며 직선 이동으로 우회하지 않는다. **가상 모드는** 활성 여부와 무관하게 기존 직선 이동을 사용하고 보호 목적지 제한도 적용하지 않는다.
  `common.yaml`의 관절 이송 속도·가속도도 0(미확정)으로 두었다.
- 높이 확정: workbench **파지점** `[423,93,100,90,-90,-90]`에서 ABOVE +100/EXIT +200 mm,
  passbox_empty·passbox_done·reject_bin은 AT Z=100에서 ABOVE +50/EXIT +150 mm.
  BASE Z 상대 높이로 계산한다. workbench.posx는 파지 AT로 통합하며 용기 계량은 ABOVE다. 스쿱·원료 접근 높이는 유지한다.
  workbench 출발 AT/ABOVE/EXIT와 passbox_done ABOVE/EXIT 관절각은 반영했다.
  passbox_done AT와 nudge_wait ABOVE 관절각은 **불필요**하다. 제공된 nudge_wait AT 관절각을
  경로 종점으로 사용한다. 넛지 경로에 필요한 티칭값은 모두 입력됐으며 사용자 검증은 남아 있다.
- 출발 AT/ABOVE 관절각·현재 위치 이력·파지 성공 이력과 최신 피드백을 확인한다.
  취소/실패 시 이력을 무효화한다. 스쿱 인출·계량·붓기는 이 변경으로 관절 이동으로 바꾸지 않는다.
- 관절 이송의 통신 계약은 그대로다. 아래 원료 반환 기능은 계약 v1.3 초안에 반영했다. 다른 출발지에서 보호 대상 목적지로 가는 경로도 따로 티칭·등록해야 한다.
- 개발 검증은 **단위 테스트까지만** 수행한다. 가상·실물 검증은 사용자가 담당한다.
  조장·A 담당 티칭과 공정 후속 인계 목록은 `docs/setup.md`의 「관절 이송 티칭·인계」를 따른다.

## 스쿱 계량·초과 반환 (9/19 사용자 승인, 계약 팀 공유 완료)

- `workbench.posx=[423,93,100,90,-90,-90]`는 용기 파지 AT다. `pick_posx`는 제거한다. 용기 계량은 BASE Z +100 mm ABOVE에서 수행한다.
- 스쿱 빈 무게·붓기 전·붓기 후는 해당 원료의 `material_N.posx`에서 잰다. `measure_posx`는 원료 재고 접촉 자세로서 스쿱 계량에 사용하지 않는다.
- 허용되는 스쿱은 `workbench.pour_start_posx → pour_end_posx`로 이동하며 전량 붓고 시작 자세로 복귀한다. 각도만 더하는 부분 붓기는 사용하지 않는다.
- 순 스쿱량이 `max(0, 목표−누적 투입량) + 목표×허용 오차율/100`보다 크면 `ReturnMaterial`로 동일 원료통에 반환하고 시도 한도 내에서 재스쿱한다. 반환 실패 시 공정을 진행하지 않는다.
- 반환은 약통 투입량에 합산하지 않으며 `ScoopCycle`의 `RETURNED=5`/`RETURN_FAILED=6`, `delivered_g=0`, `valid=false`로 남긴다. 기존 빈 스쿱 tare를 유지하므로 반환 후 잔량도 다음 스쿱 계량에 포함된다.
- **필수 추가 티칭:** 원료 A/B/C 각각 `return_start_posx`와 `return_end_posx`. 현재 null이며 두 좌표가 모두 유효하지 않으면 이동 전 거부한다. 반환 경로·원료 낙하·간섭은 사용자 검증 대상이다.
- 계약 v1.3은 팀 공유를 확인하고 구현했으며 영향 담당 최소 2명 승인 전까지 초안이다. 코드 검증은 단위 테스트까지만 수행한다.

## 계량 표본 간격 (9/20 사용자 승인)

- `common.yaml: scale.period_s=0.82`를 MeasureForce·WeighHeld·WeighContainer의 실제 계량 호출에 적용한다. 기동과 계량 요청 시 유한한 양수인지 검사한다.
- 값은 **표본 읽기 시작 간격의 하한**이다. 센서 호출이 길면 실제 간격도 늘어난다. 0.82초는 9/19 독립 표본 실측에 근거한 시험값이며 동일 성능을 보장하는 값은 아니다.
- 표본 사이 대기는 같은 DSR 워커에서 0.1초 간격으로 외력을 관측한다. 넛지 관측값은 계량 평균·표준편차 표본에 합치지 않는다. 블로킹 장치 호출 동안의 관측 지연은 남는다.
- 20표본이면 표본 시작 구간만 약 15.58초이며 정착·장치 호출 시간이 추가된다. 마지막 표본 뒤 불필요한 간격 대기는 생략한다.
- 개발 검증: 서브에이전트 교차검토 및 관련 단위 테스트 60건 통과. 가상·실물 검증은 미수행이다.
- `min_resolvable_g=19`, gain·offset·표본 수는 유지한다. 새 설정으로 실물 재측정 후 B와 분해능 14 g 적용 여부를 판단한다. B의 `scale_reference.yaml`은 과거 측정 근거로 유지하며 후속 갱신을 인계한다.

## 확정 노드·토픽

**`docs/interfaces.md` 계약 v1.2 (9/18 확정 — `WeighHeld` Action, `Deviation.kind` 3종 추가로 I-007 해소)** 과 `ros2_ws/src/gmp_interfaces`가 변경 원본이다. `RecipeItem.grade/scoop_id`, `Pour.target_station`, `WeighContainer.container_station`, `QaDecision.batch_id` 삭제, `SetGripper`, `ScoopCycle`은 1차 승인 상태이며 최소 2명 승인 전에는 확정하지 않는다.
계약을 바꿀 때는 **팀 채널에 먼저 알리고 `gmp_interfaces` 와 문서를 같은 커밋에서** 고친다. 리뷰는 영향받는 담당 전원 (AGENTS 교차검수 표).

| 노드 | 패키지 | 책임 |
|---|---|---|
| `skill_node` | `gmp_skills` | 로봇 스킬 서버 — `MoveToStation`·`Scoop`·`Pour`·`ReturnMaterial`·`WeighContainer`·`WeighHeld` Action, `SetGripper`·`MeasureForce`·`SafePose` Service, `gripper_state` 발행. DSR 워커 스레드 소유 |
| `process_node` | `gmp_process` | 레시피 실행 상태기계. `RunBatch` Action 서버, `SubmitOrder`·`QaDecision`·`InterlockRequest` Service 서버, `state`·`weight`·`scoop_cycle`·`dispense_result`·`deviation` 발행 |
| `record_node` | `gmp_hmi` | **단일 기록자.** `state`·`weight`·`scoop_cycle`·`dispense_result`·`deviation`·`event` → SQLite (계약 7절, `scoop_cycle`은 v1.2 적용 필요). 배치 종료 시 JSON 내보내기 |
| `hmi_web_node` | `gmp_hmi` | Flask 웹 HMI (:5000). 주문 제출, 상태·계량 그래프, **QA 승인/폐기(원격)**, 인터락, 이력·KPI·감사 추적 조회(DB 읽기) |

## 미결

| ID | 질문 | 결정권자 | 시점 |
|---|---|---|---|
| Q-01 | **외력 분해능** — 정지 상태 Fz 표준편차가 몇 N 인가. 30 g(0.3 N) 을 가를 수 있는가 | A + B | **9/17 오전 (게이트)** |
| Q-02 | 그리퍼 백엔드 — `modbus` 가 실물에서 폭·힘 모두 되는가, 안 되면 `dio` | A | 9/17 |
| Q-03 | `dio` 백엔드의 DI 핀 — 파지 완료·busy 가 어느 핀으로 오는가 (ws README "디지털 입력 핀 감지") | A | 9/17 |
| ~~Q-04~~ | ~~스쿱 실물 치수~~ → **9/19 해소: A/B/C 손잡이 폭 15.5/18/28 mm.** 원료별 값은 `stations.yaml`·`common.yaml`에 두며, `SetGripper.final_width_mm` 정밀도 실측은 별도 TODO로 유지 | C(하드웨어) | 해소 |
| Q-05 | 판 위 기존 고정물 (케이블·지그) — 스테이션 좌표 충돌 여부 | 팀 | 9/17 |
| ~~Q-06~~ | ~~주제 사전 승인~~ → **9/16 승인 (R26)** | — | 해소 |
| Q-07 | 가상 모드에서 `get_tool_force` 가 값을 주는가 (에뮬레이터 힘 미지원 가능성) — 안 주면 가상은 `scale.simulated=true` 로 | A | 9/16 밤 |
| Q-08 | **약통 선정** — (a) 입구 안지름 ≥ 50 mm (스쿱으로 붓기), (b) 파지부 외경 ≤ 90 mm (RG2 개방 100), (c) **파지점 위로 돌출 ≤ 40 mm** (안전 스위치 — 입구 근처를 잡거나 낮은 통), (d) 빈 무게 ≤ 50 g, (e) 바닥이 평평해 슬롯에 안정. 크림통·연고통형(Ø60~80, 높이 40~60)이 유리 | C(하드웨어) | 구매 시 |
| Q-09 | **강사 언급 "50 g" 의 의미** (R28) — `get_workpiece_weight` 분해능인지 최소 측정 무게인지. 어느 쪽이든 도징 단위는 100 g 이상이 안전 → 레시피 목표 100/150/200 g 안 | 조장(재확인) + A·B(G1) | 9/17 오전 |
| Q-11 | **D-22 판정 근거** — G1 σ 가 스쿱 1회량(≈40 g) 을 3σ 로 가르면 스쿱 계량(WEIGH_RESIDUAL)으로 원료별 판정, 못 가르면 스쿱 계량은 붓기 비율용으로만 쓰고 판정은 VERIFY(용기, 누적 200/150/100 g) 로. VERIFY 허용 차 `min_resolvable_g` 도 같이 확정. **제약 하나가 드러났다 (9/18 구현 중)** — VERIFY ②(계측 신뢰성, 임계 `min_resolvable_g`)가 ①(제품 규격, 임계 `Σ(target×tol)`) 없이 울리려면 **`min_resolvable_g < Σ(target×tol)`** 여야 한다. 현재 값이면 `30 > 22.5`(데모 레시피 450 g, 전부 ±5 %)라 **②는 죽은 검사**다. G1 σ 가 30 g 보다 충분히 작게 나와야 ②가 의미를 갖는다. **G1 결과 (9/18)**: 3σ = 18.0 → `min_resolvable_g = 19 < 22.5` 라 **② 는 살아난다**. 반면 스쿱 1회량 40 g 은 3σ 로 가르지만(40 > 18) 원료별 허용 폭 ±5 g 는 못 가른다 → **확정 (9/19 조장 "제안 기준 타당")**: 원료별 `WEIGH_RESIDUAL` 판정은 초과 예방·붓기 비율용으로만 쓰고, 합격 판정은 VERIFY ①(용기 순량 vs Σtarget, 허용 Σ(target×tol) = 22.5 g > 18 g) 로 옮긴다. 코드 변경 없이 레시피 `tol_pct` 와 FSM 판정 위치만 정하면 된다. 표본 간격을 센서 갱신 주기에 맞춰 재측정하면 σ 가 내려갈 여지가 있다(표본 43 % 중복) — 그 뒤 재검토 | B→**C 임시**(9/18 회의) + 조장 | 9/17 → **9/19 확정** (tool_force · gain 0.886 · offset 247.1 · min_resolvable_g 19, 표본 간격 조정 후 14) |
| ~~Q-12~~ | ~~Pass Box 와 `magazine`·`output_tray` 의 관계~~ → **해소 (9/18)**: Pass Box 두 칸이 대체한다. `magazine` → `passbox_empty`, `output_tray` → `passbox_done`. 판 위에 별도 매거진·트레이는 없다 | C + 조장 | ✅ |
| ~~Q-10~~ | ~~추가 기능 7 의 방식~~ → **D-21 로 확정 (건드리면 정지·다시 건드리면 재개)** — `wait_nudge()` 는 DRL 전용, 파이썬 API 에 없음. 동등 구현: `get_tool_force` 폴링으로 순간 외력(예: 8 N 이상 0.2 s) 감지. 반응 (a) 건드리면 일시정지·다시 건드리면 재개 (b) 부딪히면 안전 자세 후퇴 → 인터락 EXIT 로 재개 (c) 둘 다 + 속도 제한 | 팀 | 9/17 |

## 교육 요구 명령어 ↔ 사용처 (평가 「기능 구현 완전성」 근거)

강사가 지정한 DRL 매뉴얼 4개 절(3 모션 · 4 제어 보조 · 5 기타 설정 · 6 힘/순응·사용자 편의)의 명령을 **어느 스킬이 왜 쓰는지**. `DSR_ROBOT2` 노출 여부는 9/16 확인.

| 절 | 명령 | 사용처 (`gmp_skills`) | 비고 |
|---|---|---|---|
| 3.1 위치 | `posj`, `posx`, `trans` | 전 스킬 — `stations.yaml` → 접근점 `trans(pos, [0,0,+approach_mm,0,0,0])` | |
| 3.2 설정 | `set_velj/accj`, `set_velx/accx`, `set_tcp`, `set_ref_coord` | 기동 시 1회 + 스킬별 속도 스케일 | `set_ref_coord(DR_BASE)` 명시 — 힘제어 방향의 기준 |
| 3.3 동기 | `movej`(홈·안전 자세), `movel`(접근·작업점·티칭된 붓기 시작→종료) | MoveToStation · Pour | |
| 3.4 비동기 | `amovel` + `check_motion`/`mwait` | 취소 가능한 이동 (인터락·SafePose) | 블로킹 `movel` 은 중간 취소가 안 된다 — I-004 |
| 3.3/3.4 | `move_periodic`/`amove_periodic` | 교육 명령 대응 후보. 9/19 전량 붓기로 변경해 Pour 털어내기는 제거했으며, Scoop 평탄화는 미구현 | 두산 고유 명령 — 차별점 |
| 4.1 현재값 | `get_current_posx/posj`, `get_tool_force`, `get_external_torque` | 상태 발행 · 계량 폴백 · 미끄러짐/충돌 관측 · **사람 접촉(nudge) 감지 (D-21)** | `wait_nudge` 는 DRL 전용 → `get_tool_force` 폴링으로 대체 |
| 4.4 안전 설정 | `get_collision_sensitivity`, `get_current_tool`, `get_current_tcp` | 기동 자가진단 — 툴·TCP·감도가 기대값인지 확인하고 아니면 기동 거부 | 「동작 및 운용 안정성」 |
| 5.1 툴/작업물 | `set_tool`, **`reset_workpiece_weight`, `get_workpiece_weight`** | workpiece는 G1 탈락, 기본 계량은 tool_force (D-07) | `set_workpiece_weight` 는 안 쓴다 |
| 5.2 제어 모드 | `set_singularity_handling` | 선택 — 계량 자세 근처 특이점 회피 | |
| 6.1 힘/순응 | `task_compliance_ctrl`, `set_stiffnessx`, `set_desired_force`, `release_force`, `release_compliance_ctrl` | **Scoop 담그기** — Z 방향 힘제어(`dir=[0,0,1,0,0,0]`, `mod=DR_FC_MOD_REL`)로 원료면까지 내려가 접촉 | 진입/해제 짝 필수 (AGENTS 코드 규칙) |
| 6.2 편의 | `check_force_condition(DR_AXIS_Z, max=…)`, `check_position_condition` | 접촉 감지 · 담금 깊이 상한 · 충돌 감지 | `set_external_force_reset` 은 DSR_ROBOT2 에 없다 → 영점은 `reset_workpiece_weight` |
| 6.2 편의 | `set_user_cart_coord`, `coord_transform` | 판 좌표계 (D-15) | |
| 7.1 IO | `set_digital_output`, `get_digital_input` | 그리퍼 `dio` 백엔드 (D-04) | `wait_digital_input` 은 노출 안 됨 → 폴링 |

> 이 표는 발표 자료의 「교육 내용 ↔ 구현」 슬라이드로 그대로 쓴다. 비어 있는 절이 없도록 설계했다.

## 스킬 자체 종료 및 HMI 안전 복구 (9/20 작업)

- 검증: 9/20 서브에이전트 종료·복구 경합 검토 후 gmp_skills 단위 테스트 198건, Python 문법 검사, gmp_interfaces·gmp_skills colcon 빌드 및 생성된 RecoverSafety 필드 확인 통과. 가상/실물 종료·복구 재현은 미수행.
- 기동 자가진단 완료 전에는 일반 스킬과 SafePose 이동 요청을 거부한다. 자가진단은 비동기 작업이므로 완료 전에도 메인이 종료 신호를 처리한다.
- PR #30의 process 종료 SafePose 요청은 유지한다. A는 자체 종료 시 신규·대기 작업을 차단하고 현재 작업에 취소를 전달한다. ROS 문맥과 DSR 노드는 워커의 정지·힘제어 해제 시도 뒤에 정리한다.
- SIGINT/SIGTERM은 종료 요청으로 받고, DSR 호출은 기존 워커 한 곳에서만 수행한다. `robot.shutdown_timeout_s`는 종료 대기 한도이며, 초과/해제 실패를 성공으로 보고하지 않고 프로세스 종료 코드 1로 남긴다. 워커·콜백이 살아 있으면 사용 중인 노드를 파괴하지 않는다. 장치 호출이 응답하지 않으면 강제 종료까지 해제를 보장하지 못한다.
- 스킬 내부 직선 이동도 비동기 이동·취소 감시를 사용한다. 종료·취소·실패 시 Scoop 복귀 및 용기 내려놓기·그리퍼 열기를 자동으로 이어가지 않는다.
- 사용자 전달 팀 합의: 안전 정지 복구 요청은 HMI→C→A로 보낸다. 로봇 복구와 배치 재개는 별개이며 NUDGE/인터락 EXIT가 안전 정지 차단을 해제하지 않는다.
- 현재 Python 래퍼는 `set_robot_control`을 노출하지 않지만 벤더 서비스는 존재한다. 9/20 사용자가 A 내부 단일 워커 직접 호출의 D-01 예외를 승인했다. 벤더의 성공 응답은 실제 전이를 보장하지 않으므로 복구 후 상태 확인이 필수다.
- C/D 인계: 새 복구 요청 계약·인증된 작업자/요청 ID·중복 요청 처리, ERROR 주문 차단, 안전 정지의 FORCE_LIMIT 재시도 제외, 복구 안내·감사 기록. C/D 코드는 각 담당이 구현한다. 계약 v1.4 초안의 `RecoverSafety`와 이벤트를 구현하며 C/D 연결은 인계 상태다. 상태별 복구·응답 의미는 `docs/interfaces.md` 8절을 따른다.

- A 복구는 상태별 control 2/3/4/5/7만 허용한다. 9/10→8은 수동 조치 필요로 반환하고, 조치 후 새 요청에서 8→1을 확인한다. 무동력동작·자동 프로그램 재개는 없다.
- 감지 후 A 차단은 명시적 복구 요청으로만 해제한다. 실제 상태 조회 실패/새 알람/종료 경합은 실패로 남기며 벤더 success=true만 신뢰하지 않는다. 위치·파지 이력은 복구 후에도 폐기한다.
