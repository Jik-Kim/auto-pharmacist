cmake_minimum_required(VERSION 3.8)
project(gmp_interfaces)

find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)
find_package(std_msgs REQUIRED)
find_package(action_msgs REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/RecipeItem.msg"
  "msg/Recipe.msg"
  "msg/CellState.msg"
  "msg/WeightReading.msg"
  "msg/DispenseResult.msg"
  "msg/Deviation.msg"
  "msg/CellEvent.msg"
  "msg/GripperState.msg"
  "srv/SubmitOrder.srv"
  "srv/QaDecision.srv"
  "srv/InterlockRequest.srv"
  "srv/Grip.srv"
  "srv/MeasureForce.srv"
  "srv/SafePose.srv"
  "action/MoveToStation.action"
  "action/Scoop.action"
  "action/Pour.action"
  "action/WeighContainer.action"
  "action/RunBatch.action"
  DEPENDENCIES std_msgs action_msgs
)

ament_export_dependencies(rosidl_default_runtime)
ament_package()
