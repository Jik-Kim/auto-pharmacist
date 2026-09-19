"""벤더 브링업이 실행 중일 때 skill_node만 공통 YAML로 시작한다.

실물 기본 실행:
  ros2 launch gmp_bringup skill.launch.py
"""
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    params_dir = os.path.join(get_package_share_directory('gmp_bringup'), 'params')
    common = os.path.join(params_dir, 'common.yaml')
    stations = os.path.join(params_dir, 'stations.yaml')

    return LaunchDescription([
        DeclareLaunchArgument('mode', default_value='real', description='real | virtual'),
        DeclareLaunchArgument('vel_scale', default_value='0.2', description='로봇 속도 스케일'),
        Node(
            package='gmp_skills',
            executable='skill_node',
            name='skill_node',
            namespace='cell',
            output='screen',
            parameters=[common, {
                'mode': LaunchConfiguration('mode'),
                'robot.vel_scale': LaunchConfiguration('vel_scale'),
                'stations_file': stations,
            }],
        ),
    ])
