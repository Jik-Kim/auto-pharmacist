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
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    params_dir = os.path.join(get_package_share_directory('gmp_bringup'), 'params')
    common = os.path.join(params_dir, 'common.yaml')
    stations = os.path.join(params_dir, 'stations.yaml')

    return LaunchDescription([
        DeclareLaunchArgument('height_measure_only', default_value='false', description='원료 높이 측정만 실행'),
        DeclareLaunchArgument('mode', default_value='real', description='real | virtual'),
        DeclareLaunchArgument('vel_scale', default_value='0.2', description='로봇 속도 스케일'),
        DeclareLaunchArgument('restore_material_id', default_value='', description='인출 완료 스쿱 원료 ID'),
        DeclareLaunchArgument('restore_operator_id', default_value='', description='복원 확인 작업자'),
        DeclareLaunchArgument('restore_confirmed', default_value='false',
                              description='인출 완료·계량 자세 정지·투입/반환 중단 아님을 확인'),
        Node(
            package='gmp_skills',
            executable='skill_node',
            name='skill_node',
            namespace='cell',
            output='screen',
            parameters=[common, {
                'scoop.height_measure_only': ParameterValue(LaunchConfiguration('height_measure_only'), value_type=bool),
                'mode': LaunchConfiguration('mode'),
                'robot.vel_scale': LaunchConfiguration('vel_scale'),
                'stations_file': stations,
                'restore.material_id': ParameterValue(LaunchConfiguration('restore_material_id'), value_type=str),
                'restore.operator_id': ParameterValue(LaunchConfiguration('restore_operator_id'), value_type=str),
                'restore.confirmed': ParameterValue(LaunchConfiguration('restore_confirmed'), value_type=bool),
            }],
        ),
    ])
