"""시험 대역 + 실제 HMI + 실제 기록 노드. 로봇을 기동하지 않는다."""
import os
import math
import tempfile

import yaml

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, LogInfo, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def _start(context):
    # 실행마다 새 DB. 실제 records/cell.db 또는 이전 시험 기록을 덮어쓰지 않는다.
    test_dir = tempfile.mkdtemp(prefix='gmp_hmi_ros_check_')
    db_path = os.path.join(test_dir, 'cell.db')
    scenario = LaunchConfiguration('scenario').perform(context)
    if scenario not in ('normal', 'overfill', 'verify_mismatch', 'wrong_tool'):
        raise ValueError('scenario는 normal/overfill/verify_mismatch/wrong_tool이어야 합니다.')
    if not os.environ.get('GMP_HMI_ADMIN_PASSWORD'):
        raise ValueError('시험 관리자 비밀번호를 GMP_HMI_ADMIN_PASSWORD 환경변수에 설정하세요 (12자 이상).')
    initial = yaml.safe_load(LaunchConfiguration('test_initial_g').perform(context))
    if (not isinstance(initial, list) or len(initial) != 3 or
            any(type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1000 for value in initial)):
        raise ValueError('test_initial_g는 A/B/C 초기량 3개를 담은 YAML 숫자 배열이어야 합니다 (각 0~1000 g)')
    duration = float(LaunchConfiguration('item_duration_s').perform(context))
    if not math.isfinite(duration) or duration < 2.0:
        raise ValueError('item_duration_s는 2초 이상의 유한한 숫자여야 합니다')
    # 수량 범위는 공정 장부가 다시 검증한다. 시험 초기량만 조정하며 만충 용량은 1000 g이다.
    namespace = 'hmi_test'
    recipes_dir = os.path.join(get_package_share_directory('gmp_hmi'), 'config', 'recipes')
    recipes_dir = os.path.dirname(os.path.realpath(os.path.join(recipes_dir, 'recipe-01.yaml')))
    return [
        LogInfo(msg='[ROS 통신 시험] 실제 로봇 연결 없음 · http://127.0.0.1:5002 · DB: ' + db_path),
        Node(package='gmp_hmi', executable='record_node', namespace=namespace,
             output='screen', parameters=[{'db_path': db_path, 'export_dir': test_dir}]),
        Node(package='gmp_hmi', executable='hmi_test_process', namespace=namespace,
             output='screen', parameters=[{'scenario': scenario, 'item_duration_s': duration,
                          'test_material_ids': ['A', 'B', 'C'],
                          'test_capacity_g': [1000.0, 1000.0, 1000.0],
                          'test_initial_g': [float(value) for value in initial]}]),
        Node(package='gmp_hmi', executable='hmi_web_node', namespace=namespace,
             output='screen', parameters=[{
                 'admin_store_path': os.path.join(test_dir, 'admin.json'),
                 'test_inventory_enabled': True,
                 'port': 5002, 'db_path': db_path, 'ui_stale_after_s': 3.0,
                 'recipes_dir': recipes_dir,
                 # 사용자 확정 HMI 만충 표시 기준. 실제 C 재고 계약으로 사용하지 않는다.
                 'inventory_material_ids': ['A', 'B', 'C'],
                 'inventory_capacity_g': [1000.0, 1000.0, 1000.0],
                 'inventory_initial_g': [float(value) for value in initial],
                 'inventory_low_pct': 20.0,
             }]),
    ]


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument('scenario', default_value='normal',
                              description='normal / overfill / verify_mismatch / wrong_tool'),
        DeclareLaunchArgument('item_duration_s', default_value='3.0',
                              description='시험 원료 처리 시간 (2초 이상)'),
        DeclareLaunchArgument('test_initial_g', default_value='[1000.0, 1000.0, 1000.0]',
                              description='시험 A/B/C 초기량 YAML 배열 (각 0~1000 g)'),
        OpaqueFunction(function=_start),
    ])
