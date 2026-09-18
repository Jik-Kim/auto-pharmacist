# 원료 1종. 배열 안의 순서가 투입 순서다 (Recipe.items). 계약 1절
string material_id      # 원료 ID. 전용 스쿱 매핑은 셀 설정이 관리한다
float32 target_g        # 목표 순량 [g]
float32 tol_pct         # 허용 오차 [%]. 레시피 yaml이 직접 지정한다
