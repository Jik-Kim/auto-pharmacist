"""용기 또는 스쿱을 계량 자세로 옮겨 힘을 읽고 WeightReading을 만든다.

로봇은 외력의 평균·흔들림을 측정한다. gmp_dosing의 WeightModel이 이를
그램으로 보정하고 tare_g(미리 잰 빈 물체 무게)를 빼 순량 net_g를 만든다.
"""
import json
import math

from .context import Job
from gmp_interfaces.action import MoveToStation
from gmp_interfaces.msg import WeightReading
from gmp_dosing.core.scale import ScaleConfig, WeightModel, fit_oscillation


class WeighingSkills:
    def __init__(self, ctx, motion, safety, runtime):
        # 노드 전체 대신 필요한 장치·설정·상태·콜백만 공유한다.
        self.ctx = ctx
        self.motion = motion
        self.safety = safety
        self.runtime = runtime

    def _scale_period_s(self):
        # 센서 표본을 시작하는 최소 간격을 ROS 파라미터에서 읽는다.
        period = float(self.ctx.parameter('scale.period_s').value)
        if not math.isfinite(period) or period <= 0:
            raise ValueError('scale.period_s는 유한한 양수여야 한다')
        return period

    def _do_measure(self, job: Job):
        # MeasureForce 요청에는 보정 전 힘의 평균·표준편차를 돌려준다.
        # 가상 모드의 0값은 실제 센서 측정이 아니므로 valid=False로 표시한다.
        period_s = self._scale_period_s()
        if self.ctx.parameter('scale.simulated').value:
            return [0.0] * 6, 0.0, 0.0, False, 'simulated'
        mean6, fz, std, valid = self.ctx.arm.measure_force(job.args['samples'], job.args['settle_s'],
                                                       period_s=period_s, observer=self.safety._observe_force)
        return mean6, fz, std, valid, ''

    def _measure_weight_reading(self, tare_g: float, subject: str,
                                station_id: str = 'workbench') -> WeightReading:
        # 센서 원시값 → gmp_dosing의 보정 → 빈 물체 무게(tare_g) 차감 순서다.
        # 빈 스쿱 첫 계량이면 이후 원료면 진단에 쓸 힘 기준값도 저장한다.
        p = self.ctx.parameter
        capture_baseline = (subject == 'scoop'
                            and getattr(self.ctx.state, 'empty_scoop_baseline_pending', False))
        if capture_baseline:
            self.ctx.state.empty_scoop_force_baseline = None
        period_s = self._scale_period_s()
        samples = int(p('scale.samples').value)
        settle_s = float(p('scale.settle_s').value)
        method = p('scale.method').value
        if method not in ('workpiece', 'tool_force'):
            raise ValueError(f'scale.method는 workpiece 또는 tool_force여야 한다: {method}')
        if bool(p('scale.simulated').value):
            raw_mean, raw_std, raw_hf_std, valid_src = 0.0, 0.0, 0.0, False
            baseline_mean, baseline_std = raw_mean, raw_std
        elif method == 'workpiece':
            raw_mean, raw_std, valid_src, raw_samples = self.ctx.arm.measure_workpiece(
                samples, settle_s, period_s=period_s, observer=self.safety._observe_force,
                include_samples=True)
            baseline_mean, baseline_std = raw_mean, raw_std
            if valid_src:
                raw_mean, raw_std, raw_hf_std, _ = fit_oscillation(raw_samples, period_s)
            else:
                raw_hf_std = 0.0
        else:
            _, raw_mean, raw_std, valid_src, raw_samples = self.ctx.arm.measure_force(
                samples, settle_s, period_s=period_s, observer=self.safety._observe_force,
                include_samples=True)
            baseline_mean, baseline_std = raw_mean, raw_std
            if valid_src:
                raw_mean, raw_std, raw_hf_std, _ = fit_oscillation(raw_samples, period_s)
            else:
                raw_hf_std = 0.0
        model = WeightModel(ScaleConfig(
            method=method,
            gain=float(p('scale.gain').value),
            offset_g=float(p('scale.offset_g').value),
            max_std_g=float(p('scale.max_std_g').value),
            max_hf_std_g=float(p('scale.max_hf_std_g').value),
            fz_sign=float(p('scale.fz_sign').value),
        ))
        model.set_tare(tare_g)
        gross_g, _, net_g, std_g, valid = model.reading(
            raw_mean, raw_std, valid_src, raw_hf_std=raw_hf_std)
        reading = WeightReading(
            gross_g=gross_g,
            tare_g=tare_g,
            net_g=net_g,
            std_g=std_g,
            samples=samples,
            valid=valid,
            station=station_id,
            subject=subject,
        )
        reading.header.stamp = self.ctx.clock().now().to_msg()
        if (capture_baseline and method == 'tool_force' and valid_src
                and not bool(p('scale.simulated').value)
                and math.isfinite(baseline_mean) and math.isfinite(baseline_std) and baseline_std >= 0
                and self.ctx.state.held_payload == 'scoop' and self.ctx.state.held_material_id
                and not self.runtime._cancel_requested()):
            station = self.ctx.stations.for_material(self.ctx.state.held_material_id)
            if station.station_id == station_id and self.motion._pose_matches(self.ctx.arm.current_posx(), station.posx):
                self.ctx.state.empty_scoop_force_baseline = {
                    'fz_mean_n': float(baseline_mean), 'fz_std_n': float(baseline_std),
                    'weight_valid': bool(valid),
                    'material_id': self.ctx.state.held_material_id, 'station_id': station_id,
                    'pose': list(station.posx),
                    'safety_revision': getattr(self.ctx.state, 'safety_revision', 0),
                }
                self.ctx.state.empty_scoop_baseline_pending = False
                self.ctx.logger().info('[EMPTY_SCOOP_FZ_BASELINE] ' + json.dumps(
                    self.ctx.state.empty_scoop_force_baseline, ensure_ascii=False))
        return reading

    def _do_weigh(self, job: Job):
        # workbench의 용기를 집어 상부 계량 위치에서 측정한다. 측정이 정상
        # 완료되고 취소되지 않았을 때만 원래 자리에 내려놓고 그리퍼를 연다.
        self.motion._require_scoop_extracted()
        p = self.ctx.parameter
        station = self.ctx.stations.get('workbench')
        pick_posx = station.posx
        measure_posx = station.above(self.ctx.stations.approach_mm)
        if 'approach_posj' in station.extra:
            if getattr(self.ctx.gripper, 'backend', '') == 'dio':
                self.ctx.gripper.refresh_dio()
            state = self.ctx.gripper.state(self.ctx.now())
            if state.get('busy', True) or state.get('grip_inferred', False):
                raise RuntimeError('용기 계량 전 열린 그리퍼 확인이 필요하다')
            self.ctx.state.held_payload = 'empty'
            self.motion._do_move(job, station_id='workbench', approach=MoveToStation.Goal.AT)
            pick_posx = list(self.ctx.arm.current_posx())
            measure_posx = list(pick_posx)
            measure_posx[2] += station.extra['approach_mm']
        elif 'solution_space' in station.extra:
            # 지정 관절 분기(solution_space)가 있는 스테이션은 일반 이동과 동일하게
            # ABOVE 접근점을 거쳐 들어간다.
            self.motion._do_move(job, station_id='workbench', approach=MoveToStation.Goal.ABOVE)
        else:
            # MOVEL · TCP 직선 이동: measure_posx
            self.ctx.arm.movel(measure_posx, self.ctx.config.vel_scale)
        if not bool(p('scale.simulated').value):
            self.ctx.arm.reset_workpiece()
        # MOVEL · TCP 직선 이동: pick_posx
        self.ctx.arm.movel(pick_posx, self.ctx.config.vel_scale)
        job.feedback and job.feedback('GRIP')
        grip_commanded = True
        reading = WeightReading()
        completed = False
        try:
            ok, _, inferred = self.ctx.gripper.grip(float(p('gripper.cup_width_mm').value),
                                                float(p('gripper.force_n').value), 3.0)
            if not ok or not inferred:
                raise RuntimeError('용기 파지 실패')
            job.feedback and job.feedback('LIFT')
            # MOVEL · TCP 직선 이동: measure_posx
            self.ctx.arm.movel(measure_posx, self.ctx.config.vel_scale)
            job.feedback and job.feedback('SETTLE')
            reading = self._measure_weight_reading(float(job.args['tare_g']), 'container')
            job.feedback and job.feedback('MEASURE')
            completed = True
        finally:
            if completed and grip_commanded and not job.cancel:
                job.feedback and job.feedback('PLACE')
                # MOVEL · TCP 직선 이동: pick_posx
                self.ctx.arm.movel(pick_posx, self.ctx.config.vel_scale)
                if not self.ctx.gripper.release(3.0):
                    raise RuntimeError('용기 계량 후 열림 미확인')
                # MOVEL · TCP 직선 이동: measure_posx
                self.ctx.arm.movel(measure_posx, self.ctx.config.vel_scale)
                if 'approach_posj' in station.extra:
                    self.ctx.state.held_payload = 'empty'
                    self.motion._record_arrival(station.station_id, MoveToStation.Goal.ABOVE, measure_posx)
        return reading

    def _do_weigh_held(self, job: Job):
        # 거치대에서 스쿱을 막 집었다면 첫 WeighHeld에서 +Y 측면 인출과 수직
        # 상승을 수행한다. 이미 인출했다면 반복하지 않고 material_N 계량
        # 자세로 직선 이동해 스쿱에 담긴 원료 무게를 측정한다.
        if self.ctx.state.scoop_extract_uncertain:
            raise RuntimeError('스쿱 인출 상태가 불확실하다. SafePose 후 수동 확인이 필요하다')
        self.motion._require_held_scoop()
        material = self.ctx.stations.for_material(self.ctx.state.held_material_id)
        if job.cancel:
            raise RuntimeError('cancelled')

        job.feedback and job.feedback('LIFT')
        if self.ctx.state.pending_scoop_extract:
            lift_z_mm = self.ctx.config.scoop_extract_lift_z_mm
            if not math.isfinite(lift_z_mm) or lift_z_mm <= 0:
                raise ValueError('gripper.scoop_extract_lift_z_mm는 유한한 양수여야 한다')
            target = list(self.ctx.arm.current_posx())
            if len(target) != 6:
                raise ValueError('스쿱 인출 기준 posx는 6개여야 한다')
            target[1] += self.ctx.config.scoop_extract_y_mm
            self.ctx.state.pending_scoop_extract = False
            self.ctx.state.scoop_extract_uncertain = True
            # MOVEL · TCP 직선 이동: target
            self.ctx.arm.movel(target, self.ctx.config.vel_scale)
            if job.cancel:
                raise RuntimeError('cancelled')
            # 거치대에서 빠져나온 뒤 원료통으로 이동하기 전에 BASE 좌표의
            # X/Y와 TCP 회전은 유지하고 Z만 설정 높이만큼 올린다.
            lift_target = list(self.ctx.arm.current_posx())
            if len(lift_target) != 6 or not all(math.isfinite(v) for v in lift_target):
                raise ValueError('스쿱 상승 기준 posx는 유한한 6개 값이어야 한다')
            lift_target[2] += lift_z_mm
            if job.cancel:
                raise RuntimeError('cancelled')
            # MOVEL · TCP 직선 이동: lift_target
            self.ctx.arm.movel(lift_target, self.ctx.config.vel_scale)
            if job.cancel:
                raise RuntimeError('cancelled')
            self.ctx.state.scoop_extract_uncertain = False

        # MOVEL · TCP 직선 이동: material.posx
        self.ctx.arm.movel(material.posx, self.ctx.config.vel_scale)
        if job.cancel:
            raise RuntimeError('cancelled')
        self.ctx.state.station_id = material.station_id
        job.feedback and job.feedback('SETTLE')
        reading = self._measure_weight_reading(float(job.args['tare_g']), 'scoop', material.station_id)
        job.feedback and job.feedback('MEASURE')
        return reading
