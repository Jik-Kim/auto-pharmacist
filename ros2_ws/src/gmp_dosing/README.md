# gmp_dosing — 도징 라이브러리 [B 도징]

**ROS 를 import 하지 않는다.** `gmp_process` 가 import 해서 쓴다. 로봇 없이 `pytest` 로 완성한다.

| 파일 | 하는 일 |
|---|---|
| `core/scale.py` | 측정값(N 또는 kg) → g. 영점(tare) 저장·적용, 표준편차로 `valid` 판정, 선형 보정 `gain/offset` |
| `core/dosing.py` | `decide(target, actual, tol_pct, attempts, …)` → 다음 행동. 계약 2절 판정 규칙 그대로 |
| `core/calib.py` | 실측 CSV → offset·σ·3σ·표본 중복률. `python3 -m gmp_dosing.core.calib <csv>` |
| `calibration/measure_g1.py` | 실물 측정 도구 — 로봇을 안 움직이고 tool_force·workpiece 를 같은 표본에서 기록. `--help` |
| `calibration/*.csv` | 확정된 원시 측정 — **지금은 비어 있다** (9/21 폐기, 아래 참조). 재측정 확정본을 여기 커밋한다 |
| `config/scale_reference.yaml` | 위 CSV 의 요약 — 코드가 읽지 않는다. 지금은 `status: rezero_pending`(확정값 없음) |

값의 단일 출처는 `gmp_bringup/params/common.yaml` 의 `scale.*`·`dosing.*` — `process_node` 가 읽어서 생성자에 넣는다.
`offset_g` 는 method 에 종속이라 기본값은 0 이고 common.yaml 이 넣는다.

## ⚠️ 영점 재작업 중 (2026-09-21)

**영점(offset)부터 다시 잡는다. 9/18·9/19 G1 측정에서 나온 값은 전부 폐기했다** — `method: tool_force`,
`gain 0.8859`, `offset_g 247.091`, `min_resolvable_g 19.0`, `max_std_g 10.0`, 3σ 12.6 g 이 여기 해당한다.
원시 CSV 두 건은 `records/deprecated/scale_20260921/` 로 옮겼다 (git 밖, 폐기 사유는 그 폴더 README).
`common.yaml` 의 `scale.*` 에는 아직 폐기된 값이 남아 있다 — 조장·A 소관이라 이 브랜치에서는 주석으로만 표시했다.

재측정 전까지는 **계량 정확도에 의존하는 판단을 새로 만들지 않는다** (VERIFY 임계, `min_resolvable_g` 기반 로직).
재측정이 끝나면 `config/scale_reference.yaml` 머리말의 4단계를 따라 값·CSV·정합 테스트를 같이 되살린다.
