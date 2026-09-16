# 로그 이벤트. 모든 노드가 발행 가능. record_node 가 배치 기록에 남긴다
std_msgs/Header header
uint8 level
string code             # 짧은 대문자 코드 (예: BATCH_START, GRIP_OK, INTERVENTION_FORCED, INTERLOCK_ENTER)
string text
string batch_id

uint8 INFO=0
uint8 WARN=1
uint8 ERROR=2
