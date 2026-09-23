# gmp_skills — 로봇 스킬 서버 [A 스킬]

**로봇을 만지는 유일한 노드.** 다른 노드는 `DSR_ROBOT2` 를 import 하지도, `/onrobot/*` 를 부르지도 않는다.
계약은 [docs/interfaces.md](../../../docs/interfaces.md) 1절, 스레드 구조는 [docs/SOT.md](../../../docs/SOT.md) D-02, 교육 요구 명령어 대응표는 SOT 맨 아래.

## 구조

```text
nodes/skill_node.py       rclpy 노드(ns cell) — Action/Service 서버, gripper_state 10 Hz. 콜백은 Job 을 큐에 넣고 기다린다
adapters/dsr_arm.py       DR_init 노드(ns dsr01) 소유. DSR_ROBOT2 블로킹 함수 래핑. 워커 스레드에서만 부른다
adapters/rg2_gripper.py   /onrobot/sendCommand + 폭 피드백. 백엔드 modbus | dio | virtual
core/stations.py          stations.yaml 파싱, 접근점 계산 (ROS 비의존)
core/transfer.py          관절 이송 티칭값·출발 관절 구성·파지 조건·ZYZ 자세 검증 (ROS 비의존)
```

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
