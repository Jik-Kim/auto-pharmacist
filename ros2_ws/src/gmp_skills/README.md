# gmp_skills — 로봇 스킬 서버 [A 스킬]

**로봇을 만지는 유일한 노드.** 다른 노드는 `DSR_ROBOT2` 를 import 하지도, `/onrobot/*` 를 부르지도 않는다.
계약은 [docs/interfaces.md](../../../docs/interfaces.md) 1절, 스레드 구조는 [docs/SOT.md](../../../docs/SOT.md) D-02, 교육 요구 명령어 대응표는 SOT 맨 아래.

## 구조

`skill_node:main` 진입점과 Action/Service 계약은 유지한다. 아래 `execution/`은
노드를 추가하지 않으며, 기존 단일 `dsr-worker` 안에서 호출되는 실행 객체들이다.
모든 실행 객체는 하나의 `ExecutionContext`를 공유한다. 위치·파지·안전·큐 상태는
`SkillState`, 초기화 설정은 `SkillConfig`에 둔다. 노드 전체를 실행 객체에 넘기지 않고
장치·파라미터 조회·ROS 시계·로그·이벤트 발행 콜백만 주입한다.


```text
nodes/skill_node.py       rclpy 노드(ns cell) — Action/Service 서버, gripper_state 10 Hz. 콜백은 Job 을 큐에 넣고 기다린다
execution/runtime.py     Job 큐·단일 워커·기동/종료, handlers로 실행 객체 선택
execution/safety.py      안전 감시·차단·복구·넛지 관측
execution/motion.py      스테이션 이동·그리퍼 개폐·파지/도착 조건
execution/scooping.py    고정/높이 보정 스쿠핑·붓기·원료 반환
execution/weighing.py    용기/스쿱 계량·WeightReading 구성
execution/context.py    공통 설정·공유 상태·장치 및 ROS 콜백 의존성
adapters/dsr_arm.py       DR_init 노드(ns dsr01) 소유. DSR_ROBOT2 블로킹 함수 래핑. 워커 스레드에서만 부른다
adapters/rg2_gripper.py   /onrobot/sendCommand + 폭 피드백. 백엔드 modbus | dio | virtual
core/stations.py          stations.yaml 파싱, 접근점 계산 (ROS 비의존)
core/transfer.py          관절 이송 티칭값·출발 관절 구성·파지 조건·ZYZ 자세 검증 (ROS 비의존)
```

