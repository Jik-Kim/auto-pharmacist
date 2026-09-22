#include <gtest/gtest.h>
#include <stdexcept>
#include "gmp_dsr_controller/collision_read.hpp"
struct Config { float _fCollisionSensitivity; };
TEST(CollisionRead, RejectsMissingInvalidAndThrowingSdk) {
  using gmp_dsr_controller::read_collision;
  EXPECT_FALSE(read_collision([]() -> Config* { return nullptr; }).success);
  for (float value : {-1.F, 101.F, NAN, INFINITY}) {
    Config config{value};
    EXPECT_FALSE(read_collision([&]() { return &config; }).success);
  }
  EXPECT_FALSE(read_collision([]() -> Config* { throw std::runtime_error("offline"); }).success);
}
TEST(CollisionRead, CopiesActualValueWithoutWritingConfiguration) {
  for (float value : {0.F, 49.F, 50.F, 100.F}) {
    Config config{value};
    int calls = 0;
    auto result = gmp_dsr_controller::read_collision([&]() { ++calls; return &config; });
    ASSERT_TRUE(result.success);
    EXPECT_FLOAT_EQ(result.sensitivity, value);
    EXPECT_FLOAT_EQ(config._fCollisionSensitivity, value);
    EXPECT_EQ(calls, 1);
  }
}
