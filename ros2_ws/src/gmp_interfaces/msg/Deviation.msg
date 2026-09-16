# 일탈 1건. 자동 복구된 것도 기록한다 — 「기능 동작 지속성」의 근거 (규칙 원장 3-8)
std_msgs/Header header
string deviation_id     # D-<batch>-<n>
string batch_id
string material_id      # 원료와 무관하면 빈 문자열
uint8 kind              # 아래 상수
string detail
bool requires_decision  # true 면 QA 판정을 기다린다 (CellState.DEVIATION)
uint8 decision          # 아래 상수
string operator_id      # 판정자 (QaDecision.operator_id 를 process 가 옮겨 담는다). 감사 추적용 — v1.1

uint8 OVERFILL=0        # 초과 투입
uint8 GRIP_FAIL=1       # 파지 실패 (폭 추론)
uint8 SLIP=2            # 미끄러짐 (작업 중 폭 변화)
uint8 SAFETY_SWITCH=3   # 그리퍼 안전 스위치
uint8 SCOOP_EMPTY=4     # 퍼올렸는데 무게 변화 없음
uint8 MATERIAL_EMPTY=5  # 원료 소진 (SCOOP_EMPTY 연속)
uint8 FORCE_LIMIT=6     # 힘 상한·충돌 감지
uint8 TIMEOUT=7         # 재시도 상한 초과
uint8 WEIGH_INVALID=8   # 계량 무효 반복

uint8 PENDING=0
uint8 APPROVED=1        # QA 가 계속 진행 승인
uint8 DISCARDED=2       # QA 가 배치 폐기
uint8 AUTO_RECOVERED=3  # 사람 없이 복구됨
