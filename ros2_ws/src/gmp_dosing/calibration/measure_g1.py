#!/usr/bin/env python3
"""G1 계량 실측 — tool_force(Fz) 와 workpiece(kgf) 를 **같은 표본에서 동시에** 기록한다.

로봇을 움직이지 않는다. 자세는 사람이 티치펜던트로 잡고, 스크립트는 읽기만 한다 — 충돌 감지는 그대로다.
출력 CSV 는 calibration/g1_scoop133g_tool_force.csv 와 같은 열에 `작업물무게_kgf`·`시각_s` 를 더한 것이라
core/calib.py 가 `--method tool_force` / `--method workpiece` 로 두 경로를 같은 방법으로 비교한다.

준비: `source tools/env.sh` · dsr_bringup2 실물 기동(mode real). skill_node 는 내려 둔다 (같은 DSR 노드 이름을 쓰지는 않지만 로봇을 움직일 수 있다).
실행 예:
  python3 ros2_ws/src/gmp_dosing/calibration/measure_g1.py --actual-g 133 --object scoop \\
      --sets 6 --trials 30 --samples 10 --period 0.1 --out records/g1_workpiece_$(date +%m%d).csv

절차 (프롬프트가 안내한다):
  1. 빈 그리퍼로 계량 자세 → Enter → reset_workpiece_weight (세션 1회, 매뉴얼 5.1.2)
  2. 세트마다: 물체를 잡고 계량 자세에서 정지 → Enter → trials × samples 읽기.
     세트 사이에 물체를 **놓았다 다시 잡는다** — 운영에서 매 계량이 새 파지라, 그 흐름을 σ 에 넣기 위해서다.
--period 는 표본 간격[s]. 9/18 데이터는 표본 43 % 가 앞 값 반복이었다 — 센서 갱신보다 짧았다는 뜻.
calib.py 가 `시각_s` 로 값이 바뀌는 간격의 중앙값을 알려 주니, 그보다 길게 잡는다.
"""
import argparse
import csv
import datetime
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
COMMON = HERE.parents[2] / 'gmp_bringup' / 'params' / 'common.yaml'
COLUMNS = ['실험명', '물체종류', '측정조건', '실제총무게_g', '반복번호', '표본번호', '시각_s',
           'X축힘_N', 'Y축힘_N', 'Z축힘_N', 'X축모멘트_Nm', 'Y축모멘트_Nm', 'Z축모멘트_Nm', '작업물무게_kgf']


def robot_params():
    import yaml
    r = yaml.safe_load(COMMON.read_text())['/**']['ros__parameters']['robot']
    return r['id'], r['model'], float(r['vel']), float(r['acc']), r.get('tool_name', ''), r.get('tcp_name', '')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--actual-g', type=float, required=True, help='실제 저울로 잰 물체 총무게 [g]')
    ap.add_argument('--object', default='scoop', help='물체종류 (scoop | container | …)')
    ap.add_argument('--condition', default='', help='측정조건 표기. 비우면 <object>_total_<g>g')
    ap.add_argument('--sets', type=int, default=6)
    ap.add_argument('--trials', type=int, default=30)
    ap.add_argument('--samples', type=int, default=10)
    ap.add_argument('--period', type=float, default=0.1, help='표본 간격 [s]')
    ap.add_argument('--settle', type=float, default=1.0, help='회차 전 정착 대기 [s]')
    ap.add_argument('--out', required=True, help='CSV 경로 (records/ 는 git 밖. 확정되면 calibration/ 으로 복사)')
    ap.add_argument('--no-reset', action='store_true', help='reset_workpiece_weight 를 건너뛴다 (이미 한 세션)')
    a = ap.parse_args(argv)

    import rclpy
    from gmp_skills.adapters.dsr_arm import DsrArm
    rid, model, vel, acc, tool, tcp = robot_params()
    rclpy.init()
    arm = DsrArm(rid, model, 'real', vel, acc, tool, tcp)
    arm.initialize()                       # set_tool/set_tcp — workpiece 추정은 등록된 툴 무게가 전제다
    cond = a.condition or f'{a.object}_total_{a.actual_g:g}g'
    stamp = datetime.datetime.now().strftime('%m%d%H%M')
    out = pathlib.Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
    new = not out.exists()
    f = out.open('a', newline='', encoding='utf-8')
    w = csv.writer(f)
    if new:
        w.writerow(COLUMNS)

    if not a.no_reset:
        input('\n[1] 빈 그리퍼로 계량 자세에 두고 정지 → Enter (reset_workpiece_weight) ')
        arm.reset_workpiece()
        print('    영점 완료')
    t0 = time.monotonic()
    try:
        for s in range(1, a.sets + 1):
            input(f'\n[2] 세트 {s}/{a.sets}: 물체({a.actual_g:g} g) 를 잡고 계량 자세에서 정지 → Enter ')
            name = f'{a.object}_total{a.actual_g:g}g_{stamp}_set{s}'
            for t in range(1, a.trials + 1):
                time.sleep(a.settle)
                fz, kg = [], []
                for n in range(1, a.samples + 1):
                    force = arm.tool_force()
                    wp = arm.R.get_workpiece_weight()
                    ts = time.monotonic() - t0
                    force6 = list(force) if force else [''] * 6
                    wp_v = float(wp) if isinstance(wp, (int, float)) and wp >= 0 else ''
                    w.writerow([name, a.object, cond, f'{a.actual_g:g}', t, n, f'{ts:.3f}', *force6, wp_v])
                    if force:
                        fz.append(force[2])
                    if wp_v != '':
                        kg.append(wp_v)
                    time.sleep(a.period)
                f.flush()
                fz_g = -sum(fz) / len(fz) / 9.80665 * 1000 if fz else float('nan')
                wp_g = sum(kg) / len(kg) * 1000 if kg else float('nan')
                print(f'    세트 {s} 회차 {t:2d}/{a.trials}: Fz→ {fz_g:8.1f} g (offset 전)   workpiece {wp_g:8.1f} g', flush=True)
            print(f'    세트 {s} 끝 — 물체를 놓았다가 다시 잡는다')
    except KeyboardInterrupt:
        print('\n중단 — 지금까지 기록은 남는다')
    finally:
        f.close()
        rclpy.shutdown()
    print(f'\n저장: {out}\n요약: python3 -m gmp_dosing.core.calib {out} --method workpiece   (tool_force 도 같은 파일로)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
