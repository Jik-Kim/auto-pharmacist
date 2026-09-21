#!/usr/bin/env python3
"""G1 계량 실측 — tool_force(Fz) 와 workpiece(kgf) 를 **같은 표본에서 동시에** 기록한다.

기본은 읽기만 한다 — 충돌 감지는 그대로다. 실물 모드로 ROS 가 붙어 있으면 펜던트 조그가 막힐 수 있어
`--goto-station <STATION_ID>`(stations.yaml 의 <STATION_ID>.posx 로 movel, 느리게) 와 `--gripper`(/onrobot/sendCommand 로
열기·닫기) 를 켜면 터미널 하나로 끝난다. 둘 다 켜기 전에 로봇 주변을 비운다.
**보정값은 반드시 실제 weigh_held 가 재는 자세(`material_1/2/3`)에서 잰다** — `workbench`는 자세(orientation)가
달라(`skill_node._do_weigh_held`가 이동하는 `material_N.posx` 참고) tool_force 의 JTS 기반 편향이 안 옮겨간다.
자세마다 편향이 다르므로 **한 세션은 한 자세로 끝낸다** — 2026-09-21 재작업은 `material_1` 기준이다.
출력 CSV 는 폐기한 9/18 파일과 같은 열에 `작업물무게_kgf`·`시각_s` 를 더한 것이라
core/calib.py 가 `--method tool_force` / `--method workpiece` 로 두 경로를 같은 방법으로 비교한다.

준비 (터미널 1): `source tools/env.sh && ros2 launch m0609_rg2_bringup new_bringup.launch.py mode:=real host:=192.168.1.100`
  — 로봇 컨트롤러 + OnRobot 그리퍼 드라이버. cell.launch.py 는 쓰지 않는다 (skill_node 가 로봇을 움직인다).
실행 (터미널 2) — 2026-09-21 영점 재작업은 **material_1 에서 페이즈당 5회**로 짧게 끊어 돈다.
절차·확인 항목·결과표는 같은 폴더의 README.md 에 있다 (9/18·19 측정 근거는 폐기됨).
  페이즈 1  --actual-g 32 --goto-station material_1 --gripper --sets 1 --trials 5 --samples 10 --period 0.1 \\
              --out records/g1_rezero_0921_material1.csv            (빈 스쿱, 영점 포함)
  페이즈 2+ --actual-g <저울값> --gripper --no-reset --sets 1 --trials 5 --samples 10 --period 0.1 \\
              --out records/g1_rezero_0921_material1.csv            (같은 파일에 이어 쓴다. 영점은 세션 1회, 자세 유지)
  요약      python3 -m gmp_dosing.core.calib records/g1_rezero_0921_material1.csv --method tool_force
무게는 3점 이상 떠야 gain 직선의 잔차가 의미를 가진다 (fit_gain 이 2점이면 경고한다).

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

def _params_dir() -> pathlib.Path:
    """gmp_bringup/params — 설치본(share) 우선, 없으면 소스 트리 (calibration/ → gmp_dosing → src)."""
    try:
        from ament_index_python.packages import get_package_share_directory
        d = pathlib.Path(get_package_share_directory('gmp_bringup')) / 'params'
        if (d / 'common.yaml').exists():
            return d
    except Exception:  # noqa: BLE001 — 설치 안 됐거나 환경 미설정
        pass
    return HERE.parents[1] / 'gmp_bringup' / 'params'


COMMON = _params_dir() / 'common.yaml'
STATIONS = COMMON.with_name('stations.yaml')
COLUMNS = ['실험명', '물체종류', '측정조건', '실제총무게_g', '반복번호', '표본번호', '시각_s',
           'X축힘_N', 'Y축힘_N', 'Z축힘_N', 'X축모멘트_Nm', 'Y축모멘트_Nm', 'Z축모멘트_Nm', '작업물무게_kgf']


def robot_params():
    import yaml
    r = yaml.safe_load(COMMON.read_text())['/**']['ros__parameters']['robot']
    return r['id'], r['model'], float(r['vel']), float(r['acc']), r.get('tool_name', ''), r.get('tcp_name', '')


def station_posx(station_id: str):
    import yaml
    st = yaml.safe_load(STATIONS.read_text())
    s = st['stations'][station_id]
    print(f"    stations.yaml({st.get('frame')} frame) {station_id}: {s.get('note', '')}")
    return [float(v) for v in s['posx']]


STATES = {0: 'INITIALIZING', 1: 'STANDBY', 2: 'MOVING', 3: 'SAFE_OFF', 4: 'TEACHING', 5: 'SAFE_STOP',
          6: 'EMERGENCY_STOP', 7: 'HOMMING', 8: 'RECOVERY', 9: 'SAFE_STOP2', 10: 'SAFE_OFF2'}


def wait_controller(arm, rclpy, timeout_s: float):
    """컨트롤러가 실제로 응답하는지 먼저 확인한다 — **없으면 조용히 멈춘다.**

    DSR_ROBOT2 의 설정·조회 함수는 wait_for_service 없이 call_async 부터 하고
    spin_until_future_complete 로 기다린다. 컨트롤러 활성화 전에 부르면 future 가 끝나지 않아
    아무 출력 없이 영원히 선다 (gmp_skills/adapters/dsr_arm.py 의 initialize 주석과 같은 사유).
    dsr_arm.initialize() 는 move_stop 서비스로 이 확인을 하지만 여기서는 setup_tool 을 직접 부르므로
    그 확인이 빠져 있었다 — 브링업을 띄우자마자 실행하면 걸린다 (9/21 실제로 걸렸다).

    wait_for_service 는 '그래프에 광고됐다' 까지만 보므로, 타임아웃을 걸고 한 번 실제로 부른다.
    """
    from dsr_msgs2.srv import GetRobotMode
    t0 = time.monotonic()
    cli = arm.node.create_client(GetRobotMode, 'dsr_controller2/system/get_robot_mode')
    if not cli.wait_for_service(timeout_sec=timeout_s):
        raise TimeoutError(
            f'{timeout_s:.0f} s 안에 dsr_controller2/system/get_robot_mode 가 안 보인다 — '
            '브링업(new_bringup.launch.py mode:=real)이 떠 있는지, ROS_DOMAIN_ID 가 같은지 확인 '
            '(source tools/env.sh 하면 70)')
    left = max(5.0, timeout_s - (time.monotonic() - t0))
    fut = cli.call_async(GetRobotMode.Request())
    rclpy.spin_until_future_complete(arm.node, fut, timeout_sec=left)
    if fut.result() is None:
        raise TimeoutError(
            '컨트롤러가 응답하지 않는다 — 서비스는 떴지만 dsr_controller2 가 아직 active 가 아니거나 '
            '로봇 연결이 끊겼다. `ros2 control list_controllers -c /dsr01/controller_manager` 로 active 확인 후 다시')
    print(f'    컨트롤러 준비 확인 ({time.monotonic() - t0:.1f} s)')


def setup_tool(arm, tool: str, tcp: str, need_motion: bool):
    """initialize() 대신 — 상태를 먼저 보여 주고, 이미 선택된 툴·TCP 면 set 을 건너뛴다.
    workpiece 추정은 컨트롤러에 등록된 툴 무게가 전제라 툴이 맞는지가 핵심이다 (T0: 펜던트에서 tool_weight / GripperDA_v1 선택)."""
    R = arm.R
    mode, state = R.get_robot_mode(), R.get_robot_state()
    tool_now, tcp_now = R.get_tool(), R.get_tcp()
    print(f"로봇 mode={mode} ({'자동' if mode == 1 else '수동' if mode == 0 else '?'})  "
          f"state={state} ({STATES.get(state, '?')})  tool={tool_now!r}  tcp={tcp_now!r}")
    if mode != 1:
        print('  ⚠ 펜던트가 Auto 모드가 아니다 — set_tool/이동이 거부된다. T0: Auto 모드 + 서보 ON 후 다시')
    for what, want, now, fn in (('tool', tool, tool_now, R.set_tool), ('tcp', tcp, tcp_now, R.set_tcp)):
        if not want:
            continue
        if now == want:
            print(f'  {what} {want!r} 이미 선택됨 — set 생략')
            continue
        r = fn(want)
        if r == 0:
            print(f'  set_{what}({want!r}) OK')
        else:
            print(f'  ⚠ set_{what}({want!r}) 실패 return={r}. 펜던트에 그 이름으로 등록돼 있는지, Auto 모드인지 확인.')
            input(f'    현재 {what}={now!r} 그대로 계속하려면 Enter (workpiece 값이 어긋날 수 있다), 중단은 Ctrl-C ')
    if need_motion:                         # --goto-station 때만 속도 상한을 건다
        for name, r in (('set_velx', R.set_velx(arm.vel, arm.vel)), ('set_accx', R.set_accx(arm.acc, arm.acc))):
            if r != 0:
                raise RuntimeError(f'{name} 실패 return={r} — Auto 모드·서보 ON 확인')


def baseline(arm, n: int, period: float):
    """빈 그리퍼 기준값 — workpiece 가 여기서 0 근처가 아니면 컨트롤러 추정에 편향이 있다 (등록 툴 질량·CoG 확인)."""
    fz, kg = [], []
    for _ in range(n):
        f = arm.tool_force(); w = arm.R.get_workpiece_weight()
        if f:
            fz.append(f[2])
        if isinstance(w, (int, float)) and w >= 0:
            kg.append(float(w))
        time.sleep(period)
    fz_g = -sum(fz) / len(fz) / 9.80665 * 1000 if fz else float('nan')
    wp_g = sum(kg) / len(kg) * 1000 if kg else float('nan')
    print(f'    빈 그리퍼 기준값 ({n}표본): Fz→ {fz_g:.1f} g (offset 전)   workpiece {wp_g:.1f} g'
          + ('   ⚠ 빈 상태인데 0 이 아니다 — 등록 툴 무게와 실제가 다르거나 영점 미적용' if kg and abs(wp_g) > 50 else ''))


def probe(arm, grip, close_cmd, sec: float, actual_g: float):
    """workpiece 추정기의 거동을 본다 — reset 뒤 값이 수렴하는지, 빈 상태 편향이 얼마인지, 물체를 잡으면 얼마나 반응하는지."""
    import rclpy
    try:
        if grip:
            grip.send('o')
        input(f'\n[probe] 빈 그리퍼로 계량 자세에서 정지 → Enter (reset 후 {sec:.0f} s 관찰) ')
        r = arm.reset_workpiece()
        print(f'    reset_workpiece_weight return={r!r}')
        _watch(arm, sec, '빈')
        if grip:
            input(f'[probe] 물체({actual_g:g} g) 를 핑거 사이에 대고 → Enter (닫는다) ')
            grip.send(close_cmd)
        else:
            input(f'[probe] 물체({actual_g:g} g) 를 잡고 정지 → Enter ')
        _watch(arm, sec, f'{actual_g:g} g 파지')
        input('[probe] 리셋 없이 자세를 바꿔(툴이 옆을 향하게 등) 정지 → Enter (자세 의존 편향 확인, 건너뛰려면 Ctrl-C) ')
        _watch(arm, sec, '다른 자세')
    except KeyboardInterrupt:
        print('\nprobe 종료')
    finally:
        if grip:
            try:
                grip.send('o')
            except Exception as e:      # noqa: BLE001
                print(f'    그리퍼 열기 실패: {e}')
        rclpy.shutdown()
    return 0


def _watch(arm, sec: float, label: str):
    t0 = time.monotonic()
    print(f'    --- {label}: t[s]  Fz→g(offset 전)  workpiece[g]')
    while time.monotonic() - t0 < sec:
        f = arm.tool_force(); w = arm.R.get_workpiece_weight()
        fz_g = -f[2] / 9.80665 * 1000 if f else float('nan')
        wp_g = float(w) * 1000 if isinstance(w, (int, float)) else float('nan')
        print(f'    {time.monotonic() - t0:5.1f}  {fz_g:8.1f}  {wp_g:8.1f}', flush=True)
        time.sleep(0.5)


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
    ap.add_argument('--no-workpiece', action='store_true',
                    help='get_workpiece_weight 를 안 부른다 (호출 0.7 s — 9/19 실측). 빠른 표본 간격으로 센서 갱신 주기를 잴 때')
    ap.add_argument('--probe', type=float, default=0.0, metavar='SEC',
                    help='진단만: 리셋 직후 빈 그리퍼로 SEC 초, 물체를 잡고 SEC 초 동안 0.5 s 마다 두 경로 값을 찍는다 (CSV 안 씀)')
    ap.add_argument('--goto-station', default='', metavar='STATION_ID',
                    help="시작 시 stations.yaml <STATION_ID>.posx 로 movel (펜던트 조그 대신). "
                         "예: workbench(용기 계량) | material_1/2/3(weigh_held 가 실제로 재는 자세 — "
                         "calibration 은 이 자세로 해야 gain/offset 이 운영과 맞는다)")
    ap.add_argument('--vel-scale', type=float, default=0.2, help='--goto-station 속도 스케일')
    ap.add_argument('--offset-mm', default='', metavar='DX,DY,DZ',
                    help='--goto-station 좌표에 더할 [mm] — 실물 위치가 바뀌었는데 stations.yaml 이 '
                         '아직 반영 전(PR 대기)일 때 임시 보정. 예: 100,0,0')
    ap.add_argument('--controller-timeout', type=float, default=30.0, metavar='SEC',
                    help='dsr_controller2 응답 대기 한도 [s]. 브링업 직후엔 컨트롤러 활성화에 시간이 걸린다')
    ap.add_argument('--gripper', action='store_true', help='/onrobot/sendCommand 로 세트마다 열기·닫기')
    ap.add_argument('--grip-width-mm', type=float, default=None, help='닫을 때 목표 폭 [mm]. 없으면 완전 닫기(c)')
    a = ap.parse_args(argv)

    import rclpy
    from gmp_skills.adapters.dsr_arm import DsrArm
    rid, model, vel, acc, tool, tcp = robot_params()
    rclpy.init()
    arm = DsrArm(rid, model, 'real', vel, acc, tool, tcp)
    wait_controller(arm, rclpy, a.controller_timeout)
    setup_tool(arm, tool, tcp, bool(a.goto_station))
    grip = Gripper(rclpy) if a.gripper else None
    close_cmd = f'{int(round(a.grip_width_mm * 10))}' if a.grip_width_mm else 'c'
    if a.goto_station:
        posx = station_posx(a.goto_station)
        if a.offset_mm:
            dx, dy, dz = (float(v) for v in a.offset_mm.split(','))
            posx = [posx[0] + dx, posx[1] + dy, posx[2] + dz, *posx[3:]]
            print(f'    offset ({dx:g}, {dy:g}, {dz:g}) mm 적용 → {posx}')
        input(f'\n[0] {a.goto_station} 계량 자세 {posx} 로 이동합니다 (vel_scale {a.vel_scale}). 주변 확인 → Enter ')
        arm.movel(posx, a.vel_scale)
        print('    이동 완료')
    if a.probe > 0:
        return probe(arm, grip, close_cmd, a.probe, a.actual_g)
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
        r = arm.reset_workpiece()
        print(f'    reset_workpiece_weight return={r!r}' + ('  OK' if r == 0 else '  ⚠ 실패 — workpiece 영점이 안 잡혔다'))
        time.sleep(a.settle)
        baseline(arm, 10, a.period)
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
                    wp = None if a.no_workpiece else arm.R.get_workpiece_weight()
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
