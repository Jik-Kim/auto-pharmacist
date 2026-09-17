# 사용법: 저장소 루트에서 source tools/env.sh
# source 순서: ROS 2 기본 환경 → Doosan 언더레이 → auto-pharmacist 오버레이
# env.sh 파일의 위치를 기준으로 경로를 계산하므로 실행 위치와 무관하게 동작한다.
ENV_SH_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

export ROS_DOMAIN_ID=70   # 조 공용 도메인 (9/16 확정). 다른 조와 겹치지 않게 전원 동일
source /opt/ros/jazzy/setup.bash   # ROS 2 기본 환경

# 먼저 저장소 구조상 예상 위치에서 ws_dsr 언더레이를 찾는다.
WS_DSR_SETUP="$ENV_SH_DIR/../../ws_cobot_pjt/ws_dsr/install/setup.bash"
if [ ! -f "$WS_DSR_SETUP" ]; then
    # 저장소를 다른 위치에 둔 경우 팀원별 홈 디렉터리의 workspace를 사용한다.
    WS_DSR_SETUP="$HOME/ws_cobot_pjt/ws_dsr/install/setup.bash"
fi

# 언더레이가 없으면 이후 환경 설정이 올바르지 않으므로 source를 중단한다.
if [ ! -f "$WS_DSR_SETUP" ]; then
    echo "ws_dsr setup.bash를 찾을 수 없습니다: $WS_DSR_SETUP" >&2
    return 1
fi

source "$WS_DSR_SETUP"   # Doosan 패키지 언더레이

# 현재 프로젝트의 ROS 2 workspace를 오버레이한다. 아직 빌드 전이면 건너뛴다.
OVERLAY_SETUP="$ENV_SH_DIR/../ros2_ws/install/setup.bash"
if [ -f "$OVERLAY_SETUP" ]; then
    source "$OVERLAY_SETUP"
fi
