"""실제 C 공정에 연결할 HMI와 기록 노드. 공정·로봇 노드는 별도 실행한다."""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    params = {name: LaunchConfiguration(name) for name in
              ('db_path', 'admin_store_path', 'recipes_dir')}
    params['port'] = ParameterValue(LaunchConfiguration('port'), value_type=int)
    return LaunchDescription([
        DeclareLaunchArgument('namespace', default_value='cell'),
        DeclareLaunchArgument('port', default_value='5000'),
        DeclareLaunchArgument('db_path', default_value='~/auto-pharmacist/records/cell.db'),
        DeclareLaunchArgument('export_dir', default_value='~/auto-pharmacist/records'),
        DeclareLaunchArgument('admin_store_path', default_value='~/.config/gmp_hmi/admin.json'),
        # gmp_bringup은 downstream 패키지여서 HMI만 빌드하면 share가 없을 수 있다.
        DeclareLaunchArgument('recipes_dir', default_value='~/auto-pharmacist/ros2_ws/src/gmp_hmi/config/recipes'),
        Node(package='gmp_hmi', executable='record_node',
             namespace=LaunchConfiguration('namespace'), output='screen',
             parameters=[{'db_path': LaunchConfiguration('db_path'),
                          'export_dir': LaunchConfiguration('export_dir')}]),
        Node(package='gmp_hmi', executable='hmi_web_node',
             namespace=LaunchConfiguration('namespace'), output='screen', parameters=[params]),
    ])
