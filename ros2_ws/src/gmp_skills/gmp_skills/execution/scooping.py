"""고정/높이 보정 스쿠핑·접촉 진단·붓기·원료 반환 순서를 담당한다."""
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
        # 역할: 파지·인출 조건을 확인한 뒤 고정 티칭/높이 보정/높이 측정 전용 경로로 분기한다.
        """고정 BASE 티칭 또는 보정된 WORLD 경로로 스쿠핑 후 계량 자세에 복귀한다."""
        if getattr(self.ctx.state, 'return_rescoop_blocked', False):
            raise RuntimeError('반환 후 재스쿱 연결 경로 미구현: 자동 Scoop을 차단합니다')
        self.motion._require_scoop_extracted()
        material = job.args['material_id']
        self.motion._require_held_scoop(material)
        if getattr(self.ctx.config, 'height_measure_only', False):
            return self._measure_surface_world(job)
        profile = self.ctx.stations.scooping.get(material)
        # 현행 고정 경로는 여기서 분기한다. 아래 높이 보정 계산을 통과하지 않는다.
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
        # 설정과 좌표 변환은 첫 이동 전에 확인한다. WORLD=BASE를 가정하지 않는다.
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
            # 역할: 이동 중 스쿱 파지와 해당 경로의 관측 조건을 확인하는 워커 감시 콜백이다.
            self.motion._require_held_scoop(material)
            world = self.ctx.arm.transform_pose(self.ctx.arm.current_posx(), to_world=True)
            if tip_z(world, offset) < plan.floor_z:
                raise RuntimeError('스쿠핑 중 스쿱 끝 높이 하한 침범')

        # 취소·미도달·관측 실패 시 어댑터가 정지하고 다음 이동은 수행하지 않는다.
        job.feedback and job.feedback('DIP', True, result['max_contact_force_n'],
                                      result['insertion_depth_mm'])
        self.ctx.arm.movesx_cancellable(targets, vel, acc, cancel, self.ctx.config.motion_timeout_s,
                                    observer=observe)
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
        self.ctx.arm.movel_cancellable(station.posx, self.ctx.config.vel_scale, cancel,
                                   self.ctx.config.motion_timeout_s, observer=observe)
        job.feedback and job.feedback('LIFT', True, result['max_contact_force_n'],
                                      result['insertion_depth_mm'])
        return result

    def _do_fixed_scoop(self, job, profile):
        # 역할: 검증된 full 5점 경로와 털기를 실행하고 원료 계량 자세로 복귀한다. 접촉·깊이는 측정하지 않는다.
        """검증된 BASE 경로만 실행한다. 원료면/끝 높이/담금량을 계산하지 않는다."""
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
            # 역할: 이동 중 스쿱 파지와 해당 경로의 관측 조건을 확인하는 워커 감시 콜백이다.
            self.motion._require_held_scoop(material)

        if cancel():
            raise RuntimeError('cancelled')
        if not self.motion._pose_matches(self.ctx.arm.current_posx(), station.posx):
            raise RuntimeError('고정 스쿠핑은 해당 원료 계량 자세에서 시작해야 한다')
        observe()
        job.feedback and job.feedback('DIP')
        # 2) 해당 원료 계량 자세에서 시작해 5점 spline으로 퍼낸 뒤 털기 위치로 이동한다.
        self.ctx.arm.movesx_cancellable(points, vel, acc, cancel, self.ctx.config.motion_timeout_s, observer=observe)
        self.ctx.arm.movel_cancellable(shake, self.ctx.config.vel_scale, cancel, self.ctx.config.motion_timeout_s, observer=observe)
        if cancel():
            raise RuntimeError('cancelled')
        job.feedback and job.feedback('LEVEL')
        try:
            # 3) DRL의 BASE 기준 주기 운동. 완료와 종료 자세를 확인하고 실패하면 정지한다.
            self.ctx.arm.amove_periodic(amp, period, atime, repeat, ref_tool=False)
            self.ctx.arm.wait_motion_cancellable(cancel, self.ctx.config.motion_timeout_s, observer=observe)
            if not self.motion._pose_matches(self.ctx.arm.current_posx(), shake):
                raise RuntimeError('털기 종료 자세 미확인')
        except Exception:
            self.ctx.arm.stop_motion()
            raise
        job.feedback and job.feedback('LIFT')
        self.ctx.arm.movel_cancellable(station.posx, self.ctx.config.vel_scale, cancel,
                                   self.ctx.config.motion_timeout_s, observer=observe)
        # 4) 계량 자세 복귀까지가 Scoop의 책임. 실제 무게는 후속 WeighHeld가 측정한다.
        # 고정 경로는 접촉을 측정하지 않으므로 아래 false/0을 실측 결과로 해석하지 않는다.
        return dict(contact_detected=False, max_contact_force_n=0.0, insertion_depth_mm=0.0,
                    message='TAUGHT_FIXED: 검증된 full 경로 완료; 접촉력·삽입 깊이 미측정')

    def _measure_surface_world(self, job: Job):
        # 역할: 접촉 위치와 스쿱 끝 오프셋으로 WORLD 원료면 높이를 계산해 측정 전용 진단 결과를 만든다.
        """측정 전용: 기존 접촉 경로와 복귀만 실행하고 스쿠핑은 하지 않는다."""
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
        # 역할: 순응 제어 안정화 시간을 기다리면서 취소와 넛지 감시를 유지한다.
        """순응 진입 응답 후 컨트롤러 전환 시간을 확보하며 취소를 확인한다."""
        deadline = self.ctx.now() + duration_s
        while True:
            if job.cancel or self.runtime._cancel_requested():
                raise RuntimeError('cancelled')
            remaining = deadline - self.ctx.now()
            if remaining <= 0:
                return
            time.sleep(min(0.02, remaining))

    def _do_check_depth(self, job: Job):
        # 역할: 빈 스쿱 힘 기준과 티칭 경로로 접촉/삽입을 관측하고 힘제어 해제 및 복귀를 처리한다.
        """티칭 목표로 접근하다 최초 접촉에서 감속 정지하고 계량 자세로 복귀한다."""
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
        # 한번 접근한 뒤의 계량을 빈 스쿱 영점으로 다시 저장하지 않는다.
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
        # 기하 설정 오류는 이동 전에 거부한다.
        tip_position_base(start, reference, tip_offset)
        if job.cancel:
            raise RuntimeError('cancelled')
        job.feedback and job.feedback('APPROACH')
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
            # 역할: 외력·현재 자세로 접촉과 삽입 깊이를 갱신하고 접촉 정지 여부를 판단한다.
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
                # 이후 목표 미도달·취소로 실패해도 최초 표본은 남긴다.
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
                # 목표의 XYZ와 회전을 모두 사용한다. 고정 Z 힘·상대 40 mm 담그기는 사용하지 않는다.
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
            # 성공한 경로만 계량 자세로 되짚는다. 실패·취소 시 자동 복귀하지 않는다.
            if trace_only:
                phase = 'RETURN'
                self.ctx.arm.movel_cancellable(start, self.ctx.config.vel_scale,
                    lambda: job.cancel or self.runtime._cancel_requested(), self.ctx.config.motion_timeout_s,
                    observer=observe_depth)
            else:
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
        # 역할: 인출·파지와 붓기 요청을 확인하고 티칭 경로 또는 기존 붓기 경로를 실행한다.
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
        self.ctx.arm.movel(above, self.ctx.config.vel_scale)
        if job.cancel:
            raise RuntimeError('cancelled')
        self.ctx.arm.movel(start, self.ctx.config.vel_scale)
        if job.cancel:
            raise RuntimeError('cancelled')
        completed = False
        try:
            job.feedback and job.feedback('TILT')
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
                self.ctx.arm.movel(start, self.ctx.config.vel_scale)
        return True

    def _do_taught_pour(self, job, station):
        # 고정 붓기 순서: middle → above → start → end(기울이기)
        #                 → above → 추가 상승(high) → middle. 각 구간에서 파지·취소를 확인한다.
        # 역할: 중간점·상부·붓기 시작/끝·상승·중간점 순서로 이동하며 각 구간의 파지를 확인한다.
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
            self.ctx.arm.movel_cancellable(target, self.ctx.config.vel_scale, cancel, self.ctx.config.motion_timeout_s)
        return True

    def _do_return_material(self, job: Job):
        # 역할: 원료 반환 시작점과 관절 기울임 자세로 이동한다. 미검증 재스쿱 연결은 차단한 채 끝낸다.
        self.ctx.state.empty_scoop_baseline_pending = False
        self.motion._require_scoop_extracted()
        material_id = job.args['material_id']
        self.motion._require_held_scoop(material_id)
        station = self.ctx.stations.for_material(material_id)
        # 두 자세를 모두 검증한 뒤에만 첫 이동을 시작한다. 미티칭이면 현재 자세를 유지한다.
        start = self.motion._pose_from_extra(station, 'return_start_posx')
        end = self.motion._pose_from_extra(station, 'return_end_posj')
        if job.cancel:
            raise RuntimeError('cancelled')
        job.feedback and job.feedback('APPROACH')
        self.ctx.arm.movel(start, self.ctx.config.vel_scale)
        if job.cancel:
            raise RuntimeError('cancelled')
        job.feedback and job.feedback('TILT')
        # 실패·취소도 기울어진 자세일 수 있어 성공 여부와 무관하게 유지한다.
        # SafePose·파지 변경으로 해제하지 않는다. 연결 경로 구현 시 해제 조건을 정한다.
        self.ctx.state.return_rescoop_blocked = True
        # 손목 특이점을 지나는 직선 보간 대신 티칭한 관절각으로 이동한다.
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

