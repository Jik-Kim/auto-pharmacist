# gmp_process — 공정 오케스트레이션 [C 공정]

`core/process_fsm.py` 가 상태와 전이를 갖고, `nodes/process_node.py` 는 그 결정을 스킬 Action/Service 호출로 옮긴다.
전이표는 [docs/architecture.md](../../../docs/architecture.md) 「공정 사이클 ↔ 노드」와 **같은 것**이어야 한다 (SDD 5장도).

## 원칙

- 도징 판정은 `gmp_dosing.core.dosing.decide()` 가 한다. 여기서는 판정하지 않는다.
- 일탈은 `core/deviation.py` 의 규칙표로 처리한다 — kind 별로 「재시도 / 보충 요청(인터락) / QA 판정 / 강제 개입」 중 하나.
- 인터락 `ENTER` 는 스킬 `SafePose` 가 **성공한 뒤**에야 `granted`. 로봇이 아직 움직이는데 사람을 들이지 않는다.
- 스킬 호출은 **한 번에 하나**. 이전 Action 결과가 오기 전에 다음을 보내지 않는다 (skill_node 워커도 직렬이지만, 여기서도 지킨다).

## RunBatch 서버

`process_node` 하나가 `/cell/run_batch` ActionServer와 기존 `/cell/submit_order`를 함께 제공한다.
별도 RunBatch 노드를 띄우지 않는다. 두 입력은 같은 예약·실행 슬롯과 기존 `ProcessFSM`을 공유한다.
`gmp_interfaces/action/RunBatch.action`의 메시지 정의는 변경하지 않았다.

- Goal: 기존 레시피 검증·스테이션 매핑 확인. 실행 중·수락 예약 중·정지 중·종료 미확인 시 거부.
  빈 batch_id는 ROS clock의 UTC 날짜로 B-YYYYMMDD-NNN 형식을 생성하며, 같은 프로세스 세션에서 사용한 batch_id의 재주문은 거부한다.
  ID 중복 검사는 재시작을 넘겨 영속화하지 않는다. HMI는 매 주문 새 ID를 생성해야 한다.
- Feedback: 기존 2 Hz/전이 상태와 현재 배치의 마지막 DispenseResult. 첫 결과 전에는 기본 빈 메시지.
- Result: DONE만 success=true. DISCARDED는 처리 완료(action SUCCEEDED), success=false.
  사용자 취소는 ABORTED(action CANCELED), 안전정지·실행 실패는 ERROR(action ABORTED).
  uint8 완료 원료·일탈 수는 255로 제한한다. 레시피 원료 수가 255를 넘으면 수락하지 않는다.
- Cancel: 해당 RunBatch Goal만 취소. 다음 스킬을 막고, 진행 중 A Action에 취소를 전달한 뒤
  **최종 스킬 응답까지 기다린다**. Service에는 취소 계약이 없으므로 응답 후 다음 동작을 막는다.
  A가 취소를 거부해도 해당 스킬 종료 뒤 배치를 중단한다. 즉시 물리 정지를 보장하는 버튼이 아니다.
  취소 시 임의 SafePose 이동·원료 폐기·배치 재개를 하지 않는다. 셀 정리는 현장 확인 대상이다.
- 스킬 Goal/Result 또는 Service 응답 유실: ERROR 및 새 주문 차단. 늦게 수락된 Goal에도 취소 전달.
  **재기동 자체가 로봇을 멈추는 수단은 아니다.** A에서 이전 작업 종료와 현장 상태를 확인한 뒤 C를 재기동한다.
- 안전정지: 배치별 중단을 유지한다. 즉시 복구 성공 이벤트가 와도 진행 배치는 ERROR로 끝난다.
- 원료 재고·보충·회수 카운터·실제 재기동 이어하기는 이 구현 범위에 포함하지 않는다.
- HMI와 DB의 기존 계약 유지: 취소의 RunBatch Result는 ABORTED, CellState는 mode=ERROR/step=ABORTED이며
  현재 record_node의 배치 종료 결과는 ERROR로 저장된다. UI·비밀번호·레시피 파일은 변경하지 않는다.

