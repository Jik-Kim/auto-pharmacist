# AGENTS.md

이 파일은 저장소 전체에 적용되는 공통 작업 규칙이다. 하위 패키지의 `README.md` 에는 해당 영역 규칙만 둔다.
(mro-multi-amr 의 규칙을 이어받아 이 프로젝트에 맞게 줄였다.)

## 작업 시작

- 작업 전에 `docs/SOT.md` 와 `git status` 를 확인한다. 통신 계약은 `docs/interfaces.md`, 실행 절차는 `docs/setup.md`.
- 요구사항의 근거는 **규칙 원장 `PROJECT_RULES.md`(R1~R23, 평가 기준 3-8)** 와 BRD(`docs/spec/`). 문서와 코드가 충돌하면 SOT 를 우선하고 충돌 사실을 알린다.
- 다른 담당의 패키지를 수정하지 않는다. 필요하면 GitHub Issue 로 남기고 담당에게 알린다.
- **예외 1건:** `gmp_dosing` 은 라이브러리다. `gmp_process` 가 import 하지만 수정은 도징 담당(B)만 한다.

## 작업 범위

- 요청받은 범위에서 가장 작은 변경으로 목적을 달성한다.
- 토픽·메시지·서비스·액션 계약(`gmp_interfaces`, `docs/interfaces.md`)을 바꿀 때는 **팀 채널에 먼저 알리고** 문서와 같이 고친다. 계약은 4명 모두의 전제라 한 사람이 정하지 않는다.
- 확정되지 않은 파라미터(`[팀 확정 필요]`)를 임의로 결정하지 않는다. 값은 `gmp_bringup/params/*.yaml` 에 두고 코드에 하드코딩하지 않는다.
- 구현하지 않은 영역은 한글 `TODO([담당])` 로 남긴다.
- **코드에서 호출·상태·전이를 지우거나 바꾸면 생성 문서도 같은 PR 에서 다시 만든다** — `tools/make_process_drawio.py` → `docs/diagrams/process_flow.drawio` 등. 9/21 에 `set_tare` 를 지우면서 그 호출을 그리는 그림을 구현·1차·최종 검토가 모두 놓쳤다. 리뷰어는 `python3 tools/make_*.py` 재실행 후 `git status docs/diagrams` 가 비는지 본다.
- **계약 값의 의미가 바뀌면 그 값을 넘기는 쪽과 받는 쪽의 주석·docstring 을 같이 훑는다.** 9/21 에 `fraction` 이 v1.3→v1.5 로 「붓기 비율」에서 「담그기 깊이」로 바뀌었는데, 넘기는 C(`process_fsm` docstring·`_scoop` 주석)와 받는 B(`dosing.decide()` docstring·`Decision.fraction`·`min_fraction`) 양쪽이 각자 옛 의미를 들고 있었고, 서로 물어본 뒤에야 둘 다 찾았다. 어긋남은 파일 **사이**에 있으므로 한쪽만 보면 못 잡는다.
- 요청하지 않은 `git push`, PR 생성은 하지 않는다.

## 프로젝트 구조

- `gmp_interfaces` 는 rosidl 전용 `ament_cmake` 패키지. 알고리즘·노드·장치 코드를 두지 않는다.
- 앱 패키지는 담당 단위 `ament_python` (`gmp_skills`, `gmp_dosing`, `gmp_process`, `gmp_hmi`). 각 패키지 안은
  `nodes/`(rclpy 통신·호출 순서) · `core/`(ROS 비의존 알고리즘) · `adapters/`(장치 연결) 로 나눈다. `core/` 는 단위 테스트 대상이다.
- 노드 간 데이터는 직접 호출이 아니라 합의된 ROS2 인터페이스로만 전달한다.
- **로봇을 만지는 노드는 `gmp_skills/skill_node` 하나뿐이다.** 다른 어떤 노드도 `DSR_ROBOT2` 를 import 하거나 `/onrobot/*` 를 직접 부르지 않는다.
  로봇에 시키고 싶은 일은 전부 스킬 Action/Service 로 요청한다. 발행자가 둘이면 로그만 보고 누가 움직였는지 가릴 수 없다.
- **안전 계층 원칙:** 힘 상한·충돌 감지·안전 자세 복귀는 `skill_node` 와 두산 컨트롤러만으로 성립한다. HMI·기록·공정 노드가 죽어도
  로봇은 멈추거나 안전 자세로 갈 수 있어야 한다. 사람 감지는 비전이 아니라 **힘(PFL)** 으로만 한다 (R2·R23).
- **DB 에 쓰는 노드는 `record_node` 하나다.** HMI 는 읽기만, `events` 는 append-only — 감사 추적은 쓰는 쪽이 하나일 때만 성립한다.
- 대용량 기록·영상·`records/*.db` 는 커밋하지 않는다 (Drive). 레시피·스테이션 yaml·보정값·`schema.sql` 은 커밋한다.

## 코드 규칙

- **`DSR_ROBOT2` 호출은 콜백 밖 워커 스레드 한 곳에서만 한다.** 라이브러리가 모든 호출에서
  `rclpy.spin_until_future_complete(g_node, …)` 로 자기 노드를 직접 spin 하므로, DSR 노드(`dsr01` 네임스페이스)는
  **executor 에 넣지 않는다.** Action 콜백은 요청을 큐에 넣고 기다릴 뿐이다 (`gmp_skills/nodes/skill_node.py` 참조).
  콜백 안에서 부르면 `Executor is already spinning` 이거나 조용히 멈춘다.
