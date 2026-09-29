#!/usr/bin/env python3
"""이미 파지한 용기 놓기 → 넛지 대기 → 안전 자세만 실행하는 실물 수동 시험.

실행만으로 로봇이 움직인다. 사용 조건과 명령은 manual_place_nudge.md 참조.
기존 공정 FSM이나 로봇 어댑터를 직접 호출하지 않는다.
"""
import argparse
import math

import rclpy
from action_msgs.msg import GoalStatus
from rcl_interfaces.srv import GetParameters
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.parameter import parameter_value_to_python

from gmp_interfaces.action import MoveToStation
from gmp_interfaces.msg import CellEvent
from gmp_interfaces.srv import SafePose, SetGripper


class PlaceNudge(Node):
    def __init__(self, args):
        super().__init__('manual_place_nudge', namespace=args.namespace)
        self.args = args
        self.fault = ''
        self.wait_since_ns = None
        self.nudged = False
        self.active_goal = None
        self.pending_goal = None
        self.move_client = ActionClient(self, MoveToStation, 'move_to_station')
        self.grip_client = self.create_client(SetGripper, 'set_gripper')
        self.safe_client = self.create_client(SafePose, 'safe_pose')
        self.params_client = self.create_client(GetParameters, 'skill_node/get_parameters')
        self.event_sub = self.create_subscription(CellEvent, 'event', self.on_event, 100)

    def now_ns(self):
        return self.get_clock().now().nanoseconds

    def on_event(self, msg):
        if msg.code in ('ROBOT_SAFETY_STOP', 'NUDGE_UNAVAILABLE'):
            self.fault = f'{msg.code}: {msg.text}'
        stamp = msg.header.stamp.sec * 1_000_000_000 + msg.header.stamp.nanosec
        if (msg.code == 'NUDGE' and self.wait_since_ns is not None
                and stamp >= self.wait_since_ns):
            self.nudged = True
            self.get_logger().info(f'넛지 수신: {msg.text}')

    def check(self):
        if self.fault:
            raise RuntimeError(self.fault)
        if any(name == 'process_node' and namespace == self.get_namespace()
               for name, namespace in self.get_node_names_and_namespaces()):
            raise RuntimeError('같은 네임스페이스의 process_node를 종료한 뒤 실행하세요')

    def wait(self, future):
        deadline = self.now_ns() + int(self.args.timeout * 1e9)
        while rclpy.ok() and not future.done():
            self.check()
            if self.now_ns() >= deadline:
                raise RuntimeError('응답 시간 초과 — 후속 동작을 실행하지 않습니다')
            rclpy.spin_once(self, timeout_sec=0.1)
        if not rclpy.ok():
            raise RuntimeError('ROS 종료')
        self.check()
        return future.result()

    def move(self, station, approach):
        self.check()
        self.get_logger().info(f'이동: {station}, approach={approach}')
        goal = MoveToStation.Goal(
            station_id=station, approach=approach, vel_scale=self.args.vel_scale)
        self.pending_goal = self.move_client.send_goal_async(goal)
        self.active_goal = self.wait(self.pending_goal)
        self.pending_goal = None
        if not self.active_goal.accepted:
            raise RuntimeError('이동 요청 거부')
        result = self.wait(self.active_goal.get_result_async())
        self.active_goal = None
        if result.status != GoalStatus.STATUS_SUCCEEDED or not result.result.success:
            raise RuntimeError(f'이동 실패: {result.result.message}')
        if result.result.reached != station:
            raise RuntimeError(f'도착 스테이션 불일치: {result.result.reached}')

    def cancel_move(self):
        # 승인 응답이 늦게 온 이동도 취소한다. 실패 뒤 SafePose 등 새 이동은 하지 않는다.
        try:
            if self.pending_goal is not None:
                rclpy.spin_until_future_complete(self, self.pending_goal, timeout_sec=3.0)
                if self.pending_goal.done():
                    self.active_goal = self.pending_goal.result()
            if self.active_goal is not None and self.active_goal.accepted:
                future = self.active_goal.cancel_goal_async()
                rclpy.spin_until_future_complete(self, future, timeout_sec=3.0)
                self.get_logger().warning('진행 중 이동에 취소 요청을 보냈습니다. 실제 정지는 현장에서 확인하세요')
        except Exception as exc:
            self.get_logger().error(f'이동 취소 확인 실패: {exc}')

    def run(self):
        # ROS 그래프 발견 시간을 둔 뒤 자동 공정과의 동시 실행을 거부한다.
        until = self.now_ns() + 3_000_000_000
        while self.now_ns() < until:
            rclpy.spin_once(self, timeout_sec=0.1)
        self.check()
        for client in (self.grip_client, self.safe_client, self.params_client):
            if not client.wait_for_service(timeout_sec=self.args.timeout):
                raise RuntimeError('skill_node 서비스가 준비되지 않았습니다')
        if not self.move_client.wait_for_server(timeout_sec=self.args.timeout):
            raise RuntimeError('이동 Action 서버가 준비되지 않았습니다')
        names = ['safety.nudge_enabled', 'scale.simulated',
                 'gripper.open_width_mm', 'gripper.force_n']
        response = self.wait(self.params_client.call_async(GetParameters.Request(names=names)))
        values = [parameter_value_to_python(value) for value in response.values]
        if len(values) != len(names):
            raise RuntimeError('skill_node 파라미터 조회 실패')
        enabled, simulated, width, force = values
        if enabled is not True or simulated is not False:
            raise RuntimeError('실물 넛지 활성화와 scale.simulated=false가 필요합니다')

        # 준비된 용기를 운반하여 놓는다. 새 용기를 집거나 공정을 시작하지 않는다.
        self.move('passbox_done', MoveToStation.Goal.ABOVE)
        self.move('passbox_done', MoveToStation.Goal.AT)
        self.check()
        opened = self.wait(self.grip_client.call_async(SetGripper.Request(
            close=False, width_mm=float(width), force_n=float(force),
            timeout_s=self.args.timeout)))
        if not opened.success:
            raise RuntimeError(f'놓기 실패: {opened.message}')
        self.move('passbox_done', MoveToStation.Goal.ABOVE)
        self.move('nudge_wait', MoveToStation.Goal.AT)

        self.wait_since_ns = self.now_ns()
        self.get_logger().info('넛지 대기: 용기 회수 후 로봇을 터치하세요. 시간 초과 없이 기다립니다')
        while rclpy.ok() and not self.nudged:
            self.check()
            rclpy.spin_once(self, timeout_sec=0.1)
        self.wait_since_ns = None
        if not rclpy.ok():
            raise RuntimeError('넛지 대기 중 종료')
        self.check()
        result = self.wait(self.safe_client.call_async(
            SafePose.Request(reason='MANUAL_PLACE_NUDGE')))
        if not result.success:
            raise RuntimeError(f'안전 자세 이동 실패: {result.message}')
        self.get_logger().info('완료: 놓기 → 넛지 → 안전 자세')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--namespace', default='/cell')
    parser.add_argument('--vel-scale', type=float, default=0.2,
                        help='MoveToStation 속도·가속도 배율, 기본 0.2')
    parser.add_argument('--timeout', type=float, default=60.0,
                        help='서비스·Action 응답 제한(초), 넛지 대기에는 적용하지 않음')
    args = parser.parse_args()
    if not math.isfinite(args.vel_scale) or not 0 < args.vel_scale <= 1:
        parser.error('--vel-scale은 0 초과 1 이하여야 합니다')
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('--timeout은 유한한 양수여야 합니다')
    rclpy.init(args=[])
    node = PlaceNudge(args)
    code = 0
    try:
        node.run()
    except (Exception, KeyboardInterrupt) as exc:
        code = 1
        node.get_logger().error(f'시험 중단: {exc or "사용자 중단"}. 후속 이동은 실행하지 않습니다')
        node.cancel_move()
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    return code


if __name__ == '__main__':
    raise SystemExit(main())
