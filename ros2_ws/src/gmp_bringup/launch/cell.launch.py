"""조제 칭량 셀 전체 기동 — 벤더 브링업(m0609_rg2_bringup) 위에 우리 노드 4개(ns cell).

  ros2 launch gmp_bringup cell.launch.py mode:=virtual                        # 에뮬레이터
  ros2 launch gmp_bringup cell.launch.py mode:=real host:=192.168.1.100       # 실물 (첫 기동은 vel_scale:=0.2)
  ros2 launch gmp_bringup cell.launch.py mode:=real hmi:=false                # HMI 없이
  브라우저: http://localhost:5000 (셀 밖 QA 는 같은 네트워크 다른 기기에서 http://<로봇PC IP>:5000)

파라미터의 단일 출처는 params/common.yaml. 런치 인자는 그 위에 덮어쓰는 몇 개(mode·vel_scale·simulated)뿐이다.
stations.yaml 은 데이터 yaml 이라 ROS 파라미터가 아니다 — 경로만 넘기고 skill_node 가 직접 읽는다.
"""
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, TimerAction
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PythonExpression
from launch_ros.actions import Node


def generate_launch_description():
    params = os.path.join(get_package_share_directory('gmp_bringup'), 'params')
    common = os.path.join(params, 'common.yaml')
    stations = os.path.join(params, 'stations.yaml')
    mode = LaunchConfiguration('mode')
    vel_scale = LaunchConfiguration('vel_scale')
    db_path = os.path.expanduser('~/auto-pharmacist/records/cell.db')   # 기록 DB — record_node 가 쓰고 HMI 가 읽는다
    simulated = PythonExpression(["'", mode, "' == 'virtual'"])

    vendor = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(
            get_package_share_directory('m0609_rg2_bringup'), 'launch', 'new_bringup.launch.py')),
        launch_arguments={'mode': mode, 'host': LaunchConfiguration('host'), 'port': '12345', 'model': 'm0609',
                          'name': 'dsr01', 'gui': LaunchConfiguration('gui')}.items())

    def n(pkg, exe, extra=None, cond=None):
        return Node(package=pkg, executable=exe, name=exe, namespace='cell', output='screen',
                    parameters=[common] + ([extra] if extra else []),
                    condition=cond)

    ours = [
        n('gmp_skills', 'skill_node', {'mode': mode, 'robot.vel_scale': vel_scale,
                                       'scale.simulated': simulated, 'stations_file': stations}),
        n('gmp_process', 'process_node'),
        n('gmp_hmi', 'record_node', {'db_path': db_path, 'export_dir': os.path.dirname(db_path)}),
        n('gmp_hmi', 'hmi_web_node', {'db_path': db_path, 'recipes_dir': os.path.join(params, 'recipes'),
                                      'port': LaunchConfiguration('hmi_port')}, cond=IfCondition(LaunchConfiguration('hmi'))),
    ]

    return LaunchDescription([
        DeclareLaunchArgument('mode', default_value='virtual', description='virtual | real'),
        DeclareLaunchArgument('host', default_value='127.0.0.1', description='로봇 IP (real: 192.168.1.100)'),
        DeclareLaunchArgument('vel_scale', default_value='0.3', description='속도 스케일 0~1. 실물 첫 기동 0.2'),
        DeclareLaunchArgument('gui', default_value='false', description='RViz'),
        DeclareLaunchArgument('hmi', default_value='true', description='웹 HMI 기동'),
        DeclareLaunchArgument('hmi_port', default_value='5000', description='HMI 포트 — 셀 밖 QA 는 http://<로봇PC>:5000'),
        vendor,
        # 벤더 스택(에뮬레이터·컨트롤러 스포너)이 뜬 뒤 우리 노드. DSR 서비스가 없으면 DsrArm 생성이 wait_for_service 에서 선다
        TimerAction(period=8.0, actions=ours),
    ])
