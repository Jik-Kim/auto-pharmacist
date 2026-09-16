# 공정 상태. process_node 단독 발행, 2 Hz + 변화 시. TRANSIENT_LOCAL
std_msgs/Header header
uint8 mode              # 아래 상수
string batch_id
string step             # 상태기계 상태 이름 (core/process_fsm.py 의 State 값 그대로)
uint8 item_index        # 현재 원료 인덱스 (0 부터)
string station          # 로봇이 마지막으로 도착한 스테이션 ID
string note             # 사람이 읽을 한 줄

uint8 IDLE=0
uint8 RUNNING=1
uint8 PAUSED=2          # 인터락으로 사람 투입 중 — 로봇은 안전 자세
uint8 DEVIATION=3       # QA 판정 대기
uint8 ERROR=4           # 자동 복구 실패, 강제 개입 필요
uint8 DONE=5
