#!/usr/bin/env python3
"""시험 전용 실행기: 임시 암호 공유, 선택적 자동 검사, 종료 시 자식 정리."""
import argparse
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import time
from urllib.request import build_opener, ProxyHandler


def stop(process):
    if process is None or process.poll() is not None:
        return
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
            process.wait(timeout=10)
            return
        except ProcessLookupError:
            return
        except subprocess.TimeoutExpired:
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--auto', action='store_true', help='새 시험 서버에서 자동 검사 후 종료')
    args = parser.parse_args()
    # 기존 서버에 검사를 보내거나 기존 프로세스를 종료하지 않는다.
    with socket.socket() as probe:
        try:
            probe.bind(('0.0.0.0', 5002))
        except OSError:
            print('5002 포트가 사용 중입니다. 기존 시험 서버를 Ctrl+C로 종료하세요.', file=sys.stderr)
            return 1
    env = os.environ.copy()
    env.update(ROS_DOMAIN_ID='88', RMW_IMPLEMENTATION='rmw_fastrtps_cpp',
               GMP_HMI_ADMIN_USER='admin', GMP_HMI_ADMIN_PASSWORD=secrets.token_urlsafe(18))
    command = ['ros2', 'launch', 'gmp_hmi', 'hmi_comm_test.launch.py']
    if args.auto:
        command += ['test_initial_g:=[80.0,1000.0,1000.0]', 'item_duration_s:=2.0']
    server = checker = None
    try:
        server = subprocess.Popen(command, env=env, start_new_session=True)
        opener = build_opener(ProxyHandler({}))
        deadline = time.monotonic() + 60
        while True:
            if server.poll() is not None:
                raise RuntimeError('시험 launch가 종료되었습니다. 위 오류를 확인하세요.')
            try:
                with opener.open('http://127.0.0.1:5002/auth/session', timeout=1) as response:
                    json.load(response)
                break
            except (OSError, ValueError):
                if time.monotonic() >= deadline:
                    raise RuntimeError('60초 안에 시험 웹 서버가 준비되지 않았습니다.')
                time.sleep(0.5)
        print('\n시험 웹: http://127.0.0.1:5002 (실물 로봇 미연결)', flush=True)
        if args.auto:
            checker = subprocess.Popen(
                [sys.executable, str(Path(__file__).with_name('verify_ros_http.py'))],
                env=env, start_new_session=True)
            return checker.wait()
        # 화면 확인 모드에서만 임시 계정을 사용자 터미널에 표시한다.
        print('시험 로그인 ID: admin', flush=True)
        print('이번 실행 전용 비밀번호: ' + env['GMP_HMI_ADMIN_PASSWORD'], flush=True)
        print('휴대폰은 http://<PC IP>:5002 접속. 종료: Ctrl+C', flush=True)
        return server.wait()
    except KeyboardInterrupt:
        return 130
    except (OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        stop(checker)
        stop(server)


if __name__ == '__main__':
    raise SystemExit(main())
