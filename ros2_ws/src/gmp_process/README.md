# gmp_process — 공정 오케스트레이션 [C 공정]

`core/process_fsm.py` 가 상태와 전이를 갖고, `nodes/process_node.py` 는 그 결정을 스킬 Action/Service 호출로 옮긴다.
전이표는 [docs/architecture.md](../../../docs/architecture.md) 「공정 사이클 ↔ 노드」와 **같은 것**이어야 한다 (SDD 5장도).

## 원칙

- 도징 판정은 `gmp_dosing.core.dosing.decide()` 가 한다. 여기서는 판정하지 않는다.
- 일탈은 `core/deviation.py` 의 규칙표로 처리한다 — kind 별로 「재시도 / 보충 요청(인터락) / QA 판정 / 강제 개입」 중 하나.
- 인터락 `ENTER` 는 스킬 `SafePose` 가 **성공한 뒤**에야 `granted`. 로봇이 아직 움직이는데 사람을 들이지 않는다.
- 스킬 호출은 **한 번에 하나**. 이전 Action 결과가 오기 전에 다음을 보내지 않는다 (skill_node 워커도 직렬이지만, 여기서도 지킨다).
