#!/usr/bin/env python3
"""운영 스킬로 A/B/C 계량 후 Enter 확인을 받아 원료 반환·수납한다."""
import argparse
import csv
import json
import math
import os
from pathlib import Path
import re
import select
import sys

import rclpy
from rcl_interfaces.srv import GetParameters
from rclpy.action import ActionClient
from rclpy.parameter import parameter_value_to_python
from gmp_interfaces.action import MoveToStation, Scoop, WeighHeld, ReturnMaterial
from gmp_interfaces.srv import MeasureForce
from manual_scoop_pour_nudge import ScoopPourNudge


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skill-log', required=True, type=Path)
    parser.add_argument('--output-dir', type=Path, default=Path('records'))
    args = parser.parse_args()
    if not args.skill_log.is_file():
        parser.error('현재 skill_node의 로그 파일이 필요합니다')
    rclpy.init(args=[])
    node = ScoopPourNudge(argparse.Namespace(namespace='/cell', vel_scale=1.0, timeout=120.0))
    node.actions['return_material'] = ActionClient(node, ReturnMaterial, 'return_material')
    measure = node.create_client(MeasureForce, 'measure_force')
    start = node.now_ns() / 1e9
    args.output_dir.mkdir(parents=True, exist_ok=True)
    base = args.output_dir / f'material_return_{node.now_ns()}'
    result_path = Path(str(base) + '_readings.csv')
    raw_path = Path(str(base) + '_raw.csv')
    result_file = result_path.open('x', newline='')
    raw_file = raw_path.open('x', newline='')
    results = csv.writer(result_file)
    raw = csv.writer(raw_file)
    results.writerow(['ros_time','material','phase','attempt','gross_g','tare_g','net_g','std_g','valid','fz_mean_n','fz_std_n'])
    raw.writerow(['ros_time','station','subject','settle_s','period_s','gain','valid','result_std_g','sample_index','fz_n','diagnostic_json'])
    seen = set()

    def flush():
        for stream in (result_file, raw_file):
            stream.flush()
            os.fsync(stream.fileno())

    def capture():
        # 무효 측정도 포함해 이번 실행의 원시 표본을 저장한다.
        for line in args.skill_log.read_text().splitlines():
            if '[WEIGH_DIAGNOSTIC] ' not in line:
                continue
            match = re.search(r'\[(\d+\.\d+)\]', line)
            if not match or float(match[1]) < start or match[1] in seen:
                continue
            data = json.loads(line.split('[WEIGH_DIAGNOSTIC] ', 1)[1])
            for index, value in enumerate(data['raw_samples']):
                raw.writerow([match[1], data['station'], data['subject'], data['settle_s'],
                              data['period_s'], data['gain'], data['valid'], data['result_std_g'],
                              index, value, json.dumps(data, ensure_ascii=False)])
            seen.add(match[1])
        flush()

    def weigh(material, tare, phase):
        for attempt in range(1, 4):
            reading = node.action('weigh_held', WeighHeld.Goal(tare_g=tare)).reading
            results.writerow([node.now_ns()/1e9, material, phase, attempt, reading.gross_g,
                              tare, reading.net_g, reading.std_g, reading.valid, '', ''])
            capture()
            print(f'MEASURE {material} {phase} try={attempt} gross={reading.gross_g:.6f} '
                  f'net={reading.net_g:.6f} std={reading.std_g:.6f} valid={reading.valid}', flush=True)
            if reading.valid and all(math.isfinite(v) for v in (reading.gross_g, reading.net_g)):
                return reading
        raise RuntimeError(f'{material} {phase} 3회 무효 — 자동 후속 이동 중단')

    try:
        flush()
        print(f'CSV {result_path.resolve()}\nRAW {raw_path.resolve()}', flush=True)
        node.prepare()
        response = node.wait(node.params_client.call_async(GetParameters.Request(
            names=['robot.vel_scale', 'scale.settle_s'])))
        values = list(map(parameter_value_to_python, response.values))
        if values != [1.0, 10.0]:
            raise RuntimeError(f'운영 설정 불일치: {values}')
        if not measure.wait_for_service(timeout_sec=10):
            raise RuntimeError('외력 측정 서비스 없음')
        node.grip(False, node.values['gripper.open_width_mm'])
        force = node.wait(measure.call_async(MeasureForce.Request(samples=0, settle_s=0.0)))
        results.writerow([node.now_ns()/1e9,'','EMPTY_FORCE',1,'','','','',force.valid,
                          force.fz_mean_n,force.fz_std_n])
        flush()
        if not force.valid:
            raise RuntimeError('빈 그리퍼 외력 무효')
        for material in ('A','B','C'):
            station = node.scoops[material]
            node.move(station, MoveToStation.Goal.AT)
            node.grip(True, node.values['gripper.scoop_search_width_mm'])
            empty = weigh(material, 0.0, 'EMPTY')
            node.action('scoop', Scoop.Goal(material_id=material, attempt=1, depth_fraction=1.0))
            weigh(material, empty.gross_g, 'FULL')
            print(f'WAIT_ENTER {material}: 외부 저울 측정 후 원상 복귀·작업 구역 이탈을 확인하고 Enter', flush=True)
            while rclpy.ok():
                node.check()
                rclpy.spin_once(node, timeout_sec=0.1)
                if select.select([sys.stdin], [], [], 0)[0]:
                    value = sys.stdin.readline()
                    if value == '':
                        raise RuntimeError('입력 종료 — 반환하지 않음')
                    if not value.strip():
                        break
            if not rclpy.ok():
                raise RuntimeError('ROS 종료')
            node.action('return_material', ReturnMaterial.Goal(material_id=material))
            node.move(station, MoveToStation.Goal.AT)
            node.grip(False, node.values['gripper.open_width_mm'])
            print(f'COMPLETE {material}', flush=True)
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        print(f'STOP {exc}', flush=True)
        node.cancel_move()
        return 1
    finally:
        capture()
        result_file.close()
        raw_file.close()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    raise SystemExit(main())
