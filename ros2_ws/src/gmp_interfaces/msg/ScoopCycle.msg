# 스쿠핑 1회 시도에 대한 공정·학습 기록.
# process_node 가 시도가 성공·실패로 종료될 때 1건 발행한다.
# 정상은 WEIGH_RESIDUAL 후, 실패는 실패가 확정된 단계에서 발행한다.
std_msgs/Header header
string batch_id
string material_id
uint8 attempt                   # 원료별 1부터 시작하는 시도 번호

float32 target_g                # 원료 전체 목표량 [g]
float32 actual_before_g         # 이 시도 전까지 누적 투입량 [g]
gmp_interfaces/WeightReading scoop_tare # 빈 스쿱 기준 측정
gmp_interfaces/WeightReading pre_pour   # scoop_tare와 같은 자세·파지·영점의 붓기 전 측정
gmp_interfaces/WeightReading post_pour  # scoop_tare와 같은 자세·파지·영점의 붓기 후 측정
float32 commanded_pour_fraction # Pour 에 전달한 명령 비율 [0~1]
float32 delivered_g             # RETURNED/RETURN_FAILED는 0, 그 외 max(0, pre_pour.net_g - post_pour.net_g) [g]. 음수 원시차는 두 reading으로 보존

uint8 weigh_method              # 스쿱 계량 방식. 아래 상수
string weigh_pose_id            # 고정 계량 자세 ID (해당 "material_N")
string tool_name                # 컨트롤러에 적용한 tool 등록명
string tcp_name                 # 컨트롤러에 적용한 TCP 등록명

bool contact_detected
float32 max_contact_force_n     # Scoop 중 검출한 최대 접촉력 [N]
float32 insertion_depth_mm      # 최초 접촉점 이후 최종 삽입 깊이 [mm]
float32 grip_width_mm           # SetGripper 성공 후 정지 폭 [mm]

# 각 WeightReading.header와 같은 표본 구간, 고정 계량 자세, DR_BASE 기준 [Fx,Fy,Fz,Mx,My,Mz].
# 힘 [N], 모멘트 [N·m]. weigh_pose_id는 stations.yaml의 전체 위치·자세를 식별한다.
# CoG 변화와 JTS 계량 편향을 사후 재평가하기 위한 6축 통계 특징이다.
float32[6] tare_wrench
float32[6] pre_pour_wrench
float32[6] post_pour_wrench
float32[6] tare_wrench_std
float32[6] pre_pour_wrench_std
float32[6] post_pour_wrench_std
uint16 tare_wrench_samples
uint16 pre_pour_wrench_samples
uint16 post_pour_wrench_samples
bool tare_wrench_valid
bool pre_pour_wrench_valid
bool post_pour_wrench_valid

# 독립 저울 등 JTS 계량과 다른 기준으로 얻은 감독학습용 정답. 정상 운전에서는 false 일 수 있다.
float32 reference_delivered_g
float32 reference_std_g
string reference_source
bool reference_valid

uint8 outcome
bool valid                      # 성공했고 필수 계량 3개가 모두 유효할 때 true
float32 duration_s              # Scoop 시작부터 시도 종료까지 [s]

uint8 WEIGH_METHOD_UNKNOWN=0
uint8 WEIGH_METHOD_WORKPIECE=1
uint8 WEIGH_METHOD_TOOL_FORCE=2

uint8 COMPLETE=0
uint8 SCOOP_EMPTY=1
uint8 WEIGH_INVALID=2
uint8 POUR_FAILED=3
uint8 ABORTED=4
uint8 RETURNED=5                 # 원료통 반환 동작 완료. 투입량 0, valid=false
uint8 RETURN_FAILED=6            # 원료통 반환 실패. 투입량 0, valid=false
