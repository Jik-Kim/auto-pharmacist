# 원료 1종 분주 결과. 계약 2절 판정 규칙
std_msgs/Header header
string batch_id
string material_id
float32 target_g
float32 actual_g
float32 error_pct       # (actual - target) / target * 100
uint8 verdict           # 아래 상수
uint8 attempts          # 보정 투입 포함 시도 횟수
float32 duration_s

uint8 OK=0
uint8 UNDER=1           # 보정 투입으로 이어진다 — 최종 결과에는 남지 않는다 (OK 또는 일탈로 끝난다)
uint8 OVER=2            # 되돌릴 수 없다 → Deviation(OVERFILL)
uint8 INVALID=3         # v1.8 — 계량 무효를 QA 가 승인해 **투입량을 모르는 채** 끝난 원료.
                        # UNDER 와 다르다: 「모자랐다」가 아니라 **얼마가 들어갔는지 모른다**.
                        # `actual_g` 는 미측정 사이클이 빠져 실제보다 작으므로 목표와 비교하지 말 것
                        # (`error_pct` 도 같은 이유로 의미가 없다).
                        # 배치 결과는 `DONE_UNMEASURED`, 사이클별 근거는 `ScoopCycle.valid`,
                        # 계량별 근거는 `weights.valid` 에 있다. `CellEvent(WARN, DISPENSE_UNMEASURED)` 동반.