### 로봇 없이 자동 ROS 검증

다른 시험과 동시에 실행하지 않는다. `/runbatch_test_<pid>`에 **실제 C + 가짜 A**를 기동하고
각 사례 뒤 종료한다. 운영 `/cell`이나 로봇 드라이버를 호출하지 않는다.

```bash
# 복제 위치가 다르면 본인의 저장소 경로를 사용한다.
cd ~/auto-pharmacist
source /opt/ros/jazzy/setup.bash
source ros2_ws/install/setup.bash
export ROS_DOMAIN_ID=88
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
python3 ros2_ws/src/gmp_process/tools/verify_run_batch_ros.py
```

자동 검사 6개: 정상 Goal/Feedback/Result, 잘못된 레시피 및 중복 Action/Service 주문,
진행 스킬 종료를 기다리는 취소, NUDGE_WAIT 취소, QA 폐기, 안전정지 ERROR/복구 후 자동 재개 금지.
이것은 C↔가짜 A DDS 검사다. HMI 브라우저부터 실물까지의 통합 검증과 구분한다.

### 운영 연결

A와 동일하게 DOMAIN 70·Fast DDS·`/cell`을 맞춘다. 기존 C process_node를 먼저 종료한다.
A 담당 PC 또는 공정 담당 PC에서 C를 **한 개만** 실행한다. 기존 `cell.launch.py`에 C가 포함되어
있으므로 그것을 실행했다면 아래 명령을 추가 실행하지 않는다. HMI+record_node만 켠 상태라면:

```bash
# 복제 위치가 다르면 본인의 저장소 경로를 사용한다.
cd ~/auto-pharmacist
source /opt/ros/jazzy/setup.bash
source ros2_ws/install/setup.bash
export ROS_DOMAIN_ID=70
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
ros2 run gmp_process process_node --ros-args \
  -r __ns:=/cell \
  --params-file "$PWD/ros2_ws/src/gmp_bringup/params/common.yaml" \
  -p stations_file:="$PWD/ros2_ws/src/gmp_bringup/params/stations.yaml"
```

다른 동일 환경 터미널에서 `ros2 action info /cell/run_batch`로 서버 1개를 확인한다.
HMI 5000의 기존 RunBatch 클라이언트가 연결된다. A 스킬 서버가 준비되기 전에는 주문하지 않는다.
실물 첫 주문·취소는 A/C 담당과 현재 TCP·티칭·원료·용기 상태를 확인하고 진행한다.

### PR #163 리뷰 확인 사항

- NUDGE_WAIT 주문 거부는 세트 완료 및 다음 넛지 안내를 유지한다. 유휴 NUDGE/ENTER 잠금 사유도 CellState.note에 표시한다.
- 스킬 시간 초과 후 `_execution_uncertain`은 유지한다. `cancel_late`는 취소를 요청할 뿐 물리 정지나 최종 결과를 확인하지 않으므로 차단 해제 근거로 쓰지 않는다.
- `skill_timeout_s`는 A와 실물 계량·이동 최장 시간을 확인한 뒤 정한다. 이 PR은 기존 90초를 임의로 늘리지 않는다.
- 자동 배치 ID는 날짜형으로 복원한다. 날짜는 기록과 동일한 ROS clock을 UTC로 변환하며, 재시작 간 중복 방지는 보장하지 않는다. HMI가 전달하는 배치 ID는 유지한다.
- RunBatch 실행 콜백이 대기하는 동안 다른 콜백을 처리할 수 있도록 운영 executor는 6개 스레드를 사용한다.
- DDS 테스트 종료 시 보고된 Destroyable 예외는 아직 재현·해결 확인 전이며 기능 검사 통과와 구분한다.
