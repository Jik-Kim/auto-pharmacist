# 터미널마다: source ~/auto-pharmacist/tools/env.sh   (/opt/ros → 언더레이 ws_dsr → 오버레이 auto-pharmacist)
source /opt/ros/jazzy/setup.bash
source "$HOME/ws_cobot_pjt/ws_dsr/install/setup.bash"
[ -f "$HOME/auto-pharmacist/ros2_ws/install/setup.bash" ] && source "$HOME/auto-pharmacist/ros2_ws/install/setup.bash"
