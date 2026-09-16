# skill_node 10 Hz. 파지는 추론이다 (SOT D-05) — 드라이버가 grip 비트를 토픽으로 내지 않는다
std_msgs/Header header
float32 width_mm        # 핑거 안쪽 간격
bool busy
bool grip_inferred      # 정지 폭 > 목표 폭 + grip_margin_mm
bool safety_triggered   # 안전 스위치 (dio 백엔드나 I-003 패치가 있을 때만 참값, 아니면 항상 false)
float32 force_cmd_n     # 어댑터가 추적하는 명령 파지력
string backend          # "modbus" | "dio" | "virtual"
