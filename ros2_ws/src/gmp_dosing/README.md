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

## 영점 재작업 완료 (2026-09-21)

9/18·9/19 G1 측정은 **폐기했다** (`records/deprecated/scale_20260921/`). 거기서 나온
`gain 0.8859` · `offset_g 247.091` · `min_resolvable_g 19.0` · `max_std_g 10.0` 은 전부 무효다.

다시 잰 결과 — **`gain 1.03` · `min_resolvable_g 5.0` · `max_std_g 8.0`** (material_3 자세, 운영 조건
`samples` 20). 폐기 전에는 레시피 셋 다 판정 불가였는데 이제 A·B·C 모두 통과한다 (C 는 경계).
근거 CSV 는 `calibration/`, 요약은 `config/scale_reference.yaml`, 측정 과정과 그때 알아낸 것은
`calibration/README.md` 에 있다.

**⚠️ 이 값은 자세에 딸린다.** material_1·2 에서는 같은 조건에서 회차 평균 σ 가 5~9배 크고, 계량 창을
두 배로 늘려도 못 따라온다. 계량을 어느 자세에서 할지는 조장·A 와 정할 문제다.

**`common.yaml` 의 `scale.*` 는 아직 폐기된 값이다** — 조장·A 소관이라 B 가 바꾸지 않았다.
갱신안은 `calibration/README.md` 에 있고, 적용 전까지 운영은 옛 값으로 돈다.
