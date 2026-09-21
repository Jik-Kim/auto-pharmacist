#!/usr/bin/env python3
"""기동된 /hmi_test 전용 HTTP→RunBatch 취소·record_node 검증.

시험 서버: hmi_comm_test.launch.py item_duration_s:=10.0
계정/비밀번호는 사용자가 설정한 GMP_HMI_ADMIN_USER/PASSWORD를 사용한다.
이 스크립트는 운영 C/A·로봇, 안전정지 복구, 운영 재고 계약을 검증하지 않는다.
"""
import argparse
import http.cookiejar
import json
import os
import time
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:5002')
    args = parser.parse_args()
    if os.environ.get('ROS_DOMAIN_ID') != '88':
        raise SystemExit('ROS_DOMAIN_ID=88 시험 환경에서 실행하세요')
    password = os.environ.get('GMP_HMI_ADMIN_PASSWORD')
    if not password:
        raise SystemExit('사용자가 정한 시험 로그인 비밀번호를 GMP_HMI_ADMIN_PASSWORD에 입력하세요')
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    csrf = ''

    def request(path, data=None):
        payload = None if data is None else json.dumps(data).encode()
        req = urllib.request.Request(args.url.rstrip('/') + path, data=payload,
                headers={'Content-Type': 'application/json', 'X-CSRF-Token': csrf})
        try:
            with opener.open(req, timeout=20) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f'{path}: HTTP {exc.code} {exc.read().decode()}') from exc

    def wait(read, predicate, label, seconds=15):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            data = read()
            if predicate(data):
                print('PASS ' + label, flush=True)
                return data
            time.sleep(.2)
        raise RuntimeError(label + ' 시간 초과')

    csrf = request('/auth/session')['csrf_token']
    auth = request('/auth/login', {'username': os.environ.get('GMP_HMI_ADMIN_USER', 'admin'), 'password': password})
    csrf = auth['csrf_token']
    status = request('/status')
    if (status.get('diagnostics', {}).get('namespace') != '/hmi_test' or
            status.get('inventory', {}).get('mode') != 'test_process'):
        raise SystemExit('시험 네임스페이스·시험 재고가 아닙니다. 주문을 보내지 않습니다.')
    if status['state']['mode'] not in ('IDLE', 'DONE', 'ERROR') or status.get('safety_recovery', {}).get('active'):
        raise SystemExit('시험 배치가 대기 상태여야 합니다. 다른 시험/주문과 동시에 실행하지 마세요.')
    response = request('/order', {'recipe': 'recipe-01'})
    if not response.get('ok'):
        raise RuntimeError(response.get('message'))
    batch = response['batch_id']
    status = wait(lambda: request('/status'), lambda s: s['state'].get('batch_id') == batch and
                  s.get('batch_control', {}).get('can_cancel'), 'HMI 주문 · 소유 Goal 취소 가능')
    band = status['target_band']
    assert abs(band['target_g']-120) < .001 and abs(band['lower_g']-114) < .001 and abs(band['upper_g']-126) < .001
    print('PASS 수락 레시피 용기 총량 목표120 g · 허용114–126 g')
    wait(lambda: request('/restart-state'), lambda s: any(
        r['batch_id'] == batch and r['checkpoint'] and r['recipe'] for r in s['records']),
        'record_node 상태·수락 레시피 저장', seconds=8)
    response = request('/batch/cancel', {'batch_id': batch, 'confirmed': True})
    assert response['ok'], response
    wait(lambda: request('/status'), lambda s: s.get('run_batch', {}).get('result') == 'ABORTED' and
         s['state'].get('batch_id') == batch and s['state'].get('mode') == 'ERROR', '취소 접수 후 최종 ABORTED·ERROR')
    wait(lambda: request('/batch/' + batch), lambda b: b.get('finished_at') is not None and
         any(a['action'] == 'BATCH_CANCEL_RESPONSE' for a in b['audit']), 'SQLite 종료 및 취소 감사 기록')
    assert not any(r['batch_id'] == batch for r in request('/restart-state')['records'])
    print('ALL PASS 시험 서버의 취소·목표 기준·관측 기록 · 운영 C/A/실물 검증 아님')


if __name__ == '__main__':
    main()
