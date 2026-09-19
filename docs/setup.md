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
9/19 개발 검증: 아래 테스트 **93개 통과**. 가상·실물 검증은 수행하지 않았다.

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
현재 `stations.yaml: transfers`의 두 경로는 `enabled: false`다. 미티칭 경로 호출은 실패하며
기존 `amovel`로 우회하지 않는다. 따라서 티칭·설정 전에는 FINISH 반송이 완료되지 않는다.
보호 대상 목적지(`passbox_done`, `nudge_wait`)에 임의 출발지에서 직접 진입하는 요청도 거부한다.
기존 ROS Action 필드(`station_id`, `approach`, `vel_scale`)는 그대로다.

### 필요한 티칭

모든 posx는 등록된 `GripperDA_v1` TCP의 BASE 좌표이며, posj는 6축 실제 관절각이다.
관절점만 임의 계산해 채우지 말고 해당 TCP·툴을 적용한 상태에서 함께 기록한다.

| 구간 | 반드시 기록할 값 | 확인할 조건 |
|---|---|---|
| 공통 선행 | 실제 `workbench.posx`(계량), `workbench.pick_posx`(약통 파지) | 현재 계량 posx는 자리표시자. C 담당의 파지 좌표 연결 전 약통 경로 활성화 금지 |
| `workbench → passbox_done` | 출발 AT·ABOVE의 `start_at_posj`·`start_above_posj`, 직선 이탈점 `exit_posx`·`exit_posj`, 관절 경유점과 passbox_done ABOVE의 관절각 | 약통을 든 전체 경로의 기울기·흘림·간섭 확인 |
| `passbox_done → nudge_wait` | 출발 AT·ABOVE의 `start_at_posj`·`start_above_posj`, 직선 이탈점 `exit_posx`·`exit_posj`, 관절 경유점과 nudge_wait ABOVE의 관절각 | 약통을 놓은 빈 그리퍼. 손목·팔·케이블 간섭 확인 |

- ABOVE는 기존 계약대로 `station.posx`에 **BASE Z + approach_mm**를 적용한 위치다.
  `exit_posx`는 별도로 정할 수 있지만 출발 AT/ABOVE와 **같은 방향**이어야 한다.
  AT→이탈점과 ABOVE→이탈점 **둘 다** 직선으로 빠져나갈 수 있는지 확인한다.
- `waypoints_posj`에는 순서대로 관절점을 넣는다. 마지막 점은 목적지 ABOVE의 관절각이다.
  관절 이동 후 실제 TCP가 목적지 ABOVE와 다르면 최종 접근 없이 실패한다.
- 이미 이탈점이면 후퇴를 생략한다. `approach: 0`은 ABOVE에서 끝나고,
  `approach: 1`은 목적지 AT까지 직선 접근한다.
- `robot.transfer_joint_vel_deg_s`·`robot.transfer_joint_acc_deg_s2`는 현재 0이다.
  사용자가 검증할 양수 값을 설정해야 한다. Action `vel_scale`을 곱해 적용하며,
  직선 이동의 `robot.vel`·`robot.acc`와는 별개다.
- 출발 AT/ABOVE에서 확인된 관절 구성과 마지막 도착 상태가 맞아야 한다.
  티칭 도중 수동 이동하거나 노드를 재시작한 뒤에는 이전 위치·파지 이력을 재사용하지 않는다.
  정상적인 MoveToStation 도착과 SetGripper 성공 이력을 다시 쌓아야 한다.
  nudge 경로만 먼저 검증할 때는 수동으로 티칭된 passbox_done AT/ABOVE에 놓고,
  그 위치의 MoveToStation을 요청하면 위치·관절각 일치 시 **움직이지 않고** 출발 이력을
  확립한다. 이때 nudge 경로는 티칭값을 채워 활성화한 상태여야 하며, 이후 SetGripper
  열기 성공과 실제 열림 폭을 확인해야 한다. 위치가 다르면 자동 이동 없이 거부한다.
  빈 그리퍼는 성공한 열기 이력, 약통은 약통 스테이션 AT에서 성공한 파지 이력이 필요하다.
  파지 피드백은 각 이동 구간 전후에 확인한다. 이는 이동 중 연속 파지 감시를 대체하지 않는다.
- 취소·실패 뒤에는 이송을 바로 재시도하지 않는다. 상태를 확인하고 출발 위치·파지 이력을
  다시 확립한다. SafePose는 위치 복귀일 뿐, 그리퍼가 비었다는 증거로 사용하지 않는다.
- 단위 테스트의 좌표는 가짜 입력이다. 실물 좌표나 검증된 관절 경로로 재사용하지 않는다.

### C 담당 인계 (이번 수정에서 공정 패키지는 변경하지 않음)

- `process_node.py:_carry()`는 `workbench AT`를 파지점으로 요청한다. 현재 스킬 계량은
  `workbench.pick_posx`를 별도로 쓴다. **계량 자세와 파지 자세를 분리해 연결한 뒤**
  실제 출발점에 맞게 약통 이송 경로와 티칭값을 함께 확정해야 한다.
- `process_fsm.py:FINISH`는 현재 DONE으로 끝난다. 약통 놓기·이탈 완료 후
  `nudge_wait` 요청을 추가하는 자동 전이는 C 담당 후속 작업이다.
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
