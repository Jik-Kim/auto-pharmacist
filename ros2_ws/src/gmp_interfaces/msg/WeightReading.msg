# 1회 계량. 그램 환산·영점은 gmp_dosing/core/scale.py 단일 출처 (계약 2절)
std_msgs/Header header
float32 gross_g         # 영점 보정 전 [g]
float32 tare_g          # 같은 계량 자세·파지 조건의 기준값 [g] (용기면 빈 용기, 스쿱이면 빈 스쿱)
float32 net_g           # gross - tare [g]
float32 std_g           # 표본 표준편차 [g] — 분해능 근거
uint16 samples
bool valid              # false 면 값을 쓰지 않는다 (정착 실패·힘 조회 실패·로봇 이동 중)
string station          # 계량한 자세 (보통 "workbench") — 스쿱도 용기도 같은 자세라 이것으로는 구분되지 않는다
string subject          # 무엇을 쟀는가: "scoop"(WeighHeld) | "container"(WeighContainer). 빈 문자열이면 미상 (v1.2)
