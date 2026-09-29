#!/usr/bin/env python3
"""레시피 없이 빈 용기 배치 → A/B/C 1회 Scoop·Pour → 완성 용기 놓기 → 넛지.

실물 수동 시험 전용. process_node를 종료하고 skill_node·드라이버만 유지한다.
스쿱 인출에 필요한 WeighHeld는 호출하지만 계량값으로 투입량을 판정하지 않는다.
"""
import argparse
import math

import rclpy
import yaml
from action_msgs.msg import GoalStatus
from rcl_interfaces.srv import GetParameters
from rclpy.action import ActionClient
from rclpy.parameter import parameter_value_to_python

from gmp_interfaces.action import MoveToStation, Scoop, Pour, WeighHeld
from gmp_interfaces.srv import RestoreGrip, SafePose, SetGripper
from manual_place_nudge import PlaceNudge


class ScoopPourNudge(PlaceNudge):
    def __init__(self, args):
        super().__init__(args)
        self.restore_client = self.create_client(RestoreGrip, 'restore_grip')
        self.actions = {
            'scoop': ActionClient(self, Scoop, 'scoop'),
            'pour': ActionClient(self, Pour, 'pour'),
            'weigh_held': ActionClient(self, WeighHeld, 'weigh_held'),
        }

    def action(self, name, goal):
        self.check()
        self.get_logger().info(f'스킬: {name}')
        self.pending_goal = self.actions[name].send_goal_async(goal)
        self.active_goal = self.wait(self.pending_goal)
        self.pending_goal = None
        if not self.active_goal.accepted:
            raise RuntimeError(f'{name} 요청 거부')
        response = self.wait(self.active_goal.get_result_async())
        self.active_goal = None
        if response.status != GoalStatus.STATUS_SUCCEEDED or not response.result.success:
            raise RuntimeError(f'{name} 실패: {response.result.message}')
        return response.result

    def grip(self, close, width):
        self.check()
        result = self.wait(self.grip_client.call_async(SetGripper.Request(
            close=close, width_mm=float(width), force_n=float(self.values['gripper.force_n']),
            timeout_s=self.args.timeout)))
        if not result.success or (close and not result.grip_inferred):
            raise RuntimeError(f'파지/열기 실패: {result.message}')

    def pick_cup(self, station):
        self.move(station, MoveToStation.Goal.ABOVE)
        self.move(station, MoveToStation.Goal.AT)
        self.grip(True, self.values['gripper.cup_width_mm'])
        self.move(station, MoveToStation.Goal.ABOVE)

    def prepare(self):
        # 서버 준비와 운영 설정을 먼저 확인하고 그 뒤에만 로봇을 움직인다.
        until = self.now_ns() + 3_000_000_000
        while self.now_ns() < until:
            rclpy.spin_once(self, timeout_sec=0.1)
        self.check()
        for client in (self.grip_client, self.safe_client, self.params_client, self.restore_client):
            if not client.wait_for_service(timeout_sec=self.args.timeout):
                raise RuntimeError('skill_node 서비스가 준비되지 않았습니다')
        for client in (self.move_client, *self.actions.values()):
            if not client.wait_for_server(timeout_sec=self.args.timeout):
                raise RuntimeError('skill_node Action이 준비되지 않았습니다')
        names = ['safety.nudge_enabled', 'scale.simulated', 'scoop.height_measure_only',
                 'stations_file', 'gripper.cup_width_mm', 'gripper.open_width_mm',
                 'gripper.scoop_search_width_mm', 'gripper.force_n', 'robot.vel_scale']
        response = self.wait(self.params_client.call_async(GetParameters.Request(names=names)))
        if len(response.values) != len(names):
            raise RuntimeError('스킬 파라미터 조회 실패')
        self.values = dict(zip(names, map(parameter_value_to_python, response.values)))
        if (self.values['safety.nudge_enabled'] is not True
                or self.values['scale.simulated'] is not False
                or self.values['scoop.height_measure_only'] is not False):
            raise RuntimeError('실물 넛지 활성화·계량 시뮬레이션 해제·높이 진단 모드 해제가 필요합니다')
        with open(self.values['stations_file'], encoding='utf-8') as stream:
            data = yaml.safe_load(stream)
        self.scoops = {}
        for material in ('A', 'B', 'C'):
            matches = [name for name, body in data['stations'].items()
                       if name.startswith('scoop') and body.get('material_id') == material]
            if len(matches) != 1:
                raise RuntimeError(f'{material} 스쿱 스테이션이 유일하지 않습니다')
            self.scoops[material] = matches[0]
            profile = data['scooping'][material]
            if (profile.get('execution_mode') != 'taught_fixed'
                    or profile.get('fixed_path', {}).get('verified') is not True):
                raise RuntimeError(f'{material} 검증된 고정 스쿠핑 설정이 필요합니다')
        self.get_logger().info(
            f"이동 배율={self.args.vel_scale}, Scoop·Pour·인출 배율={self.values['robot.vel_scale']}")

    def run(self):
        self.prepare()
        self.check()
        result = self.wait(self.safe_client.call_async(SafePose.Request(reason='MANUAL_SEQUENCE_START')))
        if not result.success:
            raise RuntimeError(f'시작 안전 자세 실패: {result.message}')
        result = self.wait(self.restore_client.call_async(RestoreGrip.Request(
            expected_payload='empty', expected_material_id='')))
        if not result.success or result.payload != 'empty':
            raise RuntimeError(f'빈 그리퍼 확인 실패: {result.message}')
        self.pick_cup('passbox_empty')
        self.move('workbench', MoveToStation.Goal.ABOVE)
        self.move('workbench', MoveToStation.Goal.AT)
        self.grip(False, self.values['gripper.open_width_mm'])
        self.move('workbench', MoveToStation.Goal.ABOVE)
        for material in ('A', 'B', 'C'):
            station = self.scoops[material]
            self.move(station, MoveToStation.Goal.AT)
            self.grip(True, self.values['gripper.scoop_search_width_mm'])
            # 현 계약은 스쿱 인출과 계량이 한 Action이다. valid는 투입 판정에 쓰지 않는다.
            self.action('weigh_held', WeighHeld.Goal(tare_g=0.0))
            self.action('scoop', Scoop.Goal(material_id=material, attempt=1, depth_fraction=1.0))
            self.action('pour', Pour.Goal(fraction=1.0))
            self.move(station, MoveToStation.Goal.AT)
            self.grip(False, self.values['gripper.open_width_mm'])
        self.pick_cup('workbench')
        # 검증된 기존 놓기 → 후퇴 → 넛지 도착 이후 새 이벤트 → SafePose 순서를 재사용한다.
        super().run()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--namespace', default='/cell')
    parser.add_argument('--vel-scale', type=float, default=1.0,
                        help='MoveToStation 배율만 지정. Scoop·Pour·인출은 skill_node 설정 사용')
    parser.add_argument('--timeout', type=float, default=120.0,
                        help='스킬 응답 제한(초). 넛지 입력은 시간 제한 없음')
    args = parser.parse_args()
    if not math.isfinite(args.vel_scale) or not 0 < args.vel_scale <= 1:
        parser.error('--vel-scale은 0 초과 1 이하여야 합니다')
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('--timeout은 유한한 양수여야 합니다')
    rclpy.init(args=[])
    node = ScoopPourNudge(args)
    code = 0
    try:
        node.run()
    except (Exception, KeyboardInterrupt) as exc:
        code = 1
        node.get_logger().error(f'시험 중단: {exc or "사용자 중단"}. 후속 동작은 실행하지 않습니다')
        node.cancel_move()
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    return code


if __name__ == '__main__':
    raise SystemExit(main())
