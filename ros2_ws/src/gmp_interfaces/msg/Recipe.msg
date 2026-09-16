# 배치 1건. HMI 가 params/recipes 의 yaml 을 읽어 SubmitOrder 로 보낸다
std_msgs/Header header
string batch_id         # 비어 있으면 process_node 가 채운다 (B-YYYYMMDD-NNN)
string product          # 표시용 제품명
RecipeItem[] items      # 투입 순서 = 배열 순서. 상태기계는 이 순서를 바꾸지 않는다
