# 개발 환경과 실행 절차

## 준비 (PC 마다 한 번)

- Ubuntu 24.04, ROS 2 Jazzy, Python 3.12
- **`sudo apt install python3-flask python3-yaml`** — 웹 HMI (9/16 확인: 시스템 파이썬·rokey_venv 모두 flask 없음. venv 는 rclpy 가 안 보이므로 apt 로)
- **언더레이** `~/ws_cobot_pjt/ws_dsr` — doosan-robot2 · onrobot-ros2 · m0609_rg2_bringup · rokey. 9/16 실기로 real 연동 확인. 빌드법은 그쪽 `src/README.md`
  - DRCF 에뮬레이터(docker) 설치되어 있어야 virtual 모드의 motion 서비스가 뜬다 (`install_emulator.sh`)
  - real: 로봇 `192.168.1.100`, 그리퍼 컴퓨트박스 `192.168.1.1`, UDP 포트 권한(`ip_unprivileged_port_start=0`)
- 이 저장소: `~/auto-pharmacist` (오버레이)

```bash
source /opt/ros/jazzy/setup.bash
source ~/ws_cobot_pjt/ws_dsr/install/setup.bash
cd ~/auto-pharmacist/ros2_ws && colcon build --symlink-install && source install/setup.bash
```

`--symlink-install` 이라 파이썬·yaml 수정은 리빌드 없이 반영된다. `gmp_interfaces` 를 고치면 리빌드.

## 터미널마다 맨 처음

```bash
source ~/auto-pharmacist/tools/env.sh      # ROS_DOMAIN_ID=70 설정 + /opt/ros → ws_dsr → auto-pharmacist 순서로 source
```

`ROS_DOMAIN_ID` 는 **70** 으로 조 전원 동일해야 한다. `.bashrc` 에 다른 값이 있으면 env.sh 가 덮어쓴다.

HMI 첫 기동 때 관리자 계정이 없으면 `GMP_HMI_ADMIN_USER`·`GMP_HMI_ADMIN_PASSWORD` 환경변수로 만든다 — 없으면 로그인할 계정이 없어 HMI 를 쓸 수 없다. 입력 절차와 격리 시험용 `ROS_DOMAIN_ID=88` 은 [demo_run_procedure.md](demo_run_procedure.md) 2절과 `ros2_ws/src/gmp_hmi/README.md` 를 따른다.

## 실행

| 모드 | 명령 | 되는 것 / 안 되는 것 |
|---|---|---|
| virtual | `ros2 launch gmp_bringup cell.launch.py mode:=virtual` | 이동·시퀀스·RViz·HMI·기록 전부. **힘·무게·파지력은 없다** (`scale.simulated:=true` 자동). RViz를 끄려면 `gui:=false`. HMI: http://localhost:5000 |
| real | `ros2 launch gmp_bringup cell.launch.py mode:=real host:=192.168.1.100` | 전부. **처음 띄울 때는 `vel_scale:=0.2`** |

전체 브링업은 벤더 컨트롤러를 먼저 활성화한 뒤 약 12초 후 셀 노드를 시작한다. 터미널에
`[SELF_CHECK] OK`와 `[ACTION_SERVERS_READY]`가 출력된 뒤 `ros2 action list -t`로 확인한다.
에뮬레이터 네트워크 생성으로 Jazzy `ros2cli`의 기존 daemon handle이 무효화되는 문제를 막기 위해
11초 시점에 daemon을 종료한 뒤 바로 다시 시작한다. CLI를 처음 실행할 때 daemon 생성과 DDS discovery를
기다리지 않도록 브링업 과정에서 미리 준비한다.

벤더 브링업을 먼저 실행한 뒤 `skill_node`만 띄울 때는 전용 런치가 공통 파라미터와 위치 YAML을 자동으로 넘긴다.
기본값은 실물 모드와 첫 기동 속도 `0.2`다.

```bash
ros2 launch gmp_bringup skill.launch.py
```

속도를 바꿀 때만 `vel_scale:=0.1`처럼 덧붙인다.

## 단위 테스트 (로봇 없이)

```bash
cd ~/auto-pharmacist/ros2_ws/src && python3 -m pytest gmp_dosing gmp_process -q
```

