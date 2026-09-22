#!/usr/bin/env python3
"""C RunBatch 자동 DDS 시험. 다른 터미널/서버/비밀번호 입력 없이 실행한다."""
import os
from pathlib import Path
import subprocess
import sys


def main():
    if os.environ.get('ROS_DOMAIN_ID') != '88':
        raise SystemExit('시험 전용 ROS_DOMAIN_ID=88로 실행하세요.')
    try:
        import rclpy  # noqa: F401
        from gmp_interfaces.action import RunBatch  # noqa: F401
    except ImportError as e:
        raise SystemExit(f'ROS Jazzy와 빌드한 workspace를 source하세요: {e}')
    package=Path(__file__).resolve().parents[1]
    env=os.environ.copy()
    env['PYTHONPATH']=os.pathsep.join([str(package),str(package.parent/'gmp_dosing'),env.get('PYTHONPATH','')])
    print('C ProcessNode + 가짜 A 자동 기동 → 6개 DDS 사례 → 자동 종료',flush=True)
    code=subprocess.call([sys.executable,'-m','pytest','-q','-s',str(package/'test/test_run_batch_ros.py')],env=env)
    if code:
        raise SystemExit(code)
    print('ALL PASS C RunBatch DDS 검증. A 실물·힘제어·HMI 브라우저 검증은 별도입니다.',flush=True)


if __name__=='__main__':main()
