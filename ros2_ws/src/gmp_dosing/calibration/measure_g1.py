#!/usr/bin/env python3
"""G1 계량 실측 — tool_force(Fz) 와 workpiece(kgf) 를 **같은 표본에서 동시에** 기록한다.

기본은 읽기만 한다 — 충돌 감지는 그대로다. 실물 모드로 ROS 가 붙어 있으면 펜던트 조그가 막힐 수 있어
`--goto-workbench`(stations.yaml 의 workbench.posx 로 movel, 느리게) 와 `--gripper`(/onrobot/sendCommand 로 열기·닫기) 를
켜면 터미널 하나로 끝난다. 둘 다 켜기 전에 로봇 주변을 비운다.
출력 CSV 는 calibration/g1_scoop133g_tool_force.csv 와 같은 열에 `작업물무게_kgf`·`시각_s` 를 더한 것이라
core/calib.py 가 `--method tool_force` / `--method workpiece` 로 두 경로를 같은 방법으로 비교한다.

준비 (터미널 1): `source tools/env.sh && ros2 launch m0609_rg2_bringup new_bringup.launch.py mode:=real host:=192.168.1.100`
  — 로봇 컨트롤러 + OnRobot 그리퍼 드라이버. cell.launch.py 는 쓰지 않는다 (skill_node 가 로봇을 움직인다).
실행 (터미널 2):
  source tools/env.sh && python3 ros2_ws/src/gmp_dosing/calibration/measure_g1.py --actual-g 133 --object scoop \\
      --goto-workbench --gripper --sets 6 --trials 30 --samples 10 --period 0.1 --out records/g1_both_$(date +%m%d).csv

두 무게를 한 파일에 (9/19 확정 — 빈 스쿱 6세트 → 원료 담고 6세트, gain 의 두 점이 된다):
  1회차  --actual-g 32  --out records/g1_both_0919.csv                 (빈 스쿱, 영점 포함)
  2회차  --actual-g <저울값> --out records/g1_both_0919.csv --no-reset   (같은 파일에 이어 쓴다. 영점은 세션 1회)
  요약   python3 -m gmp_dosing.core.calib records/g1_both_0919.csv --method workpiece   → 무게별 σ + gain·offset 직선

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
STATIONS = COMMON.with_name('stations.yaml')
COLUMNS = ['실험명', '물체종류', '측정조건', '실제총무게_g', '반복번호', '표본번호', '시각_s',
           'X축힘_N', 'Y축힘_N', 'Z축힘_N', 'X축모멘트_Nm', 'Y축모멘트_Nm', 'Z축모멘트_Nm', '작업물무게_kgf']


def robot_params():
    import yaml
    r = yaml.safe_load(COMMON.read_text())['/**']['ros__parameters']['robot']
    return r['id'], r['model'], float(r['vel']), float(r['acc']), r.get('tool_name', ''), r.get('tcp_name', '')


def workbench_posx():
    import yaml
    return [float(v) for v in yaml.safe_load(STATIONS.read_text())['workbench']['posx']]


class Gripper:
    """/onrobot/sendCommand 만 쓴다 — 'o' 열기, 'c' 닫기, '<정수>' 폭 1/10 mm. 폭 피드백은 안 본다 (사람이 눈으로)."""
    def __init__(self, rclpy):
        from onrobot_rg_msgs.srv import SetCommand
        self.rclpy, self.SetCommand = rclpy, SetCommand
        self.node = rclpy.create_node('g1_gripper_client')
        self.cli = self.node.create_client(SetCommand, '/onrobot/sendCommand')
        if not self.cli.wait_for_service(timeout_sec=5.0):
            raise RuntimeError('/onrobot/sendCommand 가 없다 — 벤더 브링업(new_bringup.launch.py mode:=real) 이 떠 있는지 확인')

    def send(self, command: str):
        fut = self.cli.call_async(self.SetCommand.Request(command=command))
        self.rclpy.spin_until_future_complete(self.node, fut, timeout_sec=5.0)
        r = fut.result()
        if r is None or not r.success:
            raise RuntimeError(f'그리퍼 명령 {command!r} 실패: {getattr(r, "message", "응답 없음")}')
        time.sleep(1.0)                    # 기구 동작 대기


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--actual-g', type=float, required=True, help='실제 저울로 잰 **들고 있는 것 전체** 무게 [g] — 빈 스쿱이면 32, 원료를 담았으면 스쿱+원료 (9/18 은 133)')
    ap.add_argument('--object', default='scoop', help='물체종류 (scoop | container | …)')
    ap.add_argument('--condition', default='', help='측정조건 표기. 비우면 <object>_total_<g>g')
    ap.add_argument('--sets', type=int, default=6)
    ap.add_argument('--trials', type=int, default=30)
    ap.add_argument('--samples', type=int, default=10)
    ap.add_argument('--period', type=float, default=0.1, help='표본 간격 [s]')
    ap.add_argument('--settle', type=float, default=1.0, help='회차 전 정착 대기 [s]')
    ap.add_argument('--out', required=True, help='CSV 경로 (records/ 는 git 밖. 확정되면 calibration/ 으로 복사)')
    ap.add_argument('--no-reset', action='store_true', help='reset_workpiece_weight 를 건너뛴다 (이미 한 세션)')
    ap.add_argument('--goto-workbench', action='store_true', help='시작 시 stations.yaml workbench.posx 로 movel (펜던트 조그 대신)')
    ap.add_argument('--vel-scale', type=float, default=0.2, help='--goto-workbench 속도 스케일')
    ap.add_argument('--gripper', action='store_true', help='/onrobot/sendCommand 로 세트마다 열기·닫기')
    ap.add_argument('--grip-width-mm', type=float, default=None, help='닫을 때 목표 폭 [mm]. 없으면 완전 닫기(c)')
    a = ap.parse_args(argv)

    import rclpy
    from gmp_skills.adapters.dsr_arm import DsrArm
    rid, model, vel, acc, tool, tcp = robot_params()
    rclpy.init()
    arm = DsrArm(rid, model, 'real', vel, acc, tool, tcp)
    arm.initialize()                       # set_tool/set_tcp — workpiece 추정은 등록된 툴 무게가 전제다
    grip = Gripper(rclpy) if a.gripper else None
    close_cmd = f'{int(round(a.grip_width_mm * 10))}' if a.grip_width_mm else 'c'
    if a.goto_workbench:
        posx = workbench_posx()
        input(f'\n[0] workbench 계량 자세 {posx} 로 이동합니다 (vel_scale {a.vel_scale}). 주변 확인 → Enter ')
        arm.movel(posx, a.vel_scale)
        print('    이동 완료')
    cond = a.condition or f'{a.object}_total_{a.actual_g:g}g'
    stamp = datetime.datetime.now().strftime('%m%d%H%M')
    out = pathlib.Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
    new = not out.exists()
    f = out.open('a', newline='', encoding='utf-8')
    w = csv.writer(f)
    if new:
        w.writerow(COLUMNS)

    if not a.no_reset:
        if grip:
            grip.send('o')
        input('\n[1] 빈 그리퍼(열림)로 계량 자세에서 정지 → Enter (reset_workpiece_weight) ')
        arm.reset_workpiece()
        print('    영점 완료')
    t0 = time.monotonic()
    try:
        for s in range(1, a.sets + 1):
            if grip:
                grip.send('o')
                input(f'\n[2] 세트 {s}/{a.sets}: 물체({a.actual_g:g} g) 를 핑거 사이에 대고 → Enter (닫는다) ')
                grip.send(close_cmd)
                input('    잡혔는지 눈으로 확인 → Enter (측정 시작) ')
            else:
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
            print(f'    세트 {s} 끝 — 물체를 놓았다가 다시 잡는다' + (' (다음 세트에서 자동으로 연다)' if grip else ''))
    except KeyboardInterrupt:
        print('\n중단 — 지금까지 기록은 남는다')
    finally:
        f.close()
        if grip:
            try:
                grip.send('o')             # 물체를 든 채 끝내지 않는다
            except Exception as e:         # noqa: BLE001
                print(f'    그리퍼 열기 실패: {e}')
        rclpy.shutdown()
    print(f'\n저장: {out}\n요약: python3 -m gmp_dosing.core.calib {out} --method workpiece   (tool_force 도 같은 파일로)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
