# 터미널마다: source ~/auto-pharmacist/tools/env.sh   (/opt/ros → 언더레이 ws_dsr → 오버레이 auto-pharmacist)
export ROS_DOMAIN_ID=70   # 조 공용 도메인 (9/16 확정). 다른 조와 겹치지 않게 전원 동일
source /opt/ros/jazzy/setup.bash
source "$HOME/ws_cobot_pjt/ws_dsr/install/setup.bash"
[ -f "$HOME/auto-pharmacist/ros2_ws/install/setup.bash" ] && source "$HOME/auto-pharmacist/ros2_ws/install/setup.bash"
