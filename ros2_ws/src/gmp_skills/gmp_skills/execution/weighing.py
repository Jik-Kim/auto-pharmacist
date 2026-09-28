"""용기·스쿱 계량 순서와 측정값 구성을 담당한다. 보정 모델은 B 라이브러리를 사용한다."""
import json
import math

from .context import Job
from gmp_interfaces.action import MoveToStation
from gmp_interfaces.msg import WeightReading
from gmp_dosing.core.scale import ScaleConfig, WeightModel


class WeighingSkills:
    def __init__(self, ctx, motion, safety, runtime):
        # 노드 전체 대신 필요한 장치·설정·상태·콜백만 공유한다.
        self.ctx = ctx
        self.motion = motion
        self.safety = safety
        self.runtime = runtime

    def _scale_period_s(self):
        # 역할: 계량 표본 주기를 읽고 유한한 양수인지 검증해 초 단위로 반환한다.
        period = float(self.ctx.parameter('scale.period_s').value)
        if not math.isfinite(period) or period <= 0:
            raise ValueError('scale.period_s는 유한한 양수여야 한다')
        return period

    def _do_measure(self, job: Job):
        # 역할: 원시 힘 표본의 평균·표준편차·유효성을 반환한다. 가상 측정은 실측 유효값으로 보고하지 않는다.
        period_s = self._scale_period_s()
        if self.ctx.parameter('scale.simulated').value:
            return [0.0] * 6, 0.0, 0.0, False, 'simulated'
        mean6, fz, std, valid = self.ctx.arm.measure_force(job.args['samples'], job.args['settle_s'],
                                                       period_s=period_s, observer=self.safety._observe_force)
        return mean6, fz, std, valid, ''

    def _measure_weight_reading(self, tare_g: float, subject: str,
                                station_id: str = 'workbench') -> WeightReading:
        # 역할: 원시 측정에 B의 보정 모델과 tare를 적용해 WeightReading을 만들고 조건부 빈 스쿱 기준을 저장한다.
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
            raw_mean, raw_std, valid_src = 0.0, 0.0, False
        elif method == 'workpiece':
            raw_mean, raw_std, valid_src = self.ctx.arm.measure_workpiece(
                samples, settle_s, period_s=period_s, observer=self.safety._observe_force)
        else:
            _, raw_mean, raw_std, valid_src = self.ctx.arm.measure_force(
                samples, settle_s, period_s=period_s, observer=self.safety._observe_force)
        model = WeightModel(ScaleConfig(
            method=method,
            gain=float(p('scale.gain').value),
            offset_g=float(p('scale.offset_g').value),
            max_std_g=float(p('scale.max_std_g').value),
            fz_sign=float(p('scale.fz_sign').value),
        ))
        model.set_tare(tare_g)
        gross_g, _, net_g, std_g, valid = model.reading(raw_mean, raw_std, valid_src)
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
                and math.isfinite(raw_mean) and math.isfinite(raw_std) and raw_std >= 0
                and self.ctx.state.held_payload == 'scoop' and self.ctx.state.held_material_id
                and not self.runtime._cancel_requested()):
            station = self.ctx.stations.for_material(self.ctx.state.held_material_id)
            if station.station_id == station_id and self.motion._pose_matches(self.ctx.arm.current_posx(), station.posx):
                self.ctx.state.empty_scoop_force_baseline = {
                    'fz_mean_n': float(raw_mean), 'fz_std_n': float(raw_std),
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
        # 용기 계량: workbench 접근 → 파지 → 계량 높이로 상승 → 측정.
        # 측정 완료·취소 없음이 확인된 경우에만 내려놓기 → 열기 → 상승까지 수행한다.
        # 역할: 용기를 집어 들어 계량하고 정상 완료 시 내려놓고 그리퍼를 연 뒤 상승한다.
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
            # Weigh의 내부 이동도 MoveToStation과 같은 상부 접근 정책을 따른다.
            self.motion._do_move(job, station_id='workbench', approach=MoveToStation.Goal.ABOVE)
        else:
            self.ctx.arm.movel(measure_posx, self.ctx.config.vel_scale)
        if not bool(p('scale.simulated').value):
            self.ctx.arm.reset_workpiece()
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
            self.ctx.arm.movel(measure_posx, self.ctx.config.vel_scale)
            job.feedback and job.feedback('SETTLE')
            reading = self._measure_weight_reading(float(job.args['tare_g']), 'container')
            job.feedback and job.feedback('MEASURE')
            completed = True
        finally:
            if completed and grip_commanded and not job.cancel:
                job.feedback and job.feedback('PLACE')
                self.ctx.arm.movel(pick_posx, self.ctx.config.vel_scale)
                if not self.ctx.gripper.release(3.0):
                    raise RuntimeError('용기 계량 후 열림 미확인')
                self.ctx.arm.movel(measure_posx, self.ctx.config.vel_scale)
                if 'approach_posj' in station.extra:
                    self.ctx.state.held_payload = 'empty'
                    self.motion._record_arrival(station.station_id, MoveToStation.Goal.ABOVE, measure_posx)
        return reading

    def _do_weigh_held(self, job: Job):
        # 파지 중인 스쿱 계량. 첫 요청은 거치대 측면 인출 → 상승을 먼저 완료한다.
        # 이후 해당 원료 계량 자세로 이동해 측정한다. 재요청 때 인출을 반복하지 않는다.
        # 역할: 잡고 있는 스쿱을 필요 시 인출·상승시킨 후 해당 원료 계량 자세에서 무게를 측정한다.
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
            self.ctx.arm.movel(target, self.ctx.config.vel_scale)
            if job.cancel:
                raise RuntimeError('cancelled')
            # 원료통으로 대각선 진입하기 전에 인출 완료 위치에서 수직 상승한다.
            # 실제 BASE 자세의 X/Y·회전은 유지하고 Z에만 설정 높이를 더한다.
            lift_target = list(self.ctx.arm.current_posx())
            if len(lift_target) != 6 or not all(math.isfinite(v) for v in lift_target):
                raise ValueError('스쿱 상승 기준 posx는 유한한 6개 값이어야 한다')
            lift_target[2] += lift_z_mm
            if job.cancel:
                raise RuntimeError('cancelled')
            self.ctx.arm.movel(lift_target, self.ctx.config.vel_scale)
            if job.cancel:
                raise RuntimeError('cancelled')
            self.ctx.state.scoop_extract_uncertain = False

        self.ctx.arm.movel(material.posx, self.ctx.config.vel_scale)
        if job.cancel:
            raise RuntimeError('cancelled')
        self.ctx.state.station_id = material.station_id
        job.feedback and job.feedback('SETTLE')
        reading = self._measure_weight_reading(float(job.args['tare_g']), 'scoop', material.station_id)
        job.feedback and job.feedback('MEASURE')
        return reading

