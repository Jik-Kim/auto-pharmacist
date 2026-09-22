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
uint8 UNDER=1           # 보정 투입으로 이어진다 — 최종 결과에는 남지 않는다 (OK 또는 일탈로 끝난다).
                        # 예외: 계량 무효를 QA 가 승인해 투입량을 모르는 채 끝난 원료는 UNDER 로 남는다.
                        # 「모름」을 담을 열거값이 없어 보수적으로 미달로 보고하는 것이다 (#108 INVALID 상수 전까지).
uint8 OVER=2            # 되돌릴 수 없다 → Deviation(OVERFILL)
