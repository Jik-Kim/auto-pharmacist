# 1회 계량. 그램 환산·영점은 gmp_dosing/core/scale.py 단일 출처 (계약 2절)
std_msgs/Header header
float32 gross_g         # 영점 보정 전 [g]
float32 tare_g          # 이 자세의 영점 (빈 그리퍼 + 용기) [g]
float32 net_g           # gross - tare [g]
float32 std_g           # 표본 표준편차 [g] — 분해능 근거
uint16 samples
bool valid              # false 면 값을 쓰지 않는다 (정착 실패·힘 조회 실패·로봇 이동 중)
string station          # 계량한 자세 (보통 "scale")
