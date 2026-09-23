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
        DeclareLaunchArgument('mode', default_value='real', description='real | virtual'),
        DeclareLaunchArgument('vel_scale', default_value='0.2', description='로봇 속도 스케일'),
        DeclareLaunchArgument('restore_material_id', default_value='', description='인출 완료 스쿱 원료 ID'),
        DeclareLaunchArgument('restore_operator_id', default_value='', description='복원 확인 작업자'),
        DeclareLaunchArgument('restore_confirmed', default_value='false',
                              description='인출 완료·계량 자세 정지·투입/반환 중단 아님을 확인'),
        DeclareLaunchArgument('restore_empty_scoop_confirmed', default_value='false',
                              description='현재 복원 스쿱이 비어 있어 기준 Fz 재계량 가능함을 확인'),
        DeclareLaunchArgument('force_trace_only', default_value='false',
                              description='접촉 정지 없이 힘 CSV를 기록하는 진단 모드'),
        Node(
            package='gmp_skills',
            executable='skill_node',
            name='skill_node',
            namespace='cell',
            output='screen',
            parameters=[common, {
                'mode': LaunchConfiguration('mode'),
                'height_measurement.force_trace_only': ParameterValue(LaunchConfiguration('force_trace_only'), value_type=bool),
                'robot.vel_scale': LaunchConfiguration('vel_scale'),
                'stations_file': stations,
                'restore.material_id': ParameterValue(LaunchConfiguration('restore_material_id'), value_type=str),
                'restore.operator_id': ParameterValue(LaunchConfiguration('restore_operator_id'), value_type=str),
                'restore.empty_scoop_confirmed': ParameterValue(LaunchConfiguration('restore_empty_scoop_confirmed'), value_type=bool),
                'restore.confirmed': ParameterValue(LaunchConfiguration('restore_confirmed'), value_type=bool),
            }],
        ),
    ])
