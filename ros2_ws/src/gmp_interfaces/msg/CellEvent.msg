# 로그 이벤트. 모든 노드가 발행 가능. record_node 가 배치 기록에 남긴다
std_msgs/Header header
uint8 level
string code             # 짧은 대문자 코드 (예: BATCH_START, GRIP_OK, INTERVENTION_FORCED, INTERLOCK_ENTER)
string text
# 안전복구 이벤트 JSON 상관관계 제안(v1.6, PR 리뷰 대상; 필드 레이아웃 불변):
# ROBOT_SAFETY_STOP: robot_state, reason, origin, safety_session, safety_revision.
# origin=recovery_request에만 request_id/operator_id 추가. 실제 알람에는 넣지 않는다.
# ROBOT_SAFETY_RECOVERY: 기존 결과 + safety_session/safety_revision.
# 성공은 현재 복구 요청·정지 세대에만 적용하며 배치를 자동 재개하지 않는다.
string batch_id

uint8 INFO=0
uint8 WARN=1
uint8 ERROR=2
