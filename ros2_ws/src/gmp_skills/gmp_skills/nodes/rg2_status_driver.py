"""OnRobot RG Modbus 상태 레지스터를 ROS로 중계하는 노드."""

import rclpy
from rclpy.executors import ExternalShutdownException, MultiThreadedExecutor

from onrobot_rg_control.OnRobotRGControllerServer import OnRobotRGNode
from onrobot_rg_msgs.msg import OnRobotRGInput

from gmp_skills.core.rg2_status import status_fields


class Rg2StatusDriver(OnRobotRGNode):
    """벤더 서버가 읽은 상태 dict를 ROS 메시지로 발행한다."""

    def __init__(self, node_name: str = "Rg2StatusDriver"):
        # 벤더 생성자가 50 Hz 타이머를 만들지만 생성자 반환 전에는
        # 실행되지 않으므로 executor가 콜백을 부르기 전에 publisher가 준비된다.
        super().__init__(node_name)
        self.status_pub = self.create_publisher(OnRobotRGInput, "/onrobot/status", 10)

    def getStatus(self):
        """벤더 상태 읽기·JointState 발행 후 raw 상태를 추가 발행한다."""
        super().getStatus()
        fields = status_fields(self.status)
        message = OnRobotRGInput()
        message.gfof = fields["gfof"]
        message.ggwd = fields["ggwd"]
        message.gsta = fields["gsta"]
        message.gwdf = fields["gwdf"]
        self.status_pub.publish(message)


def main(args=None):
    rclpy.init(args=args)
    node = None
    executor = None
    try:
        node = Rg2StatusDriver()
        executor = MultiThreadedExecutor()
        executor.add_node(node)
        executor.spin()
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        if executor is not None:
            executor.shutdown()
            if node is not None:
                executor.remove_node(node)
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
