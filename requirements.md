# 실행 환경과 의존성

이 문서는 제출용 [requirements.txt](requirements.txt)의 Python 항목과, 그 파일만으로 설치할 수 없는 ROS 2·로봇 의존성을 구분합니다. 실제 실행 순서는 [docs/setup.md](docs/setup.md)와 [README.md](README.md)가 기준입니다.

## 운영체제와 장비

| 항목 | 현행 기준 |
|---|---|
| 운영체제 | Ubuntu 24.04 |
| ROS 2 / Python | Jazzy / Python 3.12 |
| 로봇·그리퍼 | 두산 M0609 · OnRobot RG2 (현행 실물 백엔드는 DIO) |
| 작업 PC | 1대에서 ROS 노드, Flask HMI, SQLite DB 실행 |
| 가상 모드 | 두산 DRCF 에뮬레이터와 가상 그리퍼. 실물 힘·파지력·안전 동작 검증은 대체하지 않음 |

## 소프트웨어 의존성

| 구분 | 필요 항목 | 설치·제공 경로 |
|---|---|---|
| ROS 기본 | ROS 2 Jazzy, `colcon`, `rclpy`, `launch`, `launch_ros`, 메시지·서비스 런타임 | 시스템 ROS 2 환경 |
| 벤더 언더레이 | `doosan-robot2`, `onrobot-ros2`, `m0609_rg2_bringup` 및 그 의존 패키지 | 별도 `ws_dsr` 빌드 후 source |
| 이 저장소 | `gmp_interfaces`, `gmp_dsr_controller`, `gmp_skills`, `gmp_dosing`, `gmp_process`, `gmp_hmi`, `gmp_bringup` | `ros2_ws`에서 `colcon build --symlink-install` |
| Python 런타임 | Flask, PyYAML | Ubuntu에서는 `sudo apt install python3-flask python3-yaml` 권장 |
| 데이터베이스 | SQLite | Python 표준 라이브러리 `sqlite3`; 별도 DB 서버 없음 |
| 시험 | pytest | 시험 실행 시 필요; 로봇 실물 검증을 대체하지 않음 |

ROS 패키지별 직접 의존성의 원본은 각 `ros2_ws/src/gmp_*/package.xml`입니다. `requirements.txt`는 **ROS·두산 드라이버 전체를 설치하는 파일이 아닙니다.** 현재 DIO 경로에서는 Modbus용 `pymodbus`가 필수는 아니며, Modbus 백엔드를 선택하는 경우 벤더 드라이버와 호환되는 `pymodbus==3.6.9`가 추가로 필요합니다.

## 빌드와 실행 순서

```bash
source /opt/ros/jazzy/setup.bash
source ~/ws_cobot_pjt/ws_dsr/install/setup.bash
cd ~/auto-pharmacist/ros2_ws
colcon build --symlink-install
source install/setup.bash
ros2 launch gmp_bringup cell.launch.py mode:=virtual
```

실물에서는 마지막 명령을 `mode:=real host:=192.168.1.100 vel_scale:=0.2`로 바꾸고, 컨트롤러·그리퍼 연결과 안전 상태를 확인한 뒤 실행합니다. 배포 PC의 실제 워크스페이스 경로가 다르면 위 경로도 맞춰야 합니다. 더 자세한 기동·검증 순서는 [docs/setup.md](docs/setup.md)에 있습니다.