- `DSR_ROBOT2` import 는 `DR_init.__dsr__node` 를 채운 **뒤에** 한다 (두산 공식 튜토리얼 Caution).
- 힘제어 진입/해제는 반드시 짝을 맞춘다 — `task_compliance_ctrl → set_desired_force → … → release_force → release_compliance_ctrl`.
  예외·취소 경로에서도 해제한다 (`finally`). 해제를 빠뜨리면 다음 동작이 이상하게 흘러간다.
- 함수 하나는 하나의 책임. 변경 가능한 실행 값은 ROS2 파라미터로. 주석·TODO 는 한국어.
- 시각은 `get_clock().now()` 하나만 쓴다. 배치 기록·CSV 에 벽시계를 섞지 않는다.
- 시간 상수는 프레임 수가 아니라 초로 적고 파라미터로 노출한다.

## 커밋 · 브랜치 · PR

**커밋 메시지** — `타입: 한국어 요약` (요약 60자 이내, 마침표 없음). 타입: `feat` `fix` `docs` `refactor` `test` `chore` `merge`.
한 커밋은 한 가지 일만. 코드와 문서가 같이 바뀌어야 하는 **계약 변경만** 예외로 한 커밋에 넣고 `feat`/`fix` 를 쓴다. 본문에는 **왜**를 적는다.

**브랜치** — `feature/<주제>` · `fix/<주제>` · `docs/<주제>`. `main` 에 직접 밀지 않는다.
예외 — 조장의 문서 커밋(`docs/`, AGENTS.md, PROJECT.md, README, `tools/`)은 `main` 에 바로 넣는다. 코드와 계약은 예외가 아니다.

**교차검수 — PM 은 없다. 서로 본다.**

| 패키지 | 주 담당 | 리뷰어 (교차) | 왜 그 사람인가 |
|---|---|---|---|
| `gmp_skills` | A | **C** | 공정이 스킬을 부르는 쪽 — 인터페이스 의미가 맞는지 |
| `gmp_dosing` | B | **A** | 측정값을 만드는 쪽 — 단위·부호·분해능 가정 |
| `gmp_process` | C | **D** | 상태를 화면에 보이는 쪽 — 상태 이름·전이가 표시와 맞는지 |
| `gmp_hmi` | D | **B** | 계량값·판정을 기록하는 쪽 — 기록 스키마가 계약과 맞는지 |
| `gmp_interfaces` · `gmp_bringup` · `docs/` | 조장 | **영향받는 담당 전원** (계약은 최소 2명 승인) | 계약은 모두의 전제 |

- 리뷰어가 부재면 **부담당 짝**(A↔C, B↔D)이 대신 본다 — 짝은 서로의 패키지를 읽어둔다.
- 리뷰는 24시간 안에. 통합 2일(9/22~23)에는 **당일**. 막히면 조장이 판단하고 `docs/SOT.md` 에 근거를 남긴다.
- 자기 PR 을 자기가 머지하지 않는다. 승인 없는 머지는 되돌린다.

## 문서

- `docs/*.md` 에는 버전을 붙이지 않는다 (git 이 이력). **예외는 계약 문서 `docs/interfaces.md`** — `계약 v1.0` 처럼 명시하고 바뀌면 올린다.
- BRD·SDD 는 `docs/spec/` 에 **버전마다 새 파일**로 둔다 (`docs/spec/README.md`). 노션은 사본이다.
- 패키지 구조·기술 방향·확정 파라미터가 바뀌면 `docs/SOT.md`. 계약이 바뀌면 `docs/interfaces.md` 와 `gmp_interfaces` 를 함께.
- 규칙(강사 지침·평가 기준·장비 제약)이 바뀌면 `PROJECT_RULES.md` 의 원장에 R번호로 먼저 적고, 설계 영향은 SOT 로 옮긴다.

## 이슈 · 진행

- **9/21 부터 할 일·이슈의 정본은 GitHub Issues 다** (https://github.com/Jik-Kim/auto-pharmacist/issues). 조장 승인. `docs/todo.md`·`docs/issues.md` 는 9/21 상태로 동결한 스냅샷이며 더 고치지 않는다 — 한 파일을 여러 브랜치가 건드려 병합마다 충돌했고, 조장 혼자 관리하기에도 무거웠다. 115개 항목을 전부 Issue 로 옮겼다(완료 항목은 Closed). `tools/todo_stats.py` 도 더 쓰지 않는다.
- 라벨: `severity:high/medium/low` · `pkg:gmp_*` · `overdue` · `source:consistency-check`. 원인 하나에 이슈 하나. 코드 PR 은 본문에 `Closes #N` 또는 `Refs #N` 으로 잇는다.
- 등록·종료는 조장 판단을 거친다. 다른 담당은 발견한 것을 PR 설명·리뷰 코멘트·팀 채널로 조장에게 전달하고, 조장이 판단하면 Issue 가 만들어지거나 닫힌다. 옛 `I-0xx` 번호는 Issue 제목에 남아 있으니 문서에서 `I-0xx` 를 보면 그 제목으로 찾는다.
- 이슈를 닫을 때 같은 원인의 할 일 Issue 도 함께 닫는다. 반대도 마찬가지.

## 검증과 완료 보고

- 변경 범위에 맞는 가장 작은 검증부터: `python3 -m py_compile`, `core/` 단위 테스트(`pytest`), 패키지 구조 변경 시 `colcon build --symlink-install`.
- 가상 모드(에뮬레이터)로 되는 것과 실물로만 되는 것을 구분해 보고한다. **힘 측정·파지력·안전 스위치는 실물로만 검증된다.**
- 검증하지 못한 항목은 성공한 것처럼 쓰지 않는다. 완료 시 변경 내용, 검증 결과, 남은 TODO 를 간결하게 보고한다.
