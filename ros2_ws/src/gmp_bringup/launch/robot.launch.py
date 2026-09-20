"""벤더 로봇 런치를 유지하고 실물 RG2 서버만 상태 발행 확장으로 교체한다."""
import importlib.util
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration, PythonExpression
from launch_ros.actions import Node


def generate_launch_description():
    path = os.path.join(get_package_share_directory('m0609_rg2_bringup'),
                        'launch', 'new_bringup.launch.py')
    spec = importlib.util.spec_from_file_location('gmp_vendor_bringup', path)
    vendor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vendor)
    original = vendor.generate_launch_description()
    actions, replaced = [], 0
    for action in original.entities:
        # 고정 패키지명의 서버 한 개만 제외한다. 벤더 구조 변경 시 중복 실행 대신 거부한다.
        if isinstance(action, Node) and action.node_package == 'onrobot_rg_control':
            replaced += 1
            continue
        actions.append(action)
    if replaced != 1:
        raise RuntimeError('벤더 RG2 서버 구성이 변경되어 자동 교체할 수 없습니다')
    actions.append(Node(
        package='gmp_skills', executable='rg2_status_driver',
        name='OnRobotRGControllerServer', namespace=LaunchConfiguration('name'),
        output='screen',
        condition=IfCondition(PythonExpression(["'", LaunchConfiguration('mode'), "' == 'real'"])),
        parameters=[{'/onrobot/control': 'modbus', '/onrobot/ip': '192.168.1.1',
                     '/onrobot/port': 502, '/onrobot/changer_addr': 65,
                     '/onrobot/gripper': 'rg2', '/onrobot/offset': 5}],
        remappings=[('/joint_states', '/onrobot_joint_states')],
    ))
    return LaunchDescription(actions)