> **9/23 현행:** 실물 DIO 개폐·DI 완료, A/B/C 고정 full 경로를 사용합니다.
> `calibrated=false`는 높이 보정만 차단합니다. 과거 sol=3·높이 보정 설명보다
> [현행 운용·B/C 인계](../../../docs/setup.md#drl-고정-경로dio-운용-923-사용자-승인)를 우선합니다.

## 불변식

- `DsrArm` 메서드는 **워커 스레드에서만** 부른다. 콜백에서 부르면 멈춘다 (D-02).
- 힘제어 진입/해제는 짝이다. 내부 `check_depth` 동작의 `finally` 에 `release_force(); release_compliance_ctrl()`.
- 좌표를 코드에 적지 않는다 — `stations.yaml`.
- 네 용기 스테이션은 `solution_space: 3`으로 ABOVE까지 `amovejx`, AT 접근은 직선으로 처리한다. 같은 위치의 상승·하강 전후에 sol을 확인한다. 실물은 남은 `stations.yaml: transfers` 이송을 미티칭/비활성일 때 거부한다. 가상에서 이 transfers 경로만 기존 직선 이동을 사용한다. 티칭 목록과 공정 담당 인계는 [관절 이송 안내](../../../docs/setup.md#관절-이송-티칭인계)를 따른다.
- `SafePose` 는 어떤 상태에서도 받아들인다. 큐를 비우고 현재 Job 에 취소 플래그를 세운 뒤 안전 자세로 간다.

## Scoop과 내부 check_depth

외부 `/cell/scoop` Action과 요청·피드백·결과는 기존 계약을 유지한다.
`_do_scoop`은 현재 `_do_check_depth`를 호출한다. `check_depth`는 원료 계량 자세
`material_N.posx`에서 `material_N.measure_posx`로 비동기 직선 이동하며
접촉 위치·삽입 깊이를 읽고, 정상 도착 후 계량 자세로 복귀한다.
목표의 XYZ·회전을 모두 사용하고 순응 제어를 유지한다. 고정 Z 힘 명령은 보내지 않으며,
이전의 ABOVE 접근·상대 40 mm 담그기·5초 종료는 사용하지 않는다.
순응 진입 반환값을 기록하고 성공(0)일 때만 `safety.compliance_settle_s`(0.5초)를
기다린 뒤 이동한다. 이 대기는 취소·안전 차단을 확인하며, 실패 시 이동하지 않고 해제한다.
9/21 실물에서 성공 응답 직후 이동은 미진행했으나 0.5초 대기 후 A 측정·복귀가 성공했다.
대기는 진입 상태를 직접 측정한 보장이 아니며, 다른 원료·접촉 조건 검증은 별도다.
이동 제한은 기존 `robot.motion_timeout_s`다. 취소·관측 실패 시 정지를 요청하고
순응을 해제하며 자동 복귀하지 않는다. 접촉 판정은 기존 `safety.fz_max_n`을 사용한다.
이동 완료는 정지 상태와 실제 목표 위치·회전을 함께 확인한다. 시작 지연을 일정 시간만으로
완료 처리하지 않으며 미도달이 지속되면 시간초과로 정지를 요청한다.
삽입 깊이는 최초 접촉 이후 BASE Z 변화량이며 경로 길이나 잔량 환산값이 아니다.
이 절의 기존 「실제 스쿠핑·`depth_fraction` 반영 미구현」 상태는 아래
**높이 기반 스쿠핑**(9/22) 구현으로 대체됐다. 외부 작업명·오류 이벤트는 유지하며,
`calibrated=false` 차단과 새 경로의 실물 미검증 상태도 유지한다.

## 실물 첫날 게이트

재기동 시 인출 완료 스쿱만 복원하는 절차는 [setup](../../../docs/setup.md#인출-완료-스쿱의-기동-시-복원)을 따른다.

`docs/demo_run_procedure.md` 1절 G1~G4. **G1(분해능)이 먼저다.**

## 충돌 감도 자가진단 (#76)

실물 기동·안전 복구에서 툴/TCP와 `safety.collision_sensitivity`(50%)를 확인한다.
`DsrArm`의 단일 워커가 `gmp_dsr_controller`의 읽기 전용 서비스로 조회하며
준비·응답은 각각 `robot.startup_timeout_s`로 제한한다. 불일치·실패는 차단하고 자동 변경하지 않는다.
가상에서는 실물 검증 생략을 명시한다. 새 서비스 정의와 플러그인 빌드가 필요하며,
SDK 호출 자체의 강제 취소 및 실제 충돌 성능 검증은 이 검사에 포함되지 않는다.

### 높이 기반 스쿠핑

`core/scooping.py`가 접촉 자세와 스쿱 끝 오프셋으로 WORLD 표면 높이를 계산하고 TW spline을 Z 보정한다. `Scoop.depth_fraction=1`은 원료 A 기준 순량65 g에 대응하는 기준 깊이이며 실제 질량 보장은 아니다. `stations.yaml:scooping.A.calibrated=false`에서는 이동 전 거부한다. 제공 근사 치수와 실측의 차이를 보정해야 한다. `amovesx` 완료·취소 감시, 털기·계량 복귀를 구현했으며 B/C·반환 후 재스쿱은 미검증이다. 레시피 연동 인계는 `docs/setup.md` 참조.

현재는 힘 측정이 신뢰되지 않고 파지부에서 스쿱이 상대 회전하므로 최초 접촉 정지와 원료 높이 측정 경로를 운영에서 비활성화한다. 관련 설정·파라미터와 단위 테스트만 유지하되 `calibrated=false`를 해제하지 않고, 전체 노드 통합과 공정 플로우 검증을 우선한다.

## 실제 호출 위치를 찾는 순서

1. `nodes/skill_node.py`의 `_exec_*`/`_srv_*`가 요청을 받아
   `execution.runtime._submit()`으로 작업을 넣고 결과를 기다린다.
2. `execution/runtime.py`의 `_worker()`가 큐를 직렬 처리하고 `handlers`에서
   작업 종류에 대응하는 메서드를 선택한다.
3. 예를 들어 `scoop`은 `ScoopingSkills._do_scoop()` → `_do_fixed_scoop()` →
   `ctx.arm.movesx_cancellable()` 순서로 실행한다. 그리퍼는
   `MotionSkills._do_grip()` → `ctx.gripper.grip()/release()`로 실행한다.
4. 실제 두산 API 호출은 `adapters/dsr_arm.py`, DIO 개폐·완료 판정은
   `adapters/rg2_gripper.py`에 있다. Job 완료 후 ROS 콜백이 결과를 반환한다.

스쿱 파지·인출/계량·스쿠핑·붓기는 각각 별도 요청이다. 전체 배치 순서는
C의 `process_fsm.py`와 `process_node.py`가 결정한다. 내부 실행 메서드를 다른 노드에서
직접 호출하지 않는다. `core/`는 ROS 비의존 계산/검증, `execution/`은 상태 있는
장치 동작 순서, `nodes/`는 ROS 입출력을 담당한다.


## 명시적 이동과 주문 시작 준비 (2026-09-28)

- `motion.py`의 호출부에서 `movej_cancellable`(MOVEJ/관절각),
  `movel_cancellable`(MOVEL/TCP 직선), `movejx_cancellable`(MOVEJX/TCP 목표 관절 이동)을
  직접 확인한다. 스쿠핑 스플라인은 `movesx_cancellable`(MOVESX)이다.
- `_taught_linear` 및 `cartesian_ready`는 제거했다. 일반 직선 이동 요청에
  안전 자세 MOVEJ를 자동으로 끼워 넣지 않는다. `_motion_scale`은 배율 검증만 한다.
- 정상 주문 준비는 기존 `SafePose` 요청으로 `safe.posj`에 명시적으로 이동한다.
  노드 기동/스쿱 복원에서는 이동하지 않는다. SafePose 속도 배율 0.3은 기존대로 유지한다.
  요청을 시작하면 이전 스테이션의 출발 자세 이력을 무효화하며, 실패·시간 초과 뒤에도 옛 이력을 재사용하지 않는다.
- **C 연결 후 순서:** 첫 주문은 안전 자세 성공 확인 → 빈 통 접근·파지.
  후속 주문은 기존 주문 경계 넛지 대기 완료 → 안전 자세 성공 확인 → 빈 통 접근·파지.
  첫 주문에 넛지를 추가하지 않으며 일반 PAUSED 재개에 초기화를 삽입하지 않는다.
- **현재 C에는 이 연결이 아직 없다.** `process_fsm.start()`/`SELF_CHECK` 뒤
  `PICK_CONTAINER`로 진행하기 전에 safe 성공을 기다리는 단계를 C가 추가해야 한다.
  실패·취소 시 빈 통 이송을 요청하지 않아야 한다. 중간 스쿱/도징 테스트는
  정상 주문 시작과 분리한다. ROS 계약 변경은 없다.
