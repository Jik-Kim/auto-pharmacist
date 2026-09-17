# 터미널마다: source tools/env.sh   (/opt/ros → 언더레이 ws_dsr → 오버레이 auto-pharmacist)
ENV_SH_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

source /opt/ros/jazzy/setup.bash
source "$ENV_SH_DIR/../../ws_cobot_pjt/ws_dsr/install/setup.bash"
[ -f "$ENV_SH_DIR/../ros2_ws/install/setup.bash" ] && source "$ENV_SH_DIR/../ros2_ws/install/setup.bash"
