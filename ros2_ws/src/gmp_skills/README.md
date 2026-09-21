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
- 실물은 `stations.yaml: transfers`에 등록한 이송을 미티칭/비활성일 때 거부한다. 가상 모드는 기존 직선 이동을 사용하고 보호 목적지 제한을 적용하지 않는다. 티칭 목록과 공정 담당 인계는 [관절 이송 안내](../../../docs/setup.md#관절-이송-티칭인계)를 따른다.
- `SafePose` 는 어떤 상태에서도 받아들인다. 큐를 비우고 현재 Job 에 취소 플래그를 세운 뒤 안전 자세로 간다.

## Scoop과 내부 check_depth

외부 `/cell/scoop` Action과 요청·피드백·결과는 기존 계약을 유지한다.
`_do_scoop`은 현재 `_do_check_depth`를 호출한다. `check_depth`는 아래로 누르며
접촉 위치·삽입 깊이를 확인하고 상승하는 동작이며, 실제 원료를 퍼 올리는 스쿠핑은 미구현이다.
이번 분리는 내부 이름만 정리하며 이동·힘제어·설정값·작업명과 오류 이벤트는 유지한다.
`depth_fraction`의 동작 반영도 아직 미구현이다.

## 실물 첫날 게이트

재기동 시 인출 완료 스쿱만 복원하는 절차는 [setup](../../../docs/setup.md#인출-완료-스쿱의-기동-시-복원)을 따른다.

`docs/demo_run_procedure.md` 1절 G1~G4. **G1(분해능)이 먼저다.**