`gmp_process/test/test_process_node.py` 는 ROS 를 소싱한 상태에서 진짜 `process_node` 와 가짜 스킬 노드를 띄우는 통합 테스트다. **같은 `ROS_DOMAIN_ID` 로 다른 pytest 가 동시에 돌면 액션 서버가 겹쳐 무작위로 실패한다** ("There may be more than one action server" 경고, 실행마다 다른 테스트가 깨짐, NUDGE 계열이 특히 잘 걸림 — #167). 여러 세션·터미널에서 동시에 돌릴 때는 세션마다 다른 도메인을 준다: `ROS_DOMAIN_ID=71 python3 -m pytest …`. `test_run_batch_ros.py` 는 `ROS_DOMAIN_ID=88` 일 때만 실행되고 그 외에는 skip 된다.

관절 이송을 포함한 스킬 단위 테스트는 저장소 루트에서 실행한다.
가상·실물 검증은 사용자가 수행한다. 개발 검증은 아래 단위 테스트로 한정한다.

```bash
PYTHONPATH=ros2_ws/src/gmp_skills python3 -m pytest ros2_ws/src/gmp_skills/test -q
```

## 확인 명령

```bash
ros2 topic echo /cell/state --once
ros2 service call /cell/measure_force gmp_interfaces/srv/MeasureForce "{samples: 20, settle_s: 1.0}"
ros2 action send_goal /cell/move_to_station gmp_interfaces/action/MoveToStation "{station_id: safe, approach: 1}"
```

## 관절 이송 티칭·인계

9/22 승인: `workbench`, `passbox_empty`, `passbox_done`, `reject_bin`은
`stations.yaml`의 `solution_space: 3`을 사용한다. 목표 ABOVE까지 `amovejx`로 관절 이동하고
AT까지는 직선으로 접근한다. 같은 스테이션 AT↔ABOVE에서는 현재 sol=3을 먼저 확인하며
작업점에서 다른 관절 구성으로 뒤집지 않는다. 출발 용기 스테이션에서는 도착 이력·파지·sol을
확인하고 EXIT까지 직선 이탈한다. 목적지에 등록된 이전 관절 경로와 solution_space 설정의 중복은 거부한다.
가상에서도 이 네 스테이션은 같은 명령 경로를 사용한다.

`amovejx` 속도·가속도는 기존 `movej`와 같이 `robot.vel`·`robot.acc`에 `vel_scale`을 곱하며
단위는 deg/s·deg/s²다. TCP 직선 속도 제한이나 충돌 회피를 뜻하지 않는다.
도착은 IDLE·TCP 위치/회전·sol 일치를 모두 확인한다. sol 조회는 기존 벤더 클라이언트와
`robot.startup_timeout_s`를 사용하며, 조회 실패·취소·시간초과 시 성공 처리하지 않는다.

기존 `workbench → passbox_done` 관절 티칭 경로는 새 접근 방식으로 대체했다.
`passbox_done → nudge_wait`는 `enabled: false`를 유지하며 변경된 높이·관절 구성에 맞춰
재티칭·검증해야 한다. 실물에서 이 경로는 자동 우회하지 않는다. 가상에서는 기존 직선 폴백을 유지한다.
ROS Action 필드는 그대로다. 실행 중 도징에는 자동 반영하거나 노드를 재시작하지 않는다.
**다음 기동에서 설정을 읽으며, 새 경로의 간섭·용기 기울기·취소 정지는 실물 검증이 필요하다.**

### 필요한 티칭

모든 posx는 등록된 `GripperDA_v1` TCP의 BASE 좌표이며, posj는 6축 실제 관절각이다.
관절점만 임의 계산해 채우지 말고 해당 TCP·툴을 적용한 상태에서 함께 기록한다.

| 구간 | 반드시 기록할 값 | 확인할 조건 |
|---|---|---|
| 원료 반환 | A/B/C 시작 `return_start_posx`·끝 `return_end_posj` 입력 완료(9/21). 끝 posx는 참고 | 시작 직선→끝 관절 이동 후 유지. 동일 원료통 낙하·관절 경로 간섭 확인 필요. 재스쿱 연결 미구현 |
| 네 용기 스테이션 접근 | 목표 자세에서 조회한 sol=3을 설정에 반영 | EXIT 직선 이탈→목적지 ABOVE 관절 이동→AT 직선 하강의 간섭·기울기·흘림 검증 |
| `passbox_done → nudge_wait` | 기존 값은 보존했지만 passbox_done ABOVE·EXIT 관절각은 새 Z/sol=3에 맞춰 재티칭 필요 | 놓기 후 ABOVE 후퇴 완료 상태에서 EXIT로 직선 이탈하고 nudge_wait AT로 관절 직접 도착. 간섭 검증은 사용자 담당 |

- ABOVE·EXIT는 기준점에 **BASE Z 상대 높이**를 더해 계산한다. XYZ/자세 절대값은 중복 저장하지 않는다.
  - workbench 파지: AT Z=130, `approach_mm: 50`, `exit_mm: 150` → Z=180/280.
  - passbox_empty·passbox_done·reject_bin: AT Z=130, `approach_mm: 50`, `exit_mm: 150` → Z=180/280.
  - 스쿱·원료·계량의 높이는 바꾸지 않는다. **nudge_wait ABOVE는 사용하지 않는다.**
  workbench는 AT/ABOVE→EXIT를 확인한다. passbox_done은 놓기 후 AT→ABOVE 후퇴를 먼저 완료하고,
  넛지 이송에서는 ABOVE→EXIT만 수행한다. AT에서 넛지로 바로 요청하면 이동 없이 거부한다.
  기존 절대 `exit_posx` 경로도 읽지만, 현재 경로는 스테이션의 상대 높이로 계산한다.
- `waypoints_posj` 마지막 점은 `arrival: above`이면 목적지 ABOVE, `arrival: at`이면 목적지 AT다.
  관절 이동 후 실제 TCP의 위치·자세를 해당 도착점과 대조한다. nudge_wait는 제공된 AT 관절각을
  YAML 별칭으로 참조하므로 중복 입력하지 않는다.
  비활성 경로에도 입력된 티칭값은 보존한다. 미입력 값은 임의 관절각으로 채우지 않는다.
- 기존 `arrival: above` 경로는 `approach: 0`이면 ABOVE에서 끝나고, `approach: 1`이면 AT까지 직선 접근한다.
  넛지의 `arrival: at` 경로는 **`approach: 1`만 허용**하며 최종 직선 접근 없이 관절 이동으로 끝난다.
  `approach: 0` 요청을 AT로 바꿔 처리하지 않고 거부한다.
- `robot.transfer_joint_vel_deg_s`·`robot.transfer_joint_acc_deg_s2`는 현재 0이다.
  사용자가 검증할 양수 값을 설정해야 한다. Action `vel_scale`을 곱해 적용하며,
  직선 이동의 `robot.vel`·`robot.acc`와는 별개다.
- 출발 AT/ABOVE에서 확인된 관절 구성과 마지막 도착 상태가 맞아야 한다.
  티칭 도중 수동 이동하거나 노드를 재시작한 뒤에는 이전 위치·파지 이력을 재사용하지 않는다.
  정상적인 MoveToStation 도착과 SetGripper 성공 이력을 다시 쌓아야 한다.
  nudge 경로는 먼저 새 sol=3의 passbox_done ABOVE·EXIT를 재티칭하고 속도를 설정한다.
  MoveToStation으로 passbox_done ABOVE 도착 이력을 쌓고 SetGripper 열기 성공과
  실제 열림 폭을 확인한 뒤, 검증한 경로를 활성화한다.
  빈 그리퍼는 성공한 열기 이력, 약통은 약통 스테이션 AT에서 성공한 파지 이력이 필요하다.
  파지 피드백은 각 이동 구간 전후에 확인한다. 이는 이동 중 연속 파지 감시를 대체하지 않는다.
- 취소·실패 뒤에는 이송을 바로 재시도하지 않는다. 상태를 확인하고 출발 위치·파지 이력을
  다시 확립한다. SafePose는 위치 복귀일 뿐, 그리퍼가 비었다는 증거로 사용하지 않는다.
- 단위 테스트의 좌표는 가짜 입력이다. 실물 좌표나 검증된 관절 경로로 재사용하지 않는다.

### 공정 연결 및 후속 인계

스테이션 티칭과 `stations.yaml` 관리는 조장·A 담당이다. process는 `station_id`·`approach` 계약만 사용하며, 파지 좌표 연결은 C 담당 작업이 아니다.

- `_carry()`와 용기 계량은 동일한 `workbench.posx`를 AT로 사용한다. 용기 계량은 ABOVE(Z=180 = AT 130 + `approach_mm` 50, 107행과 같음), 스쿱 계량은 대응 `material_N.posx`다.
- 초과 스쿱 반환·재시도와 투입량 기록 분리는 공정 패키지에 함께 반영했다. 새 `ReturnMaterial` Action이 있으므로 사용자는 가상·실물 검증 전에 인터페이스와 호출 패키지를 다시 빌드해야 한다.
- 9/21 원료 A/B/C 반환 시작 posx·끝 posx/posj 입력 완료. 끝 이동은 `return_end_posj` 관절 이동(취소·도착 확인 포함)을 사용하고 시작점으로 복귀하지 않는다. 끝 posx는 참고용이다. 관절 보간 중 스쿱 궤적·낙하·간섭 검증은 별도다.
- TODO([A]): 반환 끝→재스쿱 경로는 스쿱 모션 구현 시 함께 연결한다. 현재 기본 Scoop 접근을 이 경로의 검증으로 간주하지 않는다.
- C/D 인계: 반환 성공은 끝 자세에서 완료되며 RETURN 피드백을 내지 않는다. interfaces.md의 반환 동작 설명은 v1.5.1로 정정했다. 메시지 필드 변경은 없다.
- HMI 담당 인계: 새 `RETURN_MATERIAL` 상태 표시명 및 outcome 5/6 표시를 연결한다. 기존 record_node는 숫자 outcome과 전체 원본을 저장하므로 DB 스키마 변경은 없다.
- `process_fsm.py:FINISH`는 약통을 `passbox_done`에 놓고 ABOVE로 후퇴한 뒤 `NUDGE_WAIT`로 전이한다.
  이어서 `nudge_wait` AT(`approach: 1`)로 이동해 NUDGE를 기다리고, 그 신호 뒤 DONE으로 끝난다.
  `_carry()`의 passbox_done AT→ABOVE 후퇴 순서를 유지하며, passbox_done AT 관절각을 추가할 필요는 없다.
- 기존 빈통 운반·스쿱 반납·폐기 경로는 이번 두 경로에 포함되지 않는다.
  보호 대상 목적지로 진입하는 추가 경로는 따로 티칭·등록해야 한다.

개발 완료 범위는 이송 실행·거부 조건·단위 테스트이며, 자동 공정 완료와 무흘림·무간섭은
위 인계 및 사용자 가상·실물 검증을 완료한 뒤에만 확인할 수 있다.

## 빌드 트러블슈팅 (9/16 실제 발생분)

벤더 워크스페이스 `~/ws_cobot_pjt/ws_dsr` 와 이 저장소 모두 **`colcon build --symlink-install` 로 고정**한다. 일반 빌드와 섞으면 아래 1번이 난다.

| 증상 | 원인 | 조치 |
|---|---|---|
| `failed to create symbolic link … Is a directory` (msgs 패키지) | 일반 빌드가 복사해 둔 실제 폴더 위에 symlink 빌드가 링크를 만들려 함 | 해당 패키지의 `build/<pkg>` `install/<pkg>` 삭제 후 재빌드 |
| `'distutils.core.setup()' was never called` (ament_python) | `setup.py` entry point 의 모듈명이 잘못됨 (하이픈 등). `python3 setup.py --dry-run --name` 으로 진짜 에러 확인 | 파일명·모듈명은 소문자·숫자·밑줄만 사용 |
| `executable 'xxx.py' not found on the libexec directory` | 스크립트 원본에 실행 권한(+x) 없음. symlink 빌드는 원본 권한이 그대로 보임 | `chmod +x <스크립트>` (재빌드 불필요) |


## G2 상태 비트 드라이버 (9/20)

- 실물은 `gmp_bringup/robot.launch.py` 또는 이를 포함하는 `cell.launch.py`를 사용한다.
  벤더 런치의 기존 OnRobot 서버 한 개만 `gmp_skills/rg2_status_driver`로 교체한다.
  기존 서버와 확장 서버를 동시에 실행하지 않는다. `skill.launch.py` 단독은 드라이버를 띄우지 않는다.
- 확장 서버는 벤더 원본을 상속하고 기존 50 Hz 상태 읽기 결과를 `/onrobot/status`
  (`onrobot_rg_msgs/msg/OnRobotRGInput`)에 발행한다. 로봇 명령 요청은 계속 skill_node 한 곳이다.
- Modbus 완료는 명령 후 busy 관측→idle 전이와 `gripper.completion_settle_s=0.2`초 폭 안정으로 확인한다. 이미 목표 근처인 무동작 명령은
  명령 전후 목표 근처의 새 idle 표본으로 확인한다. 상태 누락·노후·안전 스위치는 실패 처리한다.
  `grip_inferred` 필드명은 기존 계약을 유지하되 Modbus 값은 실제 grip 비트다.
  가상·DIO 경로는 기존 의미를 유지한다. 힘 표시는 측정 힘이 아니라 추적한 명령값이다.
- `GripperState.width_mm`는 기존 relative_width 기준을 유지한다. 원시 상태의 ggwd/gwdf/gfof는
  0.1 mm 단위이며 메시지 정의상 ggwd는 offset 제외, gwdf는 offset 포함 폭이다.
  물리 폭 기준은 캘리퍼 실측 전까지 확정하지 않는다. 방향별 보정값은 아직 적용하지 않았다.
- Python 의존성: 기존 onrobot_rg_control과 `pymodbus==3.6.9`가 필요하다.
  9/20 현장에서는 `/tmp/gmp-g2-venv`에 설치했고, 벤더 egg-link 인식 문제는
  `ws_dsr/build/onrobot_rg_control`을 실행 환경의 PYTHONPATH에 추가해 해결했다.
  `/tmp` 환경은 임시이며 재부팅 뒤 재구성이 필요할 수 있다.


현장 임시 환경을 다시 사용할 때(기존 드라이버 종료 후):

```bash
source tools/env.sh
export PYTHONPATH="/tmp/gmp-g2-venv/lib/python3.12/site-packages:<ws_dsr>/build/onrobot_rg_control:$PYTHONPATH"   # <ws_dsr> = 두산 SDK 워크스페이스 경로(사람마다 다름, 예: ~/ws_cobot_pjt/ws_dsr)
ros2 launch gmp_bringup robot.launch.py mode:=real host:=192.168.1.100 gui:=false
# 별도 터미널에서 source tools/env.sh 후:
ros2 launch gmp_bringup skill.launch.py mode:=real vel_scale:=0.2
```

벤더 comModbusTcp의 `busy` 키는 이름과 달리 register 268의 원시 상태 워드다.
확장은 이 워드를 그대로 사용하고 인접 register를 grip/safety로 읽는 벤더 dict 필드는 무시한다.
근거는 같은 벤더의 `_baseOnRobotRG.getStatus()` 원시 `status[10] → gsta` 매핑이다.

### 반환 경로 실물 확인 (9/21)

- 반환 끝 관절 이동 시도 이후에는 성공·취소·실패 모두 후속 Scoop이 이동 전에 거부된다. SafePose·파지 변경으로 해제하지 않는다. 반환→재스쿱 경로 구현 전까지 자동 연속 운용은 불가하다.
- 첫 실물 이동에서 원료 ABOVE→반환 시작(Z=170, Y=-340) 직선 진입이 원료통 테두리와 간섭하지 않는지 확인한다. 끝 관절 이동의 스쿱 궤적·낙하도 확인한다.
- 9/21 posx는 사용자 제공값이며 기존 관절각은 유지했다. 넛지·작업대·Pass Box의 posx/posj 일치는 실물 확인 대상이다.


### 상태·외력 조회 시간 초과 진단 (9/21)

- `[DSR_QUERY_FAILED]` 로그의 호출명(`get_robot_state`/`get_tool_force`)과 단계(서비스 준비/응답)를 확인한다. `robot.startup_timeout_s`는 각 단계의 제한 시간이다.
- 넛지 조회는 자가진단 완료 뒤, 안전 차단이 없는 동안만 실행한다. 조회 실패를 정상 계량값으로 사용하지 않으며 복구 전에는 일반 스킬이 거부된다.
- `[STARTUP] initialize 시작` 또는 `tool/TCP/충돌 감도 self_check 시작` 이후 멈추면 두 조회 이외의 설정·자가진단 호출도 확인해야 한다. 응답 제한이 모든 벤더 API에 적용된 것으로 판단하지 않는다.
- 복구 요청이 실패하면 실제 로봇 상태·컨트롤러 서비스를 먼저 확인한다. 정지·힘제어 해제 미확인 로그는 성공으로 간주하지 않는다.


### 스쿱 인출 뒤 수직 상승 (9/21 사용자 승인)

- 첫 `WeighHeld`는 현재 BASE +Y 150 mm 인출 뒤 **실제 도착 자세에서 Z +100 mm만 상승**하고, 그다음 해당 `material_N.posx`로 이동한다. X/Y와 회전은 상승 중 유지한다.
- 상승량은 `common.yaml`의 `gripper.scoop_extract_lift_z_mm`로 지정한다. 0·음수·비유한 값은 첫 이동 전에 거부한다.
- 인출/상승 중 실패·취소되면 계량 자세로 진행하지 않으며, 같은 요청을 다시 보내도 인출을 자동 반복하지 않는다.
- 기존 경로에서 원료통 충돌로 E-stop이 눌렸다. 코드 변경은 현재 충돌 위치에서의 복구 이동을 정의하지 않는다. E-stop 해제·장비 상태·실제 파지와 간섭 확인 후 별도 재시험이 필요하다. 이번 수정에서는 로봇을 재시작하거나 움직이지 않는다.
# 인출 완료 스쿱의 기동 시 복원

실물 초기화에서 `0`은 수동, `1`은 자동 모드다. 현재 툴·TCP가 맞으면 수동 전환과 재선택을 생략하고 자동 모드를 조회로 확인한다. 설정이 다른 경우에만 수동으로 선택 후 자동으로 복귀한다. 초기화 설정·조회 응답에는 `robot.startup_timeout_s`가 적용된다. 시간 초과는 설정 미적용을 보장하지 않으므로 상태를 확인하기 전에 반복 실행하지 않는다.

스킬만 재기동하여 파지 이력이 사라졌을 때 사용하는 수동 확인 절차다.
작업자는 해당 원료의 스쿱을 잡고, 거치대 인출·상승을 완료했으며,
`material_N.posx` 계량 자세에 정지했고 투입·반환 중 중단된 상태가 아님을 확인한다.
이 전제를 확인할 수 없으면 아래 복원을 사용하지 않는다. 공정 재기동 복원은 별도다.

bringup이 연결된 상태에서 기존 스킬의 종료 완료를 확인하고, 해당 기동에만 인자를 전달한다.
아래 `작업자ID`는 실제 확인자 식별자로 바꾼다.

```bash
source tools/env.sh
ros2 launch gmp_bringup skill.launch.py mode:=real vel_scale:=1.0 \
  restore_material_id:=A restore_operator_id:=작업자ID restore_confirmed:=true
```

`SCOOP_STATE_RESTORED`와 `SELF_CHECK OK` 로그를 확인한다. 복원은 실물 대기 상태,
해당 원료 계량 자세, 최신 Modbus 파지·폭·안전 상태를 검사하며 불일치 시 기동을 거부한다.
기동 직후 센서 미수신/오래된 표본은 `robot.startup_timeout_s`까지만 기다린다.
최신 센서의 실제 미파지·안전 이상은 즉시 거부하며, 대기 후 자세도 다시 확인한다.
이동·개폐·공정 자동 재개는 하지 않는다. 기본값은 비활성이며 확인 인자를 상시 실행 설정에 저장하지 않는다.
스쿱 원료 ID는 센서가 식별한 값이 아닌 작업자 확인값이다. 폭 지문 보정 완료를 뜻하지 않는다.

## 충돌 감도 조회 확장 설치 (#76)

벤더 원본은 수정하지 않는다. 새 인터페이스와 C++ 플러그인이 있어 처음에는 빌드가 필요하다.
정지된 개발 환경에서 아래 순서로 설치한다. 실행 중인 컨트롤러는 이 명령으로 재시작하지 않는다.

```bash
cd <auto-pharmacist 저장소 경로>   # 예: ~/auto-pharmacist
source tools/env.sh
cd ros2_ws
colcon build --symlink-install --packages-select gmp_interfaces gmp_dsr_controller gmp_skills gmp_bringup
source install/local_setup.bash
ros2 pkg prefix gmp_bringup
```

마지막 경로가 `auto-pharmacist/ros2_ws/install/gmp_bringup`인지 확인한다. 이전 루트 `install`을
선택하면 새 플러그인/런치가 적용되지 않는다. 다음 계획된 재기동에는 `gmp_bringup robot.launch.py`
또는 `cell.launch.py`를 사용한다. 벤더 런치만 실행하면 새 조회 서비스가 없어 실물 자가진단이 실패한다.

실물 기대값은 `safety.collision_sensitivity: 50.0`이다. 자가진단 결과에는 실제값과 기대값을 남긴다.
불일치 시 펜던트 설정과 승인값을 확인하며 프로그램이 값을 변경하지 않는다. 가상에서는 검증 생략을 표시한다.
서비스·기대값 의미는 `interfaces.md` 8절, 플러그인 제약은 `gmp_dsr_controller/README.md`를 따른다.
실물 조회와 새 컨트롤러 기동은 별도 검증이 필요하다.


## 높이 기반 스쿠핑 보정·인계 (9/22)

A 구현: `Scoop.depth_fraction` → 접촉 자세로 표면 WORLD Z 계산 → TW spline의 WORLD Z 평행 이동 → 털기 → 계량 위치 복귀. 설정은 `stations.yaml:scooping.A`이며 원본 경로·속도·주기 운동을 기록했다. `calibrated=false`라 현재 실물 이동은 거부한다. 기준 표면72.5와 바닥 여유5는 근삿값이고 제공 오프셋은 계산상 mid=48.6으로 유격 설명에서 추정한54~55와 차이가 있다(직접 실측 아님). 실제 WORLD/BASE 변환과 스쿱 끝 오프셋을 보정한 뒤 활성화 및 사용자 가상/실물 검증이 필요하다. B/C에는 보정 경로가 없어 거부한다.

인계 목록(다른 담당 코드는 수정하지 않음):
- B `gmp_dosing/core/dosing.py:DosingConfig.scoop_nominal_g`와 C 설정: 원료 A 기준 순량65 g과 통일할 것. 기본40 g을 그대로 사용하면 요청량과 맞지 않는다. 원료별 계수를 다른 원료에 그대로 적용하지 않는다.
- C `gmp_process/core/process_fsm.py:_scoop` 및 첫 SCOOP 진입: 첫 시도부터 남은 목표/기준 순량을 깊이 비율로 전달할 것. 현재 첫 시도1.0 고정은 작은 레시피를 반영하지 못한다. 큰 목표는 검증 최대 깊이1.0 이내에서 여러 번 계량하며 분할한다.
- B/C `min_fraction`: A profile 하한0.15와 일치 필요. 하한보다 적은 목표를 조용히 올려 과다 채취하지 말고 별도 처리한다. 무효 계량의 fraction=0 경로는 A에서 거부된다.
- C 반환 후 재스쿱: 기존 차단은 유지된다. 안전한 연결 경로 검증 전 자동 재시도 가능으로 처리하지 않는다.
- 기준 순량65 g은 모델 보정값이다. 실제 tare는 기존 WeighHeld 실측값을 유지한다. 내부 contact_pose_base는 Action 필드로 추가하지 않았다.

A 단독으로 가능한 범위는 보정 완료된 원료의 명시적 depth_fraction 실행이다. 레시피 g 기반 자동 분할·보정은 위 B/C 인계 후 가능하다. 새 spline은 ROS 어댑터/컨트롤러에서도 TW 경로와 속도 의미를 확인해야 하며, 원본의 성공을 ROS 구현 검증으로 대체하지 않는다.


### 원료 높이 측정 전용 실행 (9/22)

`skill.launch.py height_measure_only:=true`는 보정 활성 여부와 무관하게 기존 check_depth 측정·정상 복귀만 실행한다. 파지/인출 이력·안전 검사는 유지하며 spline/털기는 하지 않는다. 접촉 최초 BASE 자세를 WORLD로 변환하고 기준 자세에서 구한 TCP 로컬 끝 오프셋으로 접촉 지점의 높이를 계산한다. 로그 및 Scoop Result.message의 HEIGHT_MEASUREMENT_ONLY로 전달한다. 실제 스쿠핑을 하지 않았으므로 success=false / ABORTED로 반환한다. 자동 공정과 동시 사용하지 않는 수동 진단 모드다. 약90 mm의 칼라스톤 표면은 위치별 편차가 있으므로 단일 접촉점을 전체 표면 평균으로 취급하지 않는다. 최초 힘 감지 자세는 통신 지연·돌 재배열·끝 이외 부위 접촉의 영향을 받을 수 있다.
