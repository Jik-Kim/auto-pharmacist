# auto-pharmacist — 협동로봇 조제 칭량 셀 (Cal1 프로젝트)

배치 레시피에 따라 원료를 스쿱으로 퍼서 **정밀 칭량·분주**하고, 허용 오차를 **로봇 외력으로 스스로 검증**하며, 일탈이 나면 **셀 밖 QA 가 웹 HMI 로 판정**하고 **모든 기록이 SQLite 배치 기록·감사 추적으로 남는**
M0609 + RG2 조제 셀. 평상시 무인, 사람은 패스박스와 HMI 로만 셀과 만난다. 요구·제약의 원장: **[PROJECT_RULES.md](PROJECT_RULES.md)**.

## 기준 문서

- [docs/SOT.md](docs/SOT.md) 확정 결정 · [docs/interfaces.md](docs/interfaces.md) **계약 v1.0** (토픽·서비스·액션·파라미터) · [docs/architecture.md](docs/architecture.md) 데이터 흐름
- [docs/setup.md](docs/setup.md) 환경 구축·실행 · [docs/demo_run_procedure.md](docs/demo_run_procedure.md) 시연 절차 (명령 정본)
- [docs/responsibilities.md](docs/responsibilities.md) 영역별 책임 · [docs/todo.md](docs/todo.md) 파일 단위 작업 · [docs/issues.md](docs/issues.md) 이슈
- [PROJECT.md](PROJECT.md) 개요·역할 · [AGENTS.md](AGENTS.md) 작업 규칙 · [docs/spec/](docs/spec/README.md) BRD·SDD

## 구조

```text
ros2_ws/src/
├── gmp_interfaces/   # msg/srv/action 계약 (ament_cmake + rosidl)                  [조장]
├── gmp_skills/       # DSR_ROBOT2·RG2 어댑터, 스킬 Action/Service — 로봇을 만지는 유일한 노드  [A 스킬]
├── gmp_dosing/       # 힘→그램 환산·영점·보정, 이중 폐루프 도징 정책 (ROS 비의존 라이브러리)   [B 도징]
├── gmp_process/      # 레시피 실행 상태기계, 일탈·인터락, RunBatch Action 서버            [C 공정]
├── gmp_hmi/          # 웹 HMI(Flask, 원격 QA 승인), 배치 기록 DB(SQLite, 감사 추적)      [D HMI·기록]
└── gmp_bringup/      # launch(real/virtual), params(공통·스테이션·레시피)                 [조장]
tools/                # todo_stats.py 등
docs/
```

각 앱 패키지는 `nodes/`(rclpy) · `core/`(ROS 비의존) · `adapters/`(장치) 로 나뉜다.

## 빌드 — 언더레이 위에 오버레이

`~/ws_cobot_pjt/ws_dsr`(doosan-robot2 · onrobot-ros2 · m0609_rg2_bringup, 9/16 실기 확인) 를 **먼저 source** 하고 이 저장소를 얹는다.

```bash
source /opt/ros/jazzy/setup.bash
source ~/ws_cobot_pjt/ws_dsr/install/setup.bash
cd ~/auto-pharmacist/ros2_ws && colcon build --symlink-install && source install/setup.bash
```

## 실행

```bash
ros2 launch gmp_bringup cell.launch.py mode:=virtual              # 에뮬레이터 (랜선 없이)
ros2 launch gmp_bringup cell.launch.py mode:=real host:=192.168.1.100
```

명령·순서·게이트는 [docs/demo_run_procedure.md](docs/demo_run_procedure.md) 가 정본이다.

## 상태

**골격 생성 (9/16 밤).** 계약 v1.0 과 패키지 스켈레톤만 있다. 실물 5일(9/17·18·21·22·23)은 `docs/todo.md` 순서대로 —
**9/17 오전 첫 게이트는 외력 분해능 측정**(I-001)이다. 이 값이 도징 단위(30 g 인가 100 g 인가)를 정한다.
