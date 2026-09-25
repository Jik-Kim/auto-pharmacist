# auto-pharmacist — 협동로봇 조제 칭량 셀 (Cal1 프로젝트)

배치 레시피에 따라 원료를 스쿱으로 퍼서 **정밀 칭량·분주**하고, 허용 오차를 **로봇 외력으로 스스로 검증**하며, 일탈이 나면 **셀 밖 QA 가 웹 HMI 로 판정**하고 **모든 기록이 SQLite 배치 기록·감사 추적으로 남는**
M0609 + RG2 조제 셀. 평상시 무인, 사람은 패스박스와 HMI 로만 셀과 만난다. 요구·제약의 원장: **[PROJECT_RULES.md](PROJECT_RULES.md)**.

## 기준 문서

- [docs/SOT.md](docs/SOT.md) 확정 결정 · [docs/interfaces.md](docs/interfaces.md) **계약 v1.5.1** (확정 v1.3·v1.5, v1.4 구현·확정 대기, v1.6 제안 — 상태 요약은 문서 머리) · [docs/architecture.md](docs/architecture.md) 데이터 흐름
- [docs/setup.md](docs/setup.md) 환경 구축·실행 · [docs/demo_run_procedure.md](docs/demo_run_procedure.md) 시연 절차 (명령 정본)
- [docs/responsibilities.md](docs/responsibilities.md) 영역별 책임 · 할 일·이슈는 **[GitHub Issues](https://github.com/Jik-Kim/auto-pharmacist/issues)** (9/21 부터 정본 — `docs/todo.md`·`docs/issues.md` 는 동결 스냅샷)
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
tools/                # env.sh, 도면·영상 생성 스크립트 (todo_stats.py 는 9/21 부로 미사용)
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
ros2 launch gmp_bringup cell.launch.py mode:=virtual              # 에뮬레이터 + RViz (랜선 없이)
```
```bash
ros2 launch gmp_bringup cell.launch.py mode:=real host:=192.168.1.100
```

명령·순서·게이트는 [docs/demo_run_procedure.md](docs/demo_run_procedure.md) 가 정본이다.

### 실물 로봇 bringup과 스킬 개별 실행

저장소 루트에서 실행한다. 기존 스킬을 먼저 종료하고 bringup 종료까지 확인한 뒤 재시작한다.
`cell.launch.py`와 아래 개별 실행을 동시에 띄우지 않는다.

터미널 1 — 로봇 연결·제어권 및 RG2 상태 드라이버:

```bash
source tools/env.sh
export ROS_HOME=/tmp/gmp-ros-domain70
export ROS_LOG_DIR=/tmp/gmp-g2-ros-log
GMP_DSR_WS="$(dirname "$(dirname "$WS_DSR_SETUP")")"
export PYTHONPATH="$GMP_DSR_WS/build/onrobot_rg_control${PYTHONPATH:+:$PYTHONPATH}"
ros2 launch gmp_bringup robot.launch.py mode:=real host:=192.168.1.100 gui:=false
```

`ROS_HOME`은 다른 도메인의 컨트롤러 spawner 잠금과 분리한다.
`PYTHONPATH`는 현 언더레이의 RG2 Python 모듈 위치를 보완한다.

터미널 2 — 컨트롤러 활성화 확인 후 스킬 실행:

```bash
source tools/env.sh
export ROS_LOG_DIR=/tmp/gmp-g2-ros-log
ros2 launch gmp_bringup skill.launch.py mode:=real vel_scale:=0.2
```

검증된 속도를 지정하려면 `vel_scale`을 변경한다. 스쿱을 잡은 채 재기동할 때는
[파지 상태 복원 절차](docs/setup.md#인출-완료-스쿱의-기동-시-복원)를 따르며,
복원 확인 인자를 상시 실행 명령에 넣지 않는다.

## 상태

**9/25 기준.** 계약 **v1.8**(`DispenseResult.verdict` INVALID 추가, #241), 패키지 6개(interfaces·skills·dosing·process·hmi·bringup) 구현·통합 시험 완료 — 시험 기준선은 파트별 `practice/<파트>/CURRENT.md`. 레시피는 `gmp_bringup/params/recipes/recipe-01~03.yaml`(원료당 85 g 또는 170 g, ±10 %, SOT D-33) — 9/21 의 200/150/100 g 과 9/23 오전의 총 120/120/160 g ±5 % 는 폐기값이다. VERIFY ① 허용폭은 recipe-01·02 25.5 g · recipe-03 34 g 으로 넓어져 재파지 σ 대비 통과 가능권이지만, 치구 합격 기준(D-31)은 옛 레시피 값이라 재설정 대기다. 시연은 **고정 스쿱**(DRL 고정 티칭 경로, `dosing.fixed_scoop`, D-33) — 고정 경로는 접촉을 재지 않으므로 공정이 접촉 대신 순중량으로 빈 스쿱을 판정하는 통합(D-34, PR #284·#287)이 **머지 대기**이고, 그 전에는 첫 스쿱에서 무한 보충 루프가 난다(#282). 스쿱 1회량 실측(#272, PR #283)이 기준값 85 g 보다 낮게 나와 기준값·레시피 조정이 **조장 결재 대기**다(`practice/B/CURRENT.md`). 공구 `tool_weight` 는 펜던트 동결(#235), 계량 `scale.gain/offset_g` 는 PLA 공구에서 재보정 전 미검증(#187). 실물 검증 항목은 `needs:physical` 라벨 이슈. 현황은 [GitHub Issues](https://github.com/Jik-Kim/auto-pharmacist/issues), 확정 결정은 `docs/SOT.md`, 파트별 현행값은 `practice/<파트>/CURRENT.md`.
