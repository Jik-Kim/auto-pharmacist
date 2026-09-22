#include <atomic>
#include <memory>
#include "dsr_controller2/dsr_controller2.hpp"
#include "gmp_interfaces/srv/get_collision_sensitivity.hpp"
#include "gmp_dsr_controller/collision_read.hpp"
#include "pluginlib/class_list_macros.hpp"

namespace gmp_dsr_controller {
class RobotController : public dsr_controller2::RobotController {
  using Base = dsr_controller2::RobotController;
  using Query = gmp_interfaces::srv::GetCollisionSensitivity;
  using Result = controller_interface::CallbackReturn;
public:
  Result on_activate(const rclcpp_lifecycle::State & state) override {
    const auto result = Base::on_activate(state);
    if (result != Result::SUCCESS) return result;
    active_.store(true);
    // 벤더 서비스와 동일한 기본 콜백 그룹: 별도 연결·작업 스레드를 만들지 않는다.
    query_ = get_node()->create_service<Query>(
      svc_prefix_ + "system/get_collision_sensitivity",
      [this](const std::shared_ptr<Query::Request>, std::shared_ptr<Query::Response> response) {
        CollisionReading value;
        if (!active_.load() || !Drfl) {
          value.message = "DSR 컨트롤러 비활성 또는 연결 객체 없음";
        } else {
          value = read_collision([this]() { return Drfl->get_safety_configuration(); });
          if (!active_.load()) {
            value = {false, NAN, "안전 설정 조회 중 컨트롤러 비활성화"};
          }
        }
        response->success = value.success;
        response->sensitivity = value.sensitivity;
        response->message = value.message;
      });
    return result;
  }
  Result on_deactivate(const rclcpp_lifecycle::State & state) override {
    stop_query();
    return Base::on_deactivate(state);
  }
  Result on_cleanup(const rclcpp_lifecycle::State & state) override {
    stop_query();
    return Base::on_cleanup(state);
  }
  Result on_error(const rclcpp_lifecycle::State & state) override {
    stop_query();
    return Base::on_error(state);
  }
  Result on_shutdown(const rclcpp_lifecycle::State & state) override {
    stop_query();
    return Base::on_shutdown(state);
  }
private:
  void stop_query() { active_.store(false); query_.reset(); }
  std::atomic<bool> active_{false};
  rclcpp::Service<Query>::SharedPtr query_;
};
}  // namespace gmp_dsr_controller
PLUGINLIB_EXPORT_CLASS(gmp_dsr_controller::RobotController, controller_interface::ControllerInterface)
