#include <gtest/gtest.h>
#include <pluginlib/class_loader.hpp>
#include <controller_interface/controller_interface.hpp>
TEST(CollisionPlugin, LoadsWithoutConnectingToRobot) {
  pluginlib::ClassLoader<controller_interface::ControllerInterface> loader(
    "controller_interface", "controller_interface::ControllerInterface");
  // 생성자만 실행한다. on_init/활성화는 하드웨어 연결이 필요하므로 호출하지 않는다.
  auto controller = loader.createSharedInstance("gmp_dsr_controller/RobotController");
  ASSERT_NE(controller, nullptr);
}
