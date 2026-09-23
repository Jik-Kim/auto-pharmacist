"""벤더 원본을 유지하고 DSR 감도 조회·실물 RG2 상태 발행 확장을 선택한다."""
import importlib.util
import os
import yaml
from pathlib import Path

from ament_index_python.packages import get_package_prefix, get_package_share_directory
from launch import LaunchDescription
from launch.conditions import IfCondition
from launch.substitutions import Command, FindExecutable, LaunchConfiguration, PythonExpression
from launch_ros.parameter_descriptions import ParameterValue
from launch_ros.actions import Node


def generate_launch_description():
    path = os.path.join(get_package_share_directory('m0609_rg2_bringup'),
                        'launch', 'new_bringup.launch.py')
    spec = importlib.util.spec_from_file_location('gmp_vendor_bringup', path)
    vendor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vendor)
    original = vendor.generate_launch_description()
    actions, replaced, controllers = [], 0, 0
    for action in original.entities:
        # 고정 패키지명의 서버 한 개만 제외한다. 벤더 구조 변경 시 중복 실행 대신 거부한다.
        if isinstance(action, Node) and action.node_package == 'onrobot_rg_control':
            replaced += 1
            continue
        if isinstance(action, Node) and action.node_package == 'controller_manager':
            executable = action.node_executable
            if executable == 'ros2_control_node':
                controllers += 1
                actions.append(_control_node())
                continue
        actions.append(action)
    if controllers != 1:
        raise RuntimeError('벤더 controller_manager 구성이 변경되어 자동 교체할 수 없습니다')
    if replaced != 1:
        raise RuntimeError('벤더 RG2 서버 구성이 변경되어 자동 교체할 수 없습니다')
    common = Path(get_package_share_directory('gmp_bringup')) / 'params/common.yaml'
    with common.open(encoding='utf-8') as stream:
        backend = yaml.safe_load(stream)['/**']['ros__parameters']['gripper']['backend']
    if backend not in ('dio', 'modbus'):
        raise ValueError('실물 gripper.backend는 dio 또는 modbus여야 한다')
    # DIO 운용에서는 별도 Modbus 서버의 기동 명령·상태 연결도 만들지 않는다.
    if backend == 'modbus':
        actions.append(Node(
            package='gmp_skills', executable='rg2_status_driver',
            name='OnRobotRGControllerServer', namespace=LaunchConfiguration('name'),
            output='screen',
            additional_env={'PYTHONPATH': _gripper_pythonpath()},
            condition=IfCondition(PythonExpression(["'", LaunchConfiguration('mode'), "' == 'real'"])),
            # offset=5는 벤더 계승값이며 현재 서버는 장치에 쓰지 않는다.
            # 실제 offset은 /onrobot/status.gfof로 읽는다(9/20 실측 2.0 mm).
            parameters=[{'/onrobot/control': 'modbus', '/onrobot/ip': '192.168.1.1',
                         '/onrobot/port': 502, '/onrobot/changer_addr': 65,
                         '/onrobot/gripper': 'rg2', '/onrobot/offset': 5}],
            remappings=[('/joint_states', '/onrobot_joint_states')],
        ))
    return LaunchDescription(actions)


def _gripper_pythonpath():
    """벤더 develop 설치의 egg-link를 드라이버 프로세스에만 반영한다."""
    prefix = Path(get_package_prefix('onrobot_rg_control'))
    paths = []
    for link in sorted(prefix.glob('lib/python*/site-packages/onrobot*rg*control*.egg-link')):
        lines = link.read_text().splitlines()
        if not lines:
            continue
        target = (link.parent / lines[0]).resolve()
        if (target / 'onrobot_rg_control' / 'OnRobotRGControllerServer.py').is_file():
            paths.append(str(target))
    # 일반 설치는 기존 PYTHONPATH를 그대로 사용한다. 임시 venv나 고정 홈 경로는 추가하지 않는다.
    return os.pathsep.join(paths + [os.environ.get('PYTHONPATH', '')])


def _control_node():
    """벤더와 같은 하드웨어·파라미터를 쓰고 컨트롤러 플러그인 종류만 덮어쓴다."""
    xacro = os.path.join(get_package_share_directory('m0609_rg2_bringup'),
                         'xacro', 'my_m0609_rg2.urdf.xacro')
    description = Command([
        FindExecutable(name='xacro'), ' ', xacro,
        ' name:=', LaunchConfiguration('name'),
        ' host:=', LaunchConfiguration('host'),
        ' port:=', LaunchConfiguration('port'),
        ' mode:=', LaunchConfiguration('mode'),
        ' model:=', LaunchConfiguration('model'), ' update_rate:=100',
    ])
    return Node(
        package='controller_manager', executable='ros2_control_node',
        namespace=LaunchConfiguration('name'), output='both',
        parameters=[
            {'robot_description': ParameterValue(description, value_type=str)},
            {'update_rate': 100},
            os.path.join(get_package_share_directory('dsr_controller2'),
                         'config', 'dsr_controller2.yaml'),
            # 벤더의 /**/controller_manager와 같은 범위로 type만 덮어쓴다.
            # 일반 dict는 /** 범위가 되어 더 구체적인 벤더 설정에 밀린다.
            os.path.join(get_package_share_directory('gmp_bringup'),
                         'params', 'dsr_controller_override.yaml'),
        ],
    )
