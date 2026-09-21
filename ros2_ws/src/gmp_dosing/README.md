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
그 폴더는 `.gitignore`(records/) 밑이라 git 에서 보이지 않는다 —
**원본은 git 이력 `23fbcc1` 의 `ros2_ws/src/gmp_dosing/calibration/` 에 있다**
(`git show 23fbcc1:ros2_ws/src/gmp_dosing/calibration/g1_scoop_0919_both.csv`).

다시 잰 결과 — **`gain 1.03` · `min_resolvable_g 5.0` · `max_std_g 8.0`** (material_3 자세, 운영 조건
`samples` 20). 폐기 전에는 레시피 셋 다 판정 불가였는데 이제 A·B·C 모두 통과한다 (C 는 경계).
근거 CSV 는 `calibration/`, 요약은 `config/scale_reference.yaml`, 측정 과정과 그때 알아낸 것은
`calibration/README.md` 에 있다.

**⚠️ 이 값은 자세에 딸린다.** material_1·2 에서는 같은 조건에서 회차 평균 σ 가 5~9배 크고, 계량 창을
두 배로 늘려도 못 따라온다.

**9/21 팀 결정(조장 승인): 계량은 한 자세에서만 한다 — 어느 원료든 `material_3` 자세로 가서 잰다.**
원료를 푸는 곳은 그대로 원료별 `material_1/2/3` 이고 계량(`WeighHeld`)만 통일된다. 그래서 위 보정값의
σ 가 원료 A·B·C 전부에 적용된다. 구현은 A(`skill_node`), 그 뒤 조장이 `common.yaml` 갱신.

**런타임 동작 변화는 이 브랜치에 없다.** `common.yaml` 의 `scale.*` 도, `process_node` 의
`declare_parameter` 기본값도 아직 폐기된 값이다 — 각각 조장·A / C 소관이라 B 가 바꾸지 않았다.
갱신안은 `calibration/README.md` 에 있다.

**바꾸지 않은 진짜 이유는 소관만이 아니다.** `skill_node._do_weigh_held` 는 원료마다 자기
`material_N.posx` 에서 재는데, material_1·2 에서는 회차 내부 σ 가 12~28 g 이라 `max_std_g` 가
**8 이든 10 이든 계량이 거의 전부 무효**가 된다:

| 자세 | max_std_g 8.0 | max_std_g 10.0 (현재 운영값) | 회차 내부 σ |
|---|---|---|---|
| material_1 | valid 0/15 | valid 0/15 | 12.4~24.0 g |
| material_2 | 1/15 | 2/15 | 7.2~28.2 g |
| material_3 | 15/15 | 15/15 | 4.5~7.6 g |

즉 원료 A·B 계량은 **지금 값으로도 이미 성립하지 않는다.** 새 값이 그걸 무너뜨리는 게 아니라
이미 무너져 있고, 8.0 은 그 상태를 조금 더 빡빡하게 할 뿐이다.
**계량 자세를 A 가 구현한 뒤에 값을 갱신해야 한다** (위 팀 결정).

## ⚠️ `min_resolvable_g` 를 적용하기 전에 — VERIFY ② 를 함께 고쳐야 한다

`resolvable()` 은 아직 접수 경로에 붙지 않았다(호출처는 테스트뿐) — 죽은 코드가 아니라 **미구현
요구사항**이다(BRD 3.1.3·FR-01, C 가 별도 이슈로 올림). 그래서 지금 `min_resolvable_g` 가 실제로 쓰이는
곳은 `process_fsm` 의 **VERIFY ②** 하나다 — 용기 순량과 스쿱 누적 투입량의 **차이** 임계다. 계량 한 번의
흔들림이 아니라 배치 전체 누적 차이라 필요한 크기가 다르다(√N 으로 커진다).

| 총 스쿱 사이클 | 정상 배치 차이 σ | 임계 5.0 오검출 | 임계 19.0(현재) 오검출 |
|---|---|---|---|
| 3 | 3.78 g | **18.6 %** | 0.00 % |
| 6 | 5.00 g | **31.8 %** | 0.01 % |
| 9 | 5.98 g | **40.3 %** | 0.15 % |

**지금 구조 그대로 5.0 을 적용하면 정상 배치의 20~40 %가 VERIFY_MISMATCH 로 실패한다.**
반대로 현재값 19.0 은 10 g 흘림을 96 % 놓쳐 사실상 작동하지 않는다. 고정값으로는 양쪽을 만족할 수 없다.

제안: 임계를 계량 횟수에서 유도한다 — `tol = k · σ_weigh · √(2n+2)` (n = 총 사이클).

⚠️ **`σ` 는 측정값을 그대로 쓰는 별도 파라미터여야 한다**(`scale.weigh_sigma_g: 1.34`). 처음에
`min_resolvable_g / 3` 으로 유도하는 안을 냈다가 C 지적으로 철회했다 — 5.0 은 레시피 C 경계에 맞춰 올린
값이라 /3 하면 1.67 로 실측보다 24.6 % 크고, 그 여유가 임계에 실려 **N=10 부터 ②가 ① 임계(22.5 g)를
넘어 죽는다.** 데모 레시피는 재시도 없이도 N=12 다.

`k` 는 **3.0 으로 둔다.** 한때 2.5 를 제안했다가 C 반론으로 철회했다 — ②가 죽는 건 큰 배치가 아니라
**작은 배치**이고(①은 총량에 비례, ②는 √총량에 비례), k 를 낮추면 오검출이 4.6배(배치 370건당 1건 →
81건당 1건 헛된 일탈 조사)가 된다. **옳은 레버는 σ 다** — σ 0.89 면 ② 유효 상한이 N=34 가 된다.
근거·수치는 `config/scale_reference.yaml` 의 `verify_mismatch` 절.

**아직 안 잰 σ 가 있다** — 위 1.34 는 스쿱 계량(계량 자세) 값이고, TARE·VERIFY 의 **용기 계량은
`workbench` 자세라 별도 측정이 필요하다**(B 소관).

**수식·`ItemRun.cycles` 는 C, 파라미터 추가는 조장, σ 측정은 B.**
