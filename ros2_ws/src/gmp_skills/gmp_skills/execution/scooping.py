"""스쿱으로 원료를 퍼내고, 용기에 붓거나 원료통에 반환하는 이동 순서.

Scoop Action은 스쿱을 든 상태에서 시작해 원료별 계량 자세로 돌아온다.
실제 무게 측정은 별도 WeighHeld Action이 담당한다. 현재 고정 티칭 경로는
미리 저장한 BASE 좌표의 5개 경유점을 그대로 지나며 접촉력·깊이는 재지 않는다.
"""
import uuid
import csv
import json
import math
import time
from pathlib import Path

from .context import Job
from gmp_skills.core.scooping import finite, plan_scoop, tip_offset_local, tip_z
from gmp_skills.core.surface_height import tip_position_base
from gmp_skills.core.transfer import vector6


class ScoopingSkills:
    def __init__(self, ctx, motion, safety, runtime):
        # 노드 전체 대신 필요한 장치·설정·상태·콜백만 공유한다.
        self.ctx = ctx
        self.motion = motion
        self.safety = safety
        self.runtime = runtime

    def _do_scoop(self, job: Job):
        """스쿱 파지와 인출 완료를 확인하고 설정된 스쿠핑 방식으로 실행한다.

        taught_fixed는 저장된 경유점을 그대로 쓰고, height_compensated는
        측정한 원료면 높이에 맞춰 경유점을 보정한다. height_measure_only는
        원료면 측정만 수행한다. 설정되지 않은 높이 보정은 이동 전에 거부한다.
        """
        if getattr(self.ctx.state, 'return_rescoop_blocked', False):
            raise RuntimeError('반환 후 재스쿱 연결 경로 미구현: 자동 Scoop을 차단합니다')
        self.motion._require_scoop_extracted()
        material = job.args['material_id']
        self.motion._require_held_scoop(material)
        if getattr(self.ctx.config, 'height_measure_only', False):
            return self._measure_surface_world(job)
        profile = self.ctx.stations.scooping.get(material)
        # 시연용 고정 티칭 경로는 원료면 측정·높이 보정 없이 바로 실행한다.
        if profile and profile.get('execution_mode', 'height_compensated') == 'taught_fixed':
            return self._do_fixed_scoop(job, profile)
        if profile and profile.get('execution_mode', 'height_compensated') != 'height_compensated':
            raise ValueError('알 수 없는 스쿠핑 실행 모드')
        if not profile or profile.get('calibrated') is not True:
            raise ValueError('스쿠핑 경로/스쿱 끝 높이 보정 미확인: 원료별 보정 후 실행 필요')
        fraction = finite(job.args['depth_fraction'], 'depth_fraction')
        station = self.ctx.stations.for_material(material)
        cancel = lambda: job.cancel or self.runtime._cancel_requested()
        if cancel():
            raise RuntimeError('cancelled')
        # BASE는 로봇 바닥 기준, WORLD는 작업 셀 기준 좌표다. 두 좌표계가
        # 같다고 가정하지 않고 첫 이동 전에 티칭점과 스쿱 끝 오프셋을 변환한다.
        reference = self.ctx.arm.transform_pose(profile['reference_pose_base'], to_world=True)
        offset = tip_offset_local(reference, profile['tip_offset_world_mm'])
        points = [self.ctx.arm.transform_pose(p, to_world=True) for p in profile['waypoints_base']]
        shake = self.ctx.arm.transform_pose(profile['shake_base'], to_world=True)
        plan_scoop(profile, points, offset, profile['reference_surface_world_z_mm'], fraction)
        vel = [finite(v, 'spline 속도') * self.ctx.config.vel_scale for v in profile['velocity']]
        acc = [finite(v, 'spline 가속도') * self.ctx.config.vel_scale for v in profile['acceleration']]
        if len(vel) != 2 or len(acc) != 2 or min(vel + acc) <= 0:
            raise ValueError('spline 속도/가속도는 양수 2개여야 한다')
        amp = vector6(profile['shake_amp'], '털기 진폭')
        period = vector6(profile['shake_period'], '털기 주기')
        atime = finite(profile['shake_atime'], '털기 가속시간')
        repeat = profile['shake_repeat']
        if (atime <= 0 or type(repeat) is not int or repeat <= 0
                or any(t < 0 or (a != 0 and t <= 0) for a, t in zip(amp, period))
                or not any(amp)):
            raise ValueError('털기 주기/반복 설정 오류')
        result = self._do_check_depth(job)
        contact = result.get('contact_pose_base')
        if contact is None:
            raise RuntimeError('원료면 접촉 미검출: 스쿠핑을 실행하지 않습니다')
        surface = tip_z(self.ctx.arm.transform_pose(contact, to_world=True), offset)
        plan = plan_scoop(profile, points, offset, surface, fraction)
        targets = [self.ctx.arm.transform_pose(p, to_world=False) for p in plan.world_poses]
        shake[2] += plan.shift_mm
        if tip_z(shake, offset) < plan.floor_z:
            raise ValueError('털기 위치가 스쿱 끝 높이 하한을 침범한다')
        shake_target = self.ctx.arm.transform_pose(shake, to_world=False)
        self.ctx.logger().info(
            f'[SCOOP_PLAN] material={material} surface_world_z={surface:.2f} '
            f'depth_mm={plan.depth_mm:.2f} fraction={fraction:.3f} '
            f'predicted_g={plan.predicted_g:.2f} shift_mm={plan.shift_mm:.2f}')

        def observe():
            # 이동 대기 중 반복 호출된다. 스쿱이 계속 잡혀 있는지와 스쿱 끝이
            # 계산된 바닥 높이 아래로 내려가지 않았는지 확인한다.
            self.motion._require_held_scoop(material)
            world = self.ctx.arm.transform_pose(self.ctx.arm.current_posx(), to_world=True)
            if tip_z(world, offset) < plan.floor_z:
                raise RuntimeError('스쿠핑 중 스쿱 끝 높이 하한 침범')

        # 취소·미도달·관측 실패 시 어댑터가 정지하고 다음 이동은 수행하지 않는다.
        job.feedback and job.feedback('DIP', True, result['max_contact_force_n'],
                                      result['insertion_depth_mm'])
        # MOVESX · TCP 스플라인: targets
        self.ctx.arm.movesx_cancellable(targets, vel, acc, cancel, self.ctx.config.motion_timeout_s,
                                    observer=observe)
        # MOVEL · TCP 직선 이동: shake_target
        self.ctx.arm.movel_cancellable(shake_target, self.ctx.config.vel_scale, cancel,
                                   self.ctx.config.motion_timeout_s, observer=observe)
        if cancel():
            raise RuntimeError('cancelled')
        job.feedback and job.feedback('LEVEL', True, result['max_contact_force_n'],
                                      result['insertion_depth_mm'])
        try:
            self.ctx.arm.amove_periodic(list(amp), list(period), atime, repeat, ref_tool=False)
            self.ctx.arm.wait_motion_cancellable(cancel, self.ctx.config.motion_timeout_s, observer=observe)
            if not self.motion._pose_matches(self.ctx.arm.current_posx(), shake_target):
                raise RuntimeError('털기 종료 자세 미확인')
        except Exception:
            self.ctx.arm.stop_motion()
            raise
        # MOVEL · TCP 직선 이동: station.posx
        self.ctx.arm.movel_cancellable(station.posx, self.ctx.config.vel_scale, cancel,
                                   self.ctx.config.motion_timeout_s, observer=observe)
        job.feedback and job.feedback('LIFT', True, result['max_contact_force_n'],
                                      result['insertion_depth_mm'])
        return result

    def _do_fixed_scoop(self, job, profile):
        """저장된 5점 스플라인으로 퍼내고 털기 후 원료 계량 자세로 복귀한다.

        이 경로는 depth_fraction=1만 허용한다. 접촉 센서 결과나 삽입 깊이를
        측정하지 않으므로 반환하는 0은 실제 접촉력·깊이 측정값이 아니다.
        """
        fixed = profile.get('fixed_path', {})
        if fixed.get('verified') is not True or self.ctx.stations.frame != 'base':
            raise ValueError('BASE 고정 경로의 실물 검증 확인이 필요하다')
        fraction = finite(job.args['depth_fraction'], 'depth_fraction')
        if fraction != 1.0:
            raise ValueError('고정 티칭 경로는 depth_fraction=1.0만 지원한다')
        # 1) 이동 전 검증: 검증된 BASE 경로·full 요청·5점·속도·주기 운동 설정.
        points = [list(vector6(p, '고정 경유점')) for p in fixed['waypoints_base']]
        if len(points) != 5:
            raise ValueError('고정 스쿠핑은 검증된 경유점 5개가 필요하다')
        shake = list(vector6(fixed['shake_base'], '털기 위치'))
        vel = [finite(v, '속도') * self.ctx.config.vel_scale for v in fixed['velocity']]
        acc = [finite(v, '가속도') * self.ctx.config.vel_scale for v in fixed['acceleration']]
        amp = list(vector6(fixed['shake_amp'], '털기 진폭'))
        period = list(vector6(fixed['shake_period'], '털기 주기'))
        atime, repeat = finite(fixed['shake_atime'], '털기 가속시간'), fixed['shake_repeat']
        if (len(vel) != 2 or len(acc) != 2 or min(vel + acc) <= 0
                or atime <= 0 or type(repeat) is not int or repeat <= 0
                or not any(amp) or any(t < 0 or (a != 0 and t <= 0) for a, t in zip(amp, period))):
            raise ValueError('고정 경로 속도/주기 운동 설정 오류')
        material = job.args['material_id']
        station = self.ctx.stations.for_material(material)
        cancel = lambda: job.cancel or self.runtime._cancel_requested()

        def observe():
            # 스플라인·직선 이동·털기 대기 중 파지 상태를 반복 확인한다.
            self.motion._require_held_scoop(material)

        if cancel():
            raise RuntimeError('cancelled')
        if not self.motion._pose_matches(self.ctx.arm.current_posx(), station.posx):
            raise RuntimeError('고정 스쿠핑은 해당 원료 계량 자세에서 시작해야 한다')
        observe()
        job.feedback and job.feedback('DIP')
        # 2) material_N 계량 자세에서 시작해 다섯 경유점을 잇는 곡선으로 퍼낸다.
        # 이후 설정된 털기 시작 위치로 직선 이동한다.
        # MOVESX · TCP 스플라인: points
        self.ctx.arm.movesx_cancellable(points, vel, acc, cancel, self.ctx.config.motion_timeout_s, observer=observe)
        # MOVEL · TCP 직선 이동: shake
        self.ctx.arm.movel_cancellable(shake, self.ctx.config.vel_scale, cancel, self.ctx.config.motion_timeout_s, observer=observe)
        if cancel():
            raise RuntimeError('cancelled')
        job.feedback and job.feedback('LEVEL')
        try:
            # 3) BASE 기준으로 설정된 폭·주기의 반복 흔들기다. 명령 응답뿐 아니라
            # 로봇 정지와 목표 TCP 도달도 확인하고, 실패하면 감속 정지한다.
            self.ctx.arm.amove_periodic(amp, period, atime, repeat, ref_tool=False)
            self.ctx.arm.wait_motion_cancellable(cancel, self.ctx.config.motion_timeout_s, observer=observe)
            if not self.motion._pose_matches(self.ctx.arm.current_posx(), shake):
                raise RuntimeError('털기 종료 자세 미확인')
        except Exception:
            self.ctx.arm.stop_motion()
            raise
        job.feedback and job.feedback('LIFT')
        # MOVEL · TCP 직선 이동: station.posx
        self.ctx.arm.movel_cancellable(station.posx, self.ctx.config.vel_scale, cancel,
                                   self.ctx.config.motion_timeout_s, observer=observe)
        # 4) Scoop은 material_N으로 돌아오기까지만 한다. 이어지는 WeighHeld가 계량한다.
        # 고정 경로는 접촉을 측정하지 않으므로 아래 false/0을 실측 결과로 해석하지 않는다.
        return dict(contact_detected=False, max_contact_force_n=0.0, insertion_depth_mm=0.0,
                    message='TAUGHT_FIXED: 검증된 full 경로 완료; 접촉력·삽입 깊이 미측정')

    def _measure_surface_world(self, job: Job):
        """접촉 순간의 TCP와 스쿱 끝 오프셋으로 원료면 높이를 추정한다.

        진단용 경로만 이동하며 원료를 퍼내지 않는다. 접촉 위치는 BASE에서 읽고
        WORLD 좌표로 바꾼 뒤 스쿱 끝 높이를 계산한다.
        """
        material = job.args['material_id']
        profile = self.ctx.stations.scooping.get(material)
        if not profile:
            raise ValueError('원료별 스쿱 끝 오프셋 설정이 필요하다')
        reference = self.ctx.arm.transform_pose(profile['reference_pose_base'], to_world=True)
        offset = tip_offset_local(reference, profile['tip_offset_world_mm'])
        result = self._do_check_depth(job)
        contact = result.get('contact_pose_base')
        if contact is None:
            raise RuntimeError('원료면 접촉 미검출: WORLD 원료 높이를 계산할 수 없습니다')
        world = self.ctx.arm.transform_pose(contact, to_world=True)
        z = tip_z(world, offset)
        message = (f'HEIGHT_MEASUREMENT_ONLY material={material} '
                   f'contact_base={contact} contact_world={world} '
                   f'tip_offset_local={list(offset)} surface_world_z_mm={z:.3f} '
                   f'max_contact_force_n={result["max_contact_force_n"]:.3f}; '
                   '스쿠핑 미실행, 근사 오프셋으로 계산한 접촉 지점 높이')
        self.ctx.logger().info(message)
        result.update(diagnostic_only=True, measurement_message=message)
        return result

    def _wait_compliance_settle(self, job: Job, duration_s: float):
        """로봇을 외력에 반응하는 순응 제어로 바꾼 뒤 설정 시간만큼 기다린다.

        컨트롤러의 전환 응답 직후 이동하지 않기 위한 대기이며, 도중 취소를 확인한다.
        """
        deadline = self.ctx.now() + duration_s
        while True:
            if job.cancel or self.runtime._cancel_requested():
                raise RuntimeError('cancelled')
            remaining = deadline - self.ctx.now()
            if remaining <= 0:
                return
            time.sleep(min(0.02, remaining))

    def _do_check_depth(self, job: Job):
        """빈 스쿱 때의 Fz와 현재 Fz 차이로 원료면 접촉을 찾는다.

        material_N.posx에서 measure_posx로 접근하고 힘 차이가 임계값에
        닿으면 이동을 감속 정지한다. 성공한 경우에만 출발 계량 자세로 돌아온다.
        순응 제어는 실패·취소에도 finally에서 해제한다.
        """
        if getattr(self.ctx.state, 'return_rescoop_blocked', False):
            raise RuntimeError('반환 후 재스쿱 연결 경로 미구현: 자동 Scoop을 차단합니다')
        self.motion._require_scoop_extracted()
        self.motion._require_held_scoop(job.args['material_id'])
        p = self.ctx.parameter
        settle_s = float(p('safety.compliance_settle_s').value)
        if not math.isfinite(settle_s) or not 0 < settle_s <= self.ctx.config.motion_timeout_s:
            raise ValueError('순응 전환 대기는 양수이며 이동 제한 시간 이하여야 합니다')
        station = self.ctx.stations.for_material(job.args['material_id'])
        baseline = getattr(self.ctx.state, 'empty_scoop_force_baseline', None)
        if (baseline is None or baseline['material_id'] != job.args['material_id']
                or baseline['station_id'] != station.station_id
                or baseline['safety_revision'] != getattr(self.ctx.state, 'safety_revision', 0)
                or not self.motion._pose_matches(baseline['pose'], station.posx)):
            raise RuntimeError('현재 파지·자세의 유효한 빈 스쿱 Fz 계량 기준이 필요합니다')
        baseline_fz = baseline['fz_mean_n']
        if not math.isfinite(baseline_fz):
            raise ValueError('빈 스쿱 기준 Fz가 유효하지 않습니다')
        # 원료에 닿은 뒤의 힘을 빈 스쿱 기준 Fz로 덮어쓰지 않는다.
        self.ctx.state.empty_scoop_baseline_pending = False
        target = self.motion._pose_from_extra(station, 'measure_posx')
        start = list(station.posx)
        reference_station = self.ctx.stations.get(p('height_measurement.reference_station').value)
        reference = list(reference_station.posx)
        tip_offset = list(reference_station.extra['scoop_tip_offset_base_mm'])
        trace_only = bool(p('height_measurement.force_trace_only').value)
        trace_hz = float(p('height_measurement.trace_hz').value)
        if not math.isfinite(trace_hz) or trace_hz <= 0:
            raise ValueError('힘 기록 주파수는 유한한 양수여야 합니다')
        contact_threshold = float(p('safety.fz_max_n').value)
        if not math.isfinite(contact_threshold) or contact_threshold <= 0:
            raise ValueError('접촉 판정 힘은 유한한 양수여야 합니다')
        # 기준 TCP·스쿱 끝 오프셋으로 끝 위치를 계산할 수 있는지 이동 전에 확인한다.
        tip_position_base(start, reference, tip_offset)
        if job.cancel:
            raise RuntimeError('cancelled')
        job.feedback and job.feedback('APPROACH')
        # MOVEL · TCP 직선 이동: start
        self.ctx.arm.movel(start, self.ctx.config.vel_scale)
        if job.cancel:
            raise RuntimeError('cancelled')
        contact_z = None
        contact_pose = None
        max_force_n = 0.0
        insertion_mm = 0.0
        trace_file = None
        trace_writer = None
        trace_path = None
        trace_start = self.ctx.now()
        next_trace_at = trace_start
        phase = 'BEFORE_COMPLIANCE'
        if trace_only:
            directory = Path(p('height_measurement.trace_directory').value).expanduser().resolve()
            directory.mkdir(parents=True, exist_ok=True)
            trace_path = directory / f'force_{station.station_id}_{uuid.uuid4().hex}.csv'
            trace_file = trace_path.open('x', newline='', buffering=1)
            trace_writer = csv.writer(trace_file)
            trace_writer.writerow(['ros_time_s', 'elapsed_s', 'force_read_end_s', 'pose_read_end_s',
                'frame', 'phase', 'Fx_N', 'Fy_N', 'Fz_N', 'Mx_Nm', 'My_Nm', 'Mz_Nm',
                'tcp_x_mm', 'tcp_y_mm', 'tcp_z_mm', 'tcp_a_deg', 'tcp_b_deg', 'tcp_c_deg',
                'baseline_fz_N', 'delta_fz_N', 'threshold_N', 'contact_detected'])
            self.ctx.logger().info(f'[FORCE_TRACE_CSV] {trace_path}')

        def observe_depth():
            # 이동 대기 중 현재 Fz와 TCP를 읽는다. 최초 접촉 시 TCP Z를 저장하고,
            # 그 뒤 현재 Z와의 차이를 삽입 깊이로 기록한다.
            nonlocal contact_z, contact_pose, max_force_n, insertion_mm, next_trace_at
            sample_at = self.ctx.now()
            if trace_only:
                if sample_at < next_trace_at:
                    return
                next_trace_at += (math.floor((sample_at - next_trace_at) * trace_hz) + 1) / trace_hz
            force = self.ctx.arm.tool_force()
            if force is None or len(force) != 6 or not all(math.isfinite(float(v)) for v in force):
                raise RuntimeError('깊이 측정 외력 조회 실패')
            force_read_end = self.ctx.now()
            current = self.ctx.arm.current_posx()
            pose_read_end = self.ctx.now()
            if len(current) != 6 or not all(math.isfinite(float(v)) for v in current):
                raise RuntimeError('깊이 측정 자세 조회 실패')
            max_force_n = max(max_force_n, abs(float(force[2])))
            delta_fz = float(force[2]) - baseline_fz
            if phase != 'RETURN' and contact_z is None and abs(delta_fz) >= contact_threshold:
                contact_z = float(current[2])
                contact_pose = list(current)
                # 이후 이동 실패·취소가 생겨도 최초 접촉 표본은 진단 로그에 남긴다.
                self.ctx.logger().info('[SURFACE_CONTACT_BASE] ' + json.dumps({
                    'baseline_fz_n': baseline_fz,
                    'contact_fz_n': float(force[2]), 'delta_fz_n': delta_fz,
                    'contact_tcp_posx': contact_pose,
                    'tip_position_mm': tip_position_base(contact_pose, reference, tip_offset),
                    'approximate_offset': True,
                }, ensure_ascii=False))
            insertion_mm = 0.0 if contact_z is None else abs(float(current[2]) - contact_z)
            if trace_writer is not None:
                trace_writer.writerow([sample_at, sample_at - trace_start, force_read_end, pose_read_end,
                    'BASE', phase, *force, *current, baseline_fz, delta_fz, contact_threshold,
                    contact_z is not None])
                trace_file.flush()
            job.feedback and job.feedback('DIP', contact_z is not None, abs(float(force[2])), insertion_mm)

        try:
            try:
                if trace_only:
                    observe_depth()
                self.ctx.arm.compliance_on(list(p('safety.compliance_stx').value))
                self._wait_compliance_settle(job, settle_s)
                phase = 'APPROACH'
                # 스테이션 설정의 measure_posx 전체(XYZ·회전)를 목표로 이동한다.
                # observe_depth가 접촉을 찾으면 stop_requested가 이동 중 정지를 요청한다.
                # MOVEL · TCP 직선 이동: target
                self.ctx.arm.movel_cancellable(
                    target, self.ctx.config.vel_scale, lambda: job.cancel or self.runtime._cancel_requested(),
                    self.ctx.config.motion_timeout_s, observer=observe_depth,
                    stop_requested=lambda: not trace_only and contact_pose is not None)
                if (trace_only or contact_pose is None) and not self.motion._pose_matches(self.ctx.arm.current_posx(), target):
                    raise RuntimeError('깊이 측정 목표 자세 미도달')
            finally:
                self.ctx.arm.compliance_off()
            if job.cancel or self.runtime._cancel_requested():
                raise RuntimeError('cancelled')
            # 접촉 감지 또는 진단 경로가 정상 종료된 경우에만 출발 계량 자세로
            # 되돌아간다. 실패·취소 시 현재 위치에서 멈추고 임의로 복귀하지 않는다.
            if trace_only:
                phase = 'RETURN'
                # MOVEL · TCP 직선 이동: start
                self.ctx.arm.movel_cancellable(start, self.ctx.config.vel_scale,
                    lambda: job.cancel or self.runtime._cancel_requested(), self.ctx.config.motion_timeout_s,
                    observer=observe_depth)
            else:
                # MOVEL · TCP 직선 이동: start
                self.ctx.arm.movel(start, self.ctx.config.vel_scale)
            job.feedback and job.feedback('LIFT', contact_z is not None, max_force_n, insertion_mm)
            measurement = {
                'frame': 'BASE', 'baseline_fz_n': baseline_fz, 'csv_path': str(trace_path) if trace_path else None,
                'contact_tcp_posx': contact_pose,
                'tip_position_mm': (tip_position_base(contact_pose, reference, tip_offset)
                                    if contact_pose is not None else None),
                'approximate_offset': True,
            }
            message = json.dumps(measurement, ensure_ascii=False)
            self.ctx.logger().info(f'[SURFACE_HEIGHT_BASE] {message}')
            return {'contact_detected': contact_z is not None, 'max_contact_force_n': max_force_n,
                    'insertion_depth_mm': insertion_mm, 'contact_pose_base': contact_pose,
                    'message': message}
        finally:
            if trace_file is not None:
                trace_file.close()

    def _do_pour(self, job: Job):
        # 스쿱을 인출해 쥐고 있는지 확인한 뒤 workbench의 붓기 경로를 실행한다.
        # Pour의 fraction=1은 스쿱 내용물 전체를 투입하라는 뜻이다.
        self.ctx.state.empty_scoop_baseline_pending = False
        self.motion._require_scoop_extracted()
        self.motion._require_held_scoop()
        fraction = float(job.args['fraction'])
        if not math.isfinite(fraction) or fraction != 1.0:
            raise ValueError('Pour는 전체 스쿱 투입(fraction=1.0)만 허용한다')
        workbench = self.ctx.stations.get('workbench')
        if 'pour_above_posx' in workbench.extra:
            return self._do_taught_pour(job, workbench)
        p = self.ctx.parameter
        start = self.motion._pose_from_extra(workbench, 'pour_start_posx')
        end = self.motion._pose_from_extra(workbench, 'pour_end_posx')
        height = workbench.extra.get('approach_mm', self.ctx.stations.approach_mm)
        if (type(height) not in (int, float) or not math.isfinite(height) or height <= 0):
            raise ValueError('Pour 접근 높이는 유한한 양수여야 합니다')
        above = list(start)
        above[2] += height  # 붓기 시작점 기준 BASE Z 상승. 용기 파지 ABOVE와 구분한다.
        if job.cancel:
            raise RuntimeError('cancelled')
        job.feedback and job.feedback('APPROACH')
        # MOVEL · TCP 직선 이동: above
        self.ctx.arm.movel(above, self.ctx.config.vel_scale)
        if job.cancel:
            raise RuntimeError('cancelled')
        # MOVEL · TCP 직선 이동: start
        self.ctx.arm.movel(start, self.ctx.config.vel_scale)
        if job.cancel:
            raise RuntimeError('cancelled')
        completed = False
        try:
            job.feedback and job.feedback('TILT')
            # MOVEL · TCP 직선 이동: end
            self.ctx.arm.movel(end, self.ctx.config.vel_scale)
            if job.cancel:
                raise RuntimeError('cancelled')
            job.feedback and job.feedback('HOLD')
            self.safety._wait_with_nudge(float(p('pour.hold_s').value), job)
            if job.cancel:
                raise RuntimeError('cancelled')
            completed = True
        finally:
            if completed and not job.cancel:
                job.feedback and job.feedback('RETURN')
                # MOVEL · TCP 직선 이동: start
                self.ctx.arm.movel(start, self.ctx.config.vel_scale)
        return True

    def _do_taught_pour(self, job, station):
        # 현재 계량 자세에서 workbench의 middle로 TCP 직선 이동을 시작한다.
        # middle → 붓기 상부 above → start → 기울인 end → above →
        # 더 높은 high → middle 순서다. 매 구간 전에 취소와 스쿱 파지를 확인한다.
        middle = self.motion._pose_from_extra(station, 'middle_posx')
        above = self.motion._pose_from_extra(station, 'pour_above_posx')
        start = self.motion._pose_from_extra(station, 'pour_start_posx')
        end = self.motion._pose_from_extra(station, 'pour_end_posx')
        exit_mm = finite(station.extra['pour_exit_mm'], '붓기 후 상승량')
        if exit_mm <= 0:
            raise ValueError('붓기 후 상승량은 양수여야 한다')
        high = list(above)
        high[2] += exit_mm
        cancel = lambda: job.cancel or self.runtime._cancel_requested()
        for phase, target in [('APPROACH', middle), ('APPROACH', above), ('APPROACH', start),
                              ('TILT', end), ('RETURN', above), ('RETURN', high), ('RETURN', middle)]:
            if cancel():
                raise RuntimeError('cancelled')
            self.motion._require_held_scoop()
            job.feedback and job.feedback(phase)
            # MOVEL · TCP 직선 이동: target
            self.ctx.arm.movel_cancellable(target, self.ctx.config.vel_scale, cancel, self.ctx.config.motion_timeout_s)
        return True

    def _do_return_material(self, job: Job):
        # 초과한 원료를 원료통에 되돌린다. 반환 시작 TCP로 직선 이동한 뒤
        # 티칭된 관절각으로 스쿱을 기울인다. 그 자세에서의 재스쿱 경로는 미구현이다.
        self.ctx.state.empty_scoop_baseline_pending = False
        self.motion._require_scoop_extracted()
        material_id = job.args['material_id']
        self.motion._require_held_scoop(material_id)
        station = self.ctx.stations.for_material(material_id)
        # 반환 시작 TCP와 기울인 관절 목표가 모두 설정돼 있는지 이동 전에 확인한다.
        start = self.motion._pose_from_extra(station, 'return_start_posx')
        end = self.motion._pose_from_extra(station, 'return_end_posj')
        if job.cancel:
            raise RuntimeError('cancelled')
        job.feedback and job.feedback('APPROACH')
        # MOVEL · TCP 직선 이동: start
        self.ctx.arm.movel(start, self.ctx.config.vel_scale)
        if job.cancel:
            raise RuntimeError('cancelled')
        job.feedback and job.feedback('TILT')
        # 기울이는 도중 실패·취소돼도 실제 스쿱 각도를 알 수 없다. 재스쿱을
        # 계속 차단하며, SafePose나 파지 변경만으로 이 차단을 풀지 않는다.
        self.ctx.state.return_rescoop_blocked = True
        # TCP 직선 이동은 손목 관절이 급격히 바뀌는 특이점을 지날 수 있다.
        # 그래서 검증된 반환 끝 관절각으로 MOVEJ한다.
        # MOVEJ · 관절각 목표: end
        self.ctx.arm.movej_cancellable(end, self.ctx.config.vel_scale, lambda: job.cancel, self.ctx.config.motion_timeout_s)
        if job.cancel:
            raise RuntimeError('cancelled')
        job.feedback and job.feedback('HOLD')
        self.safety._wait_with_nudge(float(self.ctx.parameter('pour.hold_s').value), job)
        if job.cancel:
            raise RuntimeError('cancelled')
        # TODO([A]): 반환 끝 → 재스쿱 연결은 스쿱 모션 구현 시 함께 티칭·검증한다.
        # 시작 자세로 돌아가지 않고 반환 끝 자세에서 종료한다.
        return True
