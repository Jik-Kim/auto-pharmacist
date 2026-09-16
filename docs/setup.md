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
source ~/auto-pharmacist/tools/env.sh      # /opt/ros → ws_dsr → auto-pharmacist 순서로 source
```

## 실행

| 모드 | 명령 | 되는 것 / 안 되는 것 |
|---|---|---|
| virtual | `ros2 launch gmp_bringup cell.launch.py mode:=virtual` | 이동·시퀀스·HMI·기록 전부. **힘·무게·파지력은 없다** (`scale.simulated:=true` 자동). HMI: http://localhost:5000 |
| real | `ros2 launch gmp_bringup cell.launch.py mode:=real host:=192.168.1.100` | 전부. **처음 띄울 때는 `vel_scale:=0.2`** |

노드만 따로: `ros2 run gmp_skills skill_node --ros-args -r __ns:=/cell -p mode:=virtual` 처럼 네임스페이스를 **반드시** 붙인다.

## 단위 테스트 (로봇 없이)

```bash
cd ~/auto-pharmacist/ros2_ws/src && python3 -m pytest gmp_dosing gmp_process -q
```

## 확인 명령

```bash
ros2 topic echo /cell/state --once
ros2 service call /cell/measure_force gmp_interfaces/srv/MeasureForce "{samples: 20, settle_s: 1.0}"
ros2 action send_goal /cell/move_to_station gmp_interfaces/action/MoveToStation "{station_id: safe, approach: 1}"
```
