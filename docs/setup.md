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

9/19 변경은 단위 테스트까지만 개발자가 검증한다. **가상·실물 검증은 사용자가 수행한다.**
현재 `stations.yaml: transfers`의 두 경로는 `enabled: false`다. **가상 모드는 활성 여부·관절 속도와 무관하게 기존 `amovel` 직선 이동을 사용**하며 보호 목적지 진입 제한도 적용하지 않는다.
**실물 모드는** 미티칭·비활성 경로와 보호 목적지의 임의 출발 진입을 거부한다. 따라서 실물 FINISH 반송은 티칭·설정·검증 뒤에 가능하다.
기존 ROS Action 필드(`station_id`, `approach`, `vel_scale`)는 그대로다.

### 필요한 티칭

모든 posx는 등록된 `GripperDA_v1` TCP의 BASE 좌표이며, posj는 6축 실제 관절각이다.
관절점만 임의 계산해 채우지 말고 해당 TCP·툴을 적용한 상태에서 함께 기록한다.

| 구간 | 반드시 기록할 값 | 확인할 조건 |
|---|---|---|
| 원료 반환 | A/B/C 시작 `return_start_posx`·끝 `return_end_posj` 입력 완료(9/21). 끝 posx는 참고 | 시작 직선→끝 관절 이동 후 유지. 동일 원료통 낙하·관절 경로 간섭 확인 필요. 재스쿱 연결 미구현 |
| `workbench → passbox_done` | 출발 파지 AT·ABOVE·EXIT 관절각 및 목적지 ABOVE 관절각 반영 완료 | 직접 관절 이송의 기울기·흘림·간섭 확인 후 필요할 때만 중간 관절점 추가 |
| `passbox_done → nudge_wait` | passbox_done ABOVE·EXIT 및 nudge_wait AT 관절각 반영 완료. **추가 필수 티칭값 없음** | 놓기 후 ABOVE 후퇴 완료 상태에서 EXIT로 직선 이탈하고 nudge_wait AT로 관절 직접 도착. 간섭 검증은 사용자 담당 |

- ABOVE·EXIT는 기준점에 **BASE Z 상대 높이**를 더해 계산한다. XYZ/자세 절대값은 중복 저장하지 않는다.
  - workbench 파지: `posx` 기준 `approach_mm: 100`, `exit_mm: 200` → Z=200/300.
  - passbox_empty·passbox_done·reject_bin: AT Z=100, `approach_mm: 50`, `exit_mm: 150` → Z=150/250.
  - 스쿱·원료·계량의 높이는 바꾸지 않는다. **nudge_wait ABOVE는 사용하지 않는다.**
  workbench는 AT/ABOVE→EXIT를 확인한다. passbox_done은 놓기 후 AT→ABOVE 후퇴를 먼저 완료하고,
  넛지 이송에서는 ABOVE→EXIT만 수행한다. AT에서 넛지로 바로 요청하면 이동 없이 거부한다.
  기존 절대 `exit_posx` 경로도 읽지만, 이번 두 경로는 스테이션의 상대 높이로 계산한다.
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
  nudge 경로만 먼저 검증할 때는 수동으로 티칭된 passbox_done **ABOVE**에 놓고,
  그 위치의 MoveToStation(`station_id: passbox_done, approach: 0`)을 요청하면 위치·관절각 일치 시 **움직이지 않고** 출발 이력을
  확립한다. 이때 nudge 경로는 티칭값을 채워 활성화한 상태여야 하며, 이후 SetGripper
  열기 성공과 실제 열림 폭을 확인해야 한다. 위치가 다르면 자동 이동 없이 거부한다.
  빈 그리퍼는 성공한 열기 이력, 약통은 약통 스테이션 AT에서 성공한 파지 이력이 필요하다.
  파지 피드백은 각 이동 구간 전후에 확인한다. 이는 이동 중 연속 파지 감시를 대체하지 않는다.
- 취소·실패 뒤에는 이송을 바로 재시도하지 않는다. 상태를 확인하고 출발 위치·파지 이력을
  다시 확립한다. SafePose는 위치 복귀일 뿐, 그리퍼가 비었다는 증거로 사용하지 않는다.
- 단위 테스트의 좌표는 가짜 입력이다. 실물 좌표나 검증된 관절 경로로 재사용하지 않는다.

### 공정 연결 및 후속 인계

스테이션 티칭과 `stations.yaml` 관리는 조장·A 담당이다. process는 `station_id`·`approach` 계약만 사용하며, 파지 좌표 연결은 C 담당 작업이 아니다.

- `_carry()`와 용기 계량은 동일한 `workbench.posx`를 AT로 사용한다. 용기 계량은 ABOVE(Z=200), 스쿱 계량은 대응 `material_N.posx`다.
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
export PYTHONPATH="/tmp/gmp-g2-venv/lib/python3.12/site-packages:/home/jonny/rokey_proj/Automation/ws_cobot_pjt/ws_dsr/build/onrobot_rg_control:$PYTHONPATH"
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
