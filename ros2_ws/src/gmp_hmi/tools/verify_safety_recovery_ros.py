#!/usr/bin/env python3
"""실제 DDS + 시험 C 응답기. 실제 C/A/로봇 복구를 검증하지 않는다.

ROS_DOMAIN_ID=88 전용. /hmi_safety_test 이외에는 명령을 보내지 않는다.
비밀번호 생성/변경 없음. HTTP 인증은 test_safety_recovery.py에서 별도 검사한다.
"""
import json
import os
from pathlib import Path
import tempfile
import threading
import time


def main():
    if os.environ.get('ROS_DOMAIN_ID') != '88':
        raise SystemExit('시험 전용 ROS_DOMAIN_ID=88을 설정하세요. 운영 도메인에서는 실행하지 않습니다.')
    import rclpy
    from rclpy.node import Node
    from rclpy.executors import MultiThreadedExecutor, ExternalShutdownException
    from rclpy.callback_groups import ReentrantCallbackGroup
    from gmp_interfaces.msg import CellEvent, CellState
    from gmp_interfaces.srv import RecoverSafety
    from gmp_hmi.nodes.hmi_web_node import HmiRosNode, LATCHED

    def wait(predicate, label, timeout=10):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if predicate():
                return
            time.sleep(.02)
        raise AssertionError(label + ' 시간 초과')

    with tempfile.TemporaryDirectory(prefix='hmi_safety_contract_') as directory:
        rclpy.init(args=['--ros-args', '-r', '__ns:=/hmi_safety_test', '-p',
                        'admin_store_path:=' + str(Path(directory) / 'admin.json')])
        hmi = HmiRosNode()
        stub = Node('safety_contract_stub')
        executor = MultiThreadedExecutor(num_threads=4)
        executor.add_node(hmi)
        executor.add_node(stub)
        calls = []
        audits = []
        answer = dict(success=True, manual_required=False, robot_state=1, message='시험 응답: STANDBY', delay=0)
        group = ReentrantCallbackGroup()
        def recover(req, response):
            calls.append(req)
            current = dict(answer)
            msg = CellEvent()
            msg.header.stamp = stub.get_clock().now().to_msg()
            msg.level = 2
            msg.code = 'ROBOT_SAFETY_STOP'
            msg.text = json.dumps(dict(origin='recovery_request', request_id=req.request_id,
                                      operator_id=req.operator_id, robot_state=req.expected_state,
                                      reason='HMI 안전 복구 요청'))
            pub_event.publish(msg)
            wait(lambda: any(m.code == msg.code and m.text == msg.text for m in audits), '복구 시작 이벤트')
            time.sleep(current.pop('delay'))
            for key, value in current.items():
                setattr(response, key, value)
            return response
        service = stub.create_service(RecoverSafety, 'request_safety_recovery', recover, callback_group=group)
        pub_state = stub.create_publisher(CellState, 'state', LATCHED)
        pub_event = stub.create_publisher(CellEvent, 'event', 100)
        sub = stub.create_subscription(CellEvent, 'event', lambda m: audits.append(m), 100)
        def state_tick():
            msg = CellState()
            msg.header.stamp = stub.get_clock().now().to_msg()
            msg.mode = CellState.ERROR
            msg.batch_id = 'SAFETY-CONTRACT-TEST'
            msg.step = 'ERROR'
            msg.note = '시험 C 응답기 · 실제 로봇 미연결'
            pub_state.publish(msg)
        timer = stub.create_timer(.1, state_tick)
        def spin():
            try:
                executor.spin()
            except ExternalShutdownException:
                pass
        thread = threading.Thread(target=spin, daemon=True)
        thread.start()
        try:
            assert hmi.get_namespace() == '/hmi_safety_test'
            resolved = hmi.resolve_service_name(hmi.cli_recovery.srv_name)
            assert resolved == '/hmi_safety_test/request_safety_recovery', resolved
            wait(lambda: hmi.cli_recovery.service_is_ready() and bool(hmi.snapshot()['state']), 'DDS 발견')
            wait(lambda: pub_event.get_subscription_count() >= 2, '이벤트 구독')
            def stop(code=5):
                generation = hmi.snapshot()['safety_recovery']['generation']
                msg = CellEvent()
                msg.header.stamp = stub.get_clock().now().to_msg()
                msg.level = 2
                msg.code = 'ROBOT_SAFETY_STOP'
                msg.text = json.dumps(dict(robot_state=code, reason='시험 안전정지'))
                pub_event.publish(msg)
                wait(lambda: hmi.snapshot()['safety_recovery']['generation'] > generation, 'STOP 수신')
            def request():
                return hmi.recover_safety('ros-verifier', 5, True,
                    hmi.snapshot()['safety_recovery']['generation'])
            stop()
            rid = request()
            wait(lambda: hmi.snapshot()['safety_recovery']['phase'] == 'recovered', '복구 응답')
            assert calls[-1].request_id == rid and calls[-1].operator_id == 'ros-verifier'
            assert hmi.snapshot()['state']['mode'] == 'ERROR'
            print('PASS 1/4 DDS STOP → C 시험 서비스 → 복구 시작 STOP → 복구 응답, 배치 ERROR 유지', flush=True)
            stop(9)
            answer.update(success=False, manual_required=True, robot_state=8, message='시험: 현장 교정 필요')
            request()
            wait(lambda: hmi.snapshot()['safety_recovery']['phase'] == 'manual_required', '수동 조치')
            assert hmi.snapshot()['safety_recovery']['active']
            print('PASS 2/4 수동 조치 응답은 안전정지 표시 유지', flush=True)
            stop()
            answer.update(success=True, manual_required=False, robot_state=1, delay=.5)
            count = len(calls)
            request()
            wait(lambda: len(calls) > count, '지연 요청 접수')
            stop(6)
            wait(lambda: any(m.code == 'HMI_SAFETY_RECOVERY_RESULT' and
                 '"applied_to_current_stop": false' in m.text for m in audits), '이전 응답 무효 감사 기록')
            assert hmi.snapshot()['safety_recovery']['active']
            assert hmi.snapshot()['safety_recovery']['robot_state'] == 6
            print('PASS 3/4 새 정지 후 이전 성공 응답 무효', flush=True)
            wait(lambda: sum(m.code == 'HMI_SAFETY_RECOVERY_RESULT' for m in audits) >= 3, '감사 이벤트')
            assert all(m.batch_id == 'SAFETY-CONTRACT-TEST' for m in audits if m.code.startswith('HMI_SAFETY_RECOVERY'))
            print('PASS 4/4 요청·결과·작업자·배치 감사 이벤트 DDS 수신', flush=True)
            print('ALL PASS (시험 C 응답기). 실제 C/A 복구·물리 안전·브라우저 표시 검증 아님.', flush=True)
        finally:
            executor.shutdown()
            thread.join(timeout=3)
            hmi.destroy_node()
            stub.destroy_node()
            rclpy.try_shutdown()


if __name__ == '__main__':
    main()
