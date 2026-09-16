# gmp_skills — 로봇 스킬 서버 [A 스킬]

**로봇을 만지는 유일한 노드.** 다른 노드는 `DSR_ROBOT2` 를 import 하지도, `/onrobot/*` 를 부르지도 않는다.
계약은 [docs/interfaces.md](../../../docs/interfaces.md) 1절, 스레드 구조는 [docs/SOT.md](../../../docs/SOT.md) D-02, 교육 요구 명령어 대응표는 SOT 맨 아래.

## 구조

```text
nodes/skill_node.py       rclpy 노드(ns cell) — Action/Service 서버, gripper_state 10 Hz. 콜백은 Job 을 큐에 넣고 기다린다
adapters/dsr_arm.py       DR_init 노드(ns dsr01) 소유. DSR_ROBOT2 블로킹 함수 래핑. 워커 스레드에서만 부른다
adapters/rg2_gripper.py   /onrobot/sendCommand + 폭 피드백. 백엔드 modbus | dio | virtual
core/stations.py          stations.yaml 파싱, 접근점 계산 (ROS 비의존)
```

## 불변식

- `DsrArm` 메서드는 **워커 스레드에서만** 부른다. 콜백에서 부르면 멈춘다 (D-02).
- 힘제어 진입/해제는 짝이다. `Scoop` 의 `finally` 에 `release_force(); release_compliance_ctrl()`.
- 좌표를 코드에 적지 않는다 — `stations.yaml`.
- `SafePose` 는 어떤 상태에서도 받아들인다. 큐를 비우고 현재 Job 에 취소 플래그를 세운 뒤 안전 자세로 간다.

## 실물 첫날 게이트

`docs/demo_run_procedure.md` 1절 G1~G4. **G1(분해능)이 먼저다.**
