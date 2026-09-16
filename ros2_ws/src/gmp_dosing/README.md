# gmp_dosing — 도징 라이브러리 [B 도징]

**ROS 를 import 하지 않는다.** `gmp_process` 가 import 해서 쓴다. 로봇 없이 `pytest` 로 완성한다.

| 파일 | 하는 일 |
|---|---|
| `core/scale.py` | 측정값(N 또는 kg) → g. 영점(tare) 저장·적용, 표준편차로 `valid` 판정, 선형 보정 `gain/offset` |
| `core/dosing.py` | `decide(target, actual, tol_pct, attempts, …)` → 다음 행동. 계약 2절 판정 규칙 그대로 |

값의 단일 출처는 `gmp_bringup/params/common.yaml` 의 `scale.*`·`dosing.*` — `process_node` 가 읽어서 생성자에 넣는다.
**G1(9/17) 결과로 `min_resolvable_g` 를 정한다** — 그 전까지 30 g 은 가정이다.
