# gmp_dosing — 도징 라이브러리 [B 도징]

**ROS 를 import 하지 않는다.** `gmp_process` 가 import 해서 쓴다. 로봇 없이 `pytest` 로 완성한다.

| 파일 | 하는 일 |
|---|---|
| `core/scale.py` | 측정값(N 또는 kg) → g. 영점(tare) 저장·적용, 표준편차로 `valid` 판정, 선형 보정 `gain/offset` |
| `core/dosing.py` | `decide(target, actual, tol_pct, attempts, …)` → 다음 행동. 계약 2절 판정 규칙 그대로 |
| `core/calib.py` | 실측 CSV → offset·σ·3σ·표본 중복률. `python3 -m gmp_dosing.core.calib <csv>` |
| `calibration/measure_g1.py` | 실물 측정 도구 — 로봇을 안 움직이고 tool_force·workpiece 를 같은 표본에서 기록. `--help` |
| `calibration/*.csv` | G1 원시 측정 (9/18, tool_force, 133 g 스쿱 6세트×30회×10표본) |
| `config/scale_reference.yaml` | 위 CSV 의 요약 — 코드가 읽지 않는다. 테스트가 CSV·yaml·기본값의 정합을 검사한다 |

값의 단일 출처는 `gmp_bringup/params/common.yaml` 의 `scale.*`·`dosing.*` — `process_node` 가 읽어서 생성자에 넣는다.
**G1(9/18) 결과**: 계량 한 번의 3σ = 18.0 g → `min_resolvable_g = 19`. `offset_g` 는 method 에 종속이라 기본값은 0 이고 common.yaml 이 넣는다.
