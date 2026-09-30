# auto-pharmacist — 협동로봇 조제 칭량 셀 (Cal1 프로젝트)

배치 레시피에 따라 원료를 스쿱으로 퍼서 **정밀 칭량·분주**하고, 허용 오차를 **로봇 외력으로 스스로 검증**하며, 일탈이 나면 **셀 밖 QA 가 웹 HMI 로 판정**하고 **모든 기록이 SQLite 배치 기록·감사 추적으로 남는**
M0609 + RG2 조제 셀. 평상시 무인, 사람은 패스박스와 HMI 로만 셀과 만난다. 요구·제약의 원장: **[PROJECT_RULES.md](PROJECT_RULES.md)**.

## 기준 문서

- [docs/SOT.md](docs/SOT.md) 확정 결정 · [docs/interfaces.md](docs/interfaces.md) **계약 v1.11** (버전별 변경·확정 상태는 문서 머리) · [docs/architecture.md](docs/architecture.md) 데이터 흐름
- [docs/setup.md](docs/setup.md) 환경 구축·실행 · [docs/demo_run_procedure.md](docs/demo_run_procedure.md) 시연 절차 (명령 정본) · [docs/trial_and_error_0929-0930.md](docs/trial_and_error_0929-0930.md) 9/29~30 시행착오 (발표용)
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

기본 `cell.launch.py` 한 번으로 로봇 bringup과 셀 앱 노드 4개가 모두 시작됩니다.
로봇 bringup은 두산 컨트롤러·에뮬레이터/실물 연결과 설정에 따른 RG2 드라이버를 포함합니다.
약 12초 뒤 `/cell/skill_node`(로봇 스킬), `/cell/process_node`(공정),
`/cell/record_node`(기록), `/cell/hmi_web_node`(웹 HMI)를 시작합니다.
`hmi:=false`는 웹 HMI만 빼고 기록 노드는 유지하며, `gui:=false`는 RViz만 끕니다.
`gmp_dosing`은 공정에서 가져다 쓰는 라이브러리이고 `gmp_interfaces`는 메시지 정의라 별도 노드가 없습니다.
런치가 시작됐어도 노드 준비가 끝났다는 뜻은 아니므로 아래 확인 명령으로 실제 기동을 확인합니다.

```bash
ros2 launch gmp_bringup cell.launch.py mode:=virtual              # 에뮬레이터 + RViz (랜선 없이)
```
```bash
ros2 launch gmp_bringup cell.launch.py mode:=real host:=192.168.1.100 vel_scale:=0.2  # 첫 기동
```

```bash
ros2 node list
ros2 action list -t
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

### 각 노드 개별 실행

아래는 실물 모드 예시입니다. `cell.launch.py`와 동시에 실행하면 같은 노드가 중복됩니다.
위의 `robot.launch.py`를 먼저 실행하고 컨트롤러 활성화를 확인한 뒤,
서로 다른 터미널에서 스킬·공정·기록·HMI를 한 개씩 실행합니다.
**각 터미널에서 먼저** 저장소 루트로 이동해 다음 환경을 설정합니다.

```bash
source tools/env.sh
GMP_PARAMS="$(ros2 pkg prefix gmp_bringup)/share/gmp_bringup/params"
GMP_DB="$HOME/auto-pharmacist/records/cell.db"
```

스킬 노드 — 위의 `skill.launch.py`가 공통 설정과 스테이션 파일을 전달합니다.

```bash
ros2 launch gmp_bringup skill.launch.py mode:=real vel_scale:=0.2
```

공정 노드:

```bash
ros2 run gmp_process process_node --ros-args -r __ns:=/cell \
  --params-file "$GMP_PARAMS/common.yaml" -p stations_file:="$GMP_PARAMS/stations.yaml"
```

기록 노드:

```bash
ros2 run gmp_hmi record_node --ros-args -r __ns:=/cell \
  --params-file "$GMP_PARAMS/common.yaml" \
  -p db_path:="$GMP_DB" -p export_dir:="$(dirname "$GMP_DB")"
```

웹 HMI 노드:

```bash
ros2 run gmp_hmi hmi_web_node --ros-args -r __ns:=/cell \
  --params-file "$GMP_PARAMS/common.yaml" \
  -p db_path:="$GMP_DB" -p recipes_dir:="$GMP_PARAMS/recipes" -p port:=5000
```

실물에서 RG2 Modbus 백엔드를 쓰면 `robot.launch.py`가 상태 드라이버를 함께 띄웁니다.
위 개별 명령은 전체 런치와 같은 `/cell` 네임스페이스와 공통 설정을 사용합니다.

## 상태

**9/30 시연일 기준.** 계약 **v1.11**(`RestoreGrip` v1.10, 반환 뒤 재스쿱 연결·붓기 후 계량 삭제 설명 v1.10.1, 소프트웨어 비상정지·HMI 안전 자세 요청 v1.11). 패키지 6개 구현·통합 시험 완료, main 시험 기준선(9/30, ROS 소싱·`ROS_DOMAIN_ID` 격리)은 process 262 · skills 589 · dosing 91 · hmi 149 · tools 6 — 파트별 값은 `practice/<파트>/CURRENT.md`.

- **레시피·1회량** — 전 원료 한 스쿱 69 g · 허용오차 ±15 %(`recipe-01` A·B·C 69 / `recipe-02` A 138·B 69 / `recipe-03` A 69·B 69·C 138), 스쿱 기준값 A·B·C 69 g (SOT D-36, D-35 의 79 g 대체).
- **계량** — 용기·스쿱 경로별 gain·offset(D-37, 용기 gain 0.873 은 9/30 케이블 정리 뒤), 안정화 10 s·스쿱 무효 기준 std 10·고주파 9.5 g·원료별 빈 스쿱 편향 A 13·B 10·C 0 g·빈 스쿱 두 번 재기(8 g), 붓기 후 잔량 계량 없음 — 투입량은 붓기 전 순량(D-38). 붓기 전에 되돌릴 수 있는 스쿱(초과·하한 미달·3회 무효)은 반환 → 재스쿱(D-42).
- **고정 스쿱** — DRL 고정 티칭 경로(`dosing.fixed_scoop`, D-33·D-34). 반환한 스쿱은 반환 끝에서 같은 원료를 다시 뜬다(D-39, 9/30 실물 확인).
- **안전** — 안전 자세 복귀는 걸릴 자리(용기 자리·원료통)에서 먼저 빠진 뒤 이동한다. HMI 에 소프트웨어 비상정지와 복구 뒤 「안전 자세로 이동」 버튼(D-43) — 물리 비상정지를 대신하지 않는다.
- **세트 끝** — 넛지 = 회수 확인(D-23, v1.9). 폐기 배치는 안전 자세를 거쳐 넛지 대기(D-40). 이송 중 ENTER 는 재개하지 않고 배치 중단으로 끝난다 — 배치 중 ENTER 는 보충 대기 때만(D-41).
- **시연 절차** — [docs/demo_run_procedure.md](docs/demo_run_procedure.md). 실물 검증 항목은 `needs:physical` 라벨 이슈.

현황은 [GitHub Issues](https://github.com/Jik-Kim/auto-pharmacist/issues), 확정 결정은 `docs/SOT.md`, 파트별 현행값은 `practice/<파트>/CURRENT.md`.
