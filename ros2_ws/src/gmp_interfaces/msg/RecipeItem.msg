# 원료 1종. 배열 안의 순서가 투입 순서다 (Recipe.items). 계약 1절
string material_id      # 원료통·전용 스쿱 ID 와 같다 (stations.yaml material_N 의 material_id)
float32 target_g        # 목표 순량 [g]
float32 tol_pct         # 허용 오차 [%] — ACTIVE 1.0 / EXCIPIENT 5.0 (레시피 yaml 이 준다, 여기서 정하지 않는다)
uint8 grade             # 아래 상수
string scoop_id         # 전용 스쿱 (교차오염 방지, GMP). 비어 있으면 material_id 와 같은 스쿱

uint8 EXCIPIENT=0       # 부형제
uint8 ACTIVE=1          # 주성분
