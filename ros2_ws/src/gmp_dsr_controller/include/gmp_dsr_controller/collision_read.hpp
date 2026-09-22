#pragma once
#include <cmath>
#include <exception>
#include <limits>
#include <string>

namespace gmp_dsr_controller {
struct CollisionReading {
  bool success = false;
  float sensitivity = std::numeric_limits<float>::quiet_NaN();
  std::string message;
};

// SDK 소유 포인터를 저장하지 않고 콜백 안에서 값만 복사한다.
template<class Reader>
CollisionReading read_collision(Reader reader) {
  try {
    const auto * configuration = reader();
    if (!configuration) {
      return {false, NAN, "안전 설정 조회 결과 없음"};
    }
    const float value = configuration->_fCollisionSensitivity;
    if (!std::isfinite(value) || value < 0.0F || value > 100.0F) {
      return {false, NAN, "충돌 감도 응답 범위 오류"};
    }
    return {true, value, "전역 안전 설정의 충돌 감도(%)"};
  } catch (const std::exception & e) {
    return {false, NAN, std::string("안전 설정 조회 실패: ") + e.what()};
  } catch (...) {
    return {false, NAN, "안전 설정 조회 중 알 수 없는 오류"};
  }
}
}  // namespace gmp_dsr_controller
