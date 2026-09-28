"""스테이션 이동·그리퍼 개폐·파지와 출발/도착 조건을 담당한다."""
import math

from .context import Job
from gmp_interfaces.action import MoveToStation
from gmp_skills.core.scooping import finite
from gmp_skills.core.transfer import MotionAnchor, joints_match, pose_matches, validate_start, vector6


class MotionSkills:
    def __init__(self, ctx, runtime):
        # 노드 전체 대신 필요한 장치·설정·상태·콜백만 공유한다.
        self.ctx = ctx
        self.runtime = runtime

    def _require_scoop_extracted(self):
        # 역할: 스쿱 인출이 미완료이거나 불확실하면 후속 이동을 예외로 차단한다.
        if self.ctx.state.scoop_extract_uncertain:
            raise RuntimeError('스쿱 인출 상태가 불확실하다. SafePose 후 수동 확인이 필요하다')
        if self.ctx.state.pending_scoop_extract:
            raise RuntimeError('스쿱 파지 후 WeighHeld로 +Y 인출을 먼저 수행해야 한다')

    def _do_move(self, job: Job, *, station_id=None, approach=None):
        # 역할: 스테이션 이동의 진입점이다. 실패하면 출발/파지 이력을 지워 후속 동작의 오판을 막는다.
        self._require_scoop_extracted()
        try:
            return self._move_checked(job, station_id=station_id, approach=approach)
        except Exception:
            self.ctx.state.motion_anchor = None
            self.ctx.state.held_payload = 'unknown'
            self.ctx.state.held_material_id = ''
            self.ctx.state.empty_scoop_force_baseline = None
            self.ctx.state.empty_scoop_baseline_pending = False
            raise

    def _pose_matches(self, actual, target):
        # 역할: 위치·회전 허용오차 안에서 실제 TCP 자세가 목표와 일치하는지 반환한다.
        return pose_matches(actual, target, self.ctx.config.pose_xyz_tolerance, self.ctx.config.pose_rotation_tolerance)

    def _record_arrival(self, station_id, approach, target):
        # 역할: 실제 목표 도착을 확인한 뒤 위치·관절각을 다음 이동의 출발 이력으로 저장한다.
        actual = self.ctx.arm.current_posx()
        if not self._pose_matches(actual, target):
            raise RuntimeError(f'이동 위치/자세 미도달: target={target}, actual={actual}')
        self.ctx.state.motion_anchor = MotionAnchor(station_id, approach, tuple(actual),
                                           tuple(self.ctx.arm.current_posj()))
        self.ctx.state.station_id = station_id

    def _move_checked(self, job: Job, *, station_id=None, approach=None):
        # 목적지 설정으로 티칭 경로/전용 이송/sol 접근/일반 이동을 선택한다.
        # 경로 선택 뒤에도 출발 이력과 실제 도착 자세를 확인해야 다음 파지 요청이 가능하다.
        # 역할: 목적지와 출발 상태에 맞는 이동 경로를 선택하고 실행·도착 확인을 수행한다.
        if job.cancel:
            raise RuntimeError('cancelled')
        approach = job.args['approach'] if approach is None else approach
        if approach not in (MoveToStation.Goal.ABOVE, MoveToStation.Goal.AT):
            raise ValueError('approach는 ABOVE(0) 또는 AT(1)이어야 한다')
        st = self.ctx.stations.get(station_id or job.args['station_id'])
        # 티칭 관절 경로만 가상에서 직선 폴백한다. solution_space 접근은 양 모드에 적용한다.
        if 'approach_posj' in st.extra or 'return_entry_posx' in st.extra:
            return self._move_taught_station(job, st, approach)
        transfers = self.ctx.stations.transfers if self.ctx.config.mode != 'virtual' else {}
        incoming = [r for r in transfers.values() if r.destination == st.station_id]
        if incoming and all(r.arrival == 'at' for r in incoming) and approach != MoveToStation.Goal.AT:
            raise ValueError('관절 직접 도착 목적지는 AT 요청만 허용한다')
        target = st.above(self.ctx.stations.approach_mm) if approach == MoveToStation.Goal.ABOVE else st.posx
        vel_scale = job.args.get('vel_scale') or self.ctx.config.vel_scale
        if not math.isfinite(vel_scale) or not 0 < vel_scale <= 1:
            raise ValueError('vel_scale은 0 초과 1 이하여야 한다')
        route = transfers.get((self.ctx.state.station_id, st.station_id))
        if route is not None:
            self._run_transfer(route, job, target, vel_scale)
            return st.station_id
        protected = any(r.destination == st.station_id for r in transfers.values())
        if protected:
            # 같은 스테이션의 AT↔ABOVE만 기존 직선 접근으로 허용한다.
            anchor = self.ctx.state.motion_anchor
            if (anchor is None or anchor.station != st.station_id
                    or not self._pose_matches(self.ctx.arm.current_posx(), anchor.pose)
                    or not joints_match(self.ctx.arm.current_posj(), anchor.joints, self.ctx.config.joint_tolerance)):
                # 재기동·수동 티칭 후 이미 출발점에 있다면 움직이지 않고 확인만 한다.
                # 다른 위치에서 그 점으로 자동 복구하는 경로는 추측하지 않는다.
                for outgoing in self.ctx.stations.transfers.values():
                    if outgoing.source != st.station_id or not outgoing.enabled:
                        continue
                    if outgoing.start_from == 'above' and approach != MoveToStation.Goal.ABOVE:
                        continue
                    taught = (outgoing.start_above_posj if approach == MoveToStation.Goal.ABOVE
                              else outgoing.start_at_posj)
                    if (self._pose_matches(self.ctx.arm.current_posx(), target)
                            and joints_match(self.ctx.arm.current_posj(), taught, self.ctx.config.joint_tolerance)):
                        self.ctx.state.held_payload = 'unknown'
                        self._record_arrival(st.station_id, approach, target)
                        self.ctx.state.cartesian_ready = True
                        return st.station_id
                raise ValueError('등록된 출발 이력이 없는 보호 대상 이송이다')
        self._leave_taught_station(job, st.station_id)
        safe_posj = st.extra.get('posj') if approach != MoveToStation.Goal.ABOVE else None
        if safe_posj is not None:
            job.feedback and job.feedback('HOMING')
            self.ctx.arm.movej_cancellable(safe_posj, vel_scale, lambda: job.cancel,
                                       self.ctx.config.motion_timeout_s)
            self.ctx.state.cartesian_ready = True
            # safe.posx는 자리표시자일 수 있다. 실제 관절 목표가 도착 기준이다.
            target = self.ctx.arm.current_posx()
        else:
            if not self.ctx.state.cartesian_ready:
                entry = self.ctx.stations.get('safe')
                entry_posj = entry.extra.get('posj')
                if not isinstance(entry_posj, list) or len(entry_posj) != 6:
                    raise ValueError('safe station에 시작 posj 6개가 필요하다')
                job.feedback and job.feedback('HOMING')
                self.ctx.arm.movej_cancellable(entry_posj, vel_scale, lambda: job.cancel,
                                           self.ctx.config.motion_timeout_s)
                self.ctx.state.cartesian_ready = True
            job.feedback and job.feedback('MOVING')
            self._leave_solution_station(job, st.station_id, vel_scale)
            if 'solution_space' in st.extra:
                self._move_solution_station(job, st, target, vel_scale)
            else:
                self.ctx.arm.movel_cancellable(target, vel_scale, lambda: job.cancel,
                                           self.ctx.config.motion_timeout_s)
        if job.cancel:
            raise RuntimeError('cancelled')
        self._record_arrival(st.station_id, approach, target)
        return st.station_id

    def _taught_linear(self, job, target):
        # 역할: 직선 이동 준비가 안 됐으면 안전 관절 자세를 거친 뒤 지정 TCP 목표로 이동한다.
        scale = job.args.get('vel_scale') or self.ctx.config.vel_scale
        if not math.isfinite(scale) or not 0 < scale <= 1:
            raise ValueError('vel_scale은 0 초과 1 이하여야 한다')
        if not self.ctx.state.cartesian_ready:
            joints = list(vector6(self.ctx.stations.get('safe').extra['posj'], 'safe.posj'))
            self.ctx.arm.movej_cancellable(joints, scale,
                lambda: job.cancel or self.runtime._cancel_requested(), self.ctx.config.motion_timeout_s,
                joint_vel=self.ctx.config.transfer_joint_vel, joint_acc=self.ctx.config.transfer_joint_acc)
            self.ctx.state.cartesian_ready = True
        self.ctx.arm.movel_cancellable(list(target), scale,
                                   lambda: job.cancel or self.runtime._cancel_requested(), self.ctx.config.motion_timeout_s)

    def _leave_taught_station(self, job, destination):
        # 역할: 현재 티칭 스테이션의 출발 이력을 확인하고 다음 목적지로 가기 전 이탈 높이를 확보한다.
        source = self.ctx.stations.stations.get(self.ctx.state.station_id)
        if source is None or source.station_id == destination:
            return
        if 'approach_posj' not in source.extra and 'return_entry_posx' not in source.extra:
            return
        anchor = self.ctx.state.motion_anchor
        if (anchor is None or anchor.station != source.station_id
                or not self._pose_matches(self.ctx.arm.current_posx(), anchor.pose)
                or not joints_match(self.ctx.arm.current_posj(), anchor.joints, self.ctx.config.joint_tolerance)):
            raise RuntimeError('티칭 경로 출발 이력이 불확실하다')
        if self.ctx.state.held_payload not in ('cup', 'empty'):
            raise RuntimeError('티칭 경로 출발 전 인출·파지 확인이 필요하다')
        self._require_transfer_payload(self.ctx.state.held_payload)
        target = list(anchor.pose)
        target[2] = source.posx[2] + source.extra['exit_mm']
        if not self._pose_matches(self.ctx.arm.current_posx(), target):
            self._taught_linear(job, target)
        self.ctx.state.motion_anchor = None

    def _move_taught_station(self, job, station, approach):
        # 역할: 티칭 설정에 따라 용기 접근/이탈 또는 스쿱 거치대 진입/반납 이동을 수행한다.
        """DRL 관절 진입·직선 하강과 거치대 측면 반납을 외부 AT/ABOVE에 연결한다."""
        scale = job.args.get('vel_scale') or self.ctx.config.vel_scale
        if not math.isfinite(scale) or not 0 < scale <= 1:
            raise ValueError('vel_scale은 0 초과 1 이하여야 한다')
        if any(not math.isfinite(v) or v <= 0
               for v in (self.ctx.config.transfer_joint_vel, self.ctx.config.transfer_joint_acc)):
            raise ValueError('티칭 관절 속도·가속도는 유한한 양수여야 한다')
        anchor = self.ctx.state.motion_anchor
        local = (anchor is not None and anchor.station == station.station_id)
        if local and (not self._pose_matches(self.ctx.arm.current_posx(), anchor.pose)
                      or not joints_match(self.ctx.arm.current_posj(), anchor.joints, self.ctx.config.joint_tolerance)):
            raise RuntimeError('티칭 스테이션 도착 후 위치/관절이 변경되었다')
        if 'return_entry_posx' in station.extra:
            if self.ctx.state.held_payload == 'scoop':
                self._require_held_scoop(station.extra['material_id'])
                if approach != MoveToStation.Goal.AT:
                    raise ValueError('스쿱 반납은 AT 요청으로 실행해야 한다')
                entry = self._pose_from_extra(station, 'return_entry_posx')
                lower = list(entry)
                lower[2] -= finite(station.extra['return_lower_mm'], '반납 하강량')
                for target in (entry, lower, station.posx):
                    self._taught_linear(job, target)
            else:
                self._require_transfer_payload('empty')
                self._leave_taught_station(job, station.station_id)
                target = (station.offset_z(station.extra['exit_mm']) if local
                          and approach == MoveToStation.Goal.ABOVE else station.above(self.ctx.stations.approach_mm))
                if not local or approach == MoveToStation.Goal.ABOVE:
                    self._taught_linear(job, target)
                if approach == MoveToStation.Goal.AT:
                    self._taught_linear(job, station.posx)
        else:
            if self.ctx.state.held_payload not in ('cup', 'empty'):
                raise RuntimeError('용기 스테이션 진입 전 파지/열림 이력이 필요하다')
            self._require_transfer_payload(self.ctx.state.held_payload)
            if local:
                target = list(anchor.pose)
                if approach == MoveToStation.Goal.AT:
                    # 빈 그리퍼의 workbench 진입 관절각은 놓기와 다르다.
                    target[2] = station.posx[2]
                    if self.ctx.state.held_payload == 'cup':
                        target = list(station.posx)
                else:
                    target[2] = station.posx[2] + station.extra['exit_mm']
                self._taught_linear(job, target)
            else:
                self._leave_taught_station(job, station.station_id)
                empty_entry = self.ctx.state.held_payload == 'empty' and 'empty_approach_posj' in station.extra
                if empty_entry:
                    self._taught_linear(job, self._pose_from_extra(station, 'middle_posx'))
                key = 'empty_approach_posj' if empty_entry else 'approach_posj'
                joints = list(vector6(station.extra[key], key))
                self.ctx.arm.movej_cancellable(joints, scale,
                    lambda: job.cancel or self.runtime._cancel_requested(), self.ctx.config.motion_timeout_s,
                    joint_vel=self.ctx.config.transfer_joint_vel, joint_acc=self.ctx.config.transfer_joint_acc)
                self.ctx.state.cartesian_ready = True
                self._require_transfer_payload(self.ctx.state.held_payload)
                if approach == MoveToStation.Goal.AT:
                    if empty_entry:
                        target = list(self.ctx.arm.current_posx())
                        target[2] -= station.extra['empty_descent_mm']
                    else:
                        target = station.posx
                    self._taught_linear(job, target)
            self._require_transfer_payload(self.ctx.state.held_payload)
        if job.cancel or self.runtime._cancel_requested():
            raise RuntimeError('cancelled')
        # 각 어댑터는 실제 관절/직선 목표 도달을 확인한다. 관절각 TCP를 임의 합성하지 않는다.
        self._record_arrival(station.station_id, approach, self.ctx.arm.current_posx())
        return station.station_id

    def _require_solution(self, station):
        # 역할: 현재 로봇의 관절 구성이 스테이션에 지정된 solution_space와 다르면 거부한다.
        sol = self.ctx.arm.solution_space()
        if sol != station.extra['solution_space']:
            raise RuntimeError(f'{station.station_id}: 관절 구성 불일치 sol={sol}')

    def _leave_solution_station(self, job, destination, vel_scale):
        # 역할: solution_space 방식의 용기 스테이션을 떠나기 전 파지·자세 확인과 직선 이탈을 수행한다.
        """용기 위치를 떠날 때는 파지·출발 이력을 확인하고 직선으로 이탈한다."""
        if self.ctx.state.station_id == destination or self.ctx.state.station_id not in self.ctx.stations.stations:
            return
        source = self.ctx.stations.get(self.ctx.state.station_id)
        if 'solution_space' not in source.extra:
            return
        anchor = self.ctx.state.motion_anchor
        if (anchor is None or anchor.station != source.station_id
                or not self._pose_matches(self.ctx.arm.current_posx(), anchor.pose)
                or not joints_match(self.ctx.arm.current_posj(), anchor.joints, self.ctx.config.joint_tolerance)):
            raise RuntimeError('용기 스테이션 출발 이력이 불확실하다')
        if self.ctx.state.held_payload not in ('cup', 'empty'):
            raise RuntimeError('용기 이송 전 파지 상태 확인이 필요하다')
        self._require_transfer_payload(self.ctx.state.held_payload)
        self._require_solution(source)
        self.ctx.arm.movel_cancellable(source.exit(), vel_scale, lambda: job.cancel,
                                   self.ctx.config.motion_timeout_s)
        self._require_solution(source)
        self._require_transfer_payload(self.ctx.state.held_payload)
        self.ctx.state.motion_anchor = None

    def _move_solution_station(self, job, station, target, vel_scale):
        # 역할: solution_space 방식으로 ABOVE까지 관절 접근하고 필요하면 목표까지 직선 이동한다.
        """ABOVE에서 관절 구성을 선택하고 AT 접근은 직선으로 유지한다."""
        if self.ctx.state.held_payload in ('cup', 'empty'):
            self._require_transfer_payload(self.ctx.state.held_payload)
        above = station.above(self.ctx.stations.approach_mm)
        actual = self.ctx.arm.current_posx()
        at_station = (self._pose_matches(actual, station.posx)
                      or self._pose_matches(actual, above))
        if at_station:
            # 작업점에서 손목을 뒤집지 않는다. 수동 이동 뒤에도 구성 확인이 먼저다.
            self._require_solution(station)
        else:
            self.ctx.arm.movejx_cancellable(above, station.extra['solution_space'], vel_scale,
                                        lambda: job.cancel, self.ctx.config.motion_timeout_s)
            self._require_solution(station)
        if self.ctx.state.held_payload in ('cup', 'empty'):
            self._require_transfer_payload(self.ctx.state.held_payload)
        if not self._pose_matches(self.ctx.arm.current_posx(), target):
            self.ctx.arm.movel_cancellable(target, vel_scale, lambda: job.cancel,
                                       self.ctx.config.motion_timeout_s)
        self._require_solution(station)

    def _require_transfer_payload(self, expected):
        # 역할: 이송 경로가 요구하는 빈 그리퍼/용기/스쿱 상태와 최신 파지 피드백을 확인한다.
        if getattr(self.ctx.gripper, 'backend', '') == 'dio':
            self.ctx.gripper.refresh_dio()
            state = self.ctx.gripper.state(self.ctx.now())
            if (state['busy'] or self.ctx.state.held_payload != expected
                    or (expected == 'cup' and not state['grip_inferred'])
                    or (expected == 'empty' and not state['open_confirmed'])):
                raise RuntimeError('이송 파지 이력 또는 DI 완료 상태가 불확실하다')
            return
        state = self.ctx.gripper.state(self.ctx.now())
        if state['busy'] or state['width_mm'] is None or self.ctx.state.held_payload != expected:
            raise RuntimeError('이송 파지 이력 또는 그리퍼 피드백이 불확실하다')
        if bool(state['grip_inferred']) != (expected == 'cup'):
            raise RuntimeError('이송 중 파지 상태가 변경되었다')
        if (not math.isfinite(state['width_mm']) or
                (expected == 'empty' and abs(state['width_mm'] - self.ctx.gripper.open_width_mm)
                 > self.ctx.gripper.grip_margin_mm)):
            raise RuntimeError('빈 그리퍼의 열림 폭을 확인할 수 없다')

    def _run_transfer(self, route, job, target, vel_scale):
        # 역할: 출발 조건을 검증한 전용 이송 경로를 실행하며 중간 구간과 도착 상태를 확인한다.
        if route.arrival == 'at' and job.args['approach'] != MoveToStation.Goal.AT:
            raise ValueError('관절 직접 도착 경로는 AT 요청만 허용한다')
        validate_start(route, self.ctx.state.motion_anchor, self.ctx.arm.current_posx(),
                       self.ctx.arm.current_posj(), self.ctx.state.held_payload,
                       self.ctx.config.pose_xyz_tolerance, self.ctx.config.pose_rotation_tolerance, self.ctx.config.joint_tolerance)
        if any(not math.isfinite(v) or v <= 0
               for v in (self.ctx.config.transfer_joint_vel, self.ctx.config.transfer_joint_acc)):
            raise ValueError('이송 관절 속도·가속도를 먼저 설정해야 한다')
        self._require_transfer_payload(route.payload)
        self.ctx.state.motion_anchor = None
        job.feedback and job.feedback('MOVING')

        def checkpoint():
            # 역할: 전용 이송 구간에서 요구 파지 상태를 다시 확인하는 감시 콜백이다.
            if job.cancel:
                raise RuntimeError('cancelled')
            self._require_transfer_payload(route.payload)

        # 이미 이탈점이면 다시 움직이지 않는다. 마지막 관절점은 경로의 도착 방식에 따른다.
        checkpoint()
        if not self._pose_matches(self.ctx.arm.current_posx(), route.exit_posx):
            self.ctx.arm.movel_cancellable(route.exit_posx, vel_scale, lambda: job.cancel,
                                       self.ctx.config.motion_timeout_s)
        if (not self._pose_matches(self.ctx.arm.current_posx(), route.exit_posx)
                or not joints_match(self.ctx.arm.current_posj(), route.exit_posj, self.ctx.config.joint_tolerance)):
            raise RuntimeError('직선 이탈 후 관절 구성/자세가 티칭값과 다르다')
        for point in route.waypoints_posj:
            checkpoint()
            self.ctx.arm.movej_cancellable(point, vel_scale, lambda: job.cancel, self.ctx.config.motion_timeout_s,
                                       joint_vel=self.ctx.config.transfer_joint_vel, joint_acc=self.ctx.config.transfer_joint_acc)
        checkpoint()
        destination = self.ctx.stations.get(route.destination)
        entry = (destination.posx if route.arrival == 'at'
                 else destination.above(self.ctx.stations.approach_mm))
        if not self._pose_matches(self.ctx.arm.current_posx(), entry):
            raise RuntimeError(f'마지막 관절점이 목적지 {route.arrival.upper()} 위치/자세와 일치하지 않는다')
        if route.arrival == 'above' and job.args['approach'] == MoveToStation.Goal.AT:
            self.ctx.arm.movel_cancellable(target, vel_scale, lambda: job.cancel, self.ctx.config.motion_timeout_s)
        checkpoint()
        self._record_arrival(route.destination, job.args['approach'], target)

    def _do_grip(self, job: Job):
        # 그리퍼 개폐와 파지 이력만 처리한다. 스쿱을 잡아도 여기서 인출하지 않는다.
        # DIO의 출력·입력 완료 확인은 Rg2Gripper가 담당하고, 인출은 _do_weigh_held에서 한다.
        # 역할: 개폐를 실행하고 성공한 위치와 파지 대상에 따라 용기/스쿱 및 인출 대기 상태를 갱신한다.
        a = job.args
        self.ctx.state.held_payload = 'unknown'
        self.ctx.state.held_material_id = ''
        self.ctx.state.empty_scoop_force_baseline = None
        self.ctx.state.empty_scoop_baseline_pending = False
        if a['close']:
            if self.ctx.state.scoop_extract_uncertain:
                raise RuntimeError('스쿱 인출 상태가 불확실하여 재파지할 수 없다')
            anchor = getattr(self.ctx.state, 'motion_anchor', None)
            scoop_at = (
                anchor is not None
                and self.ctx.state.station_id.startswith('scoop_')
                and anchor.station == self.ctx.state.station_id
                and anchor.approach == MoveToStation.Goal.AT
                and self._pose_matches(self.ctx.arm.current_posx(), anchor.pose)
                and joints_match(self.ctx.arm.current_posj(), anchor.joints, self.ctx.config.joint_tolerance)
            )
            options = {'scoop': scoop_at} if getattr(self.ctx.gripper, 'backend', '') == 'dio' else {}
            result = self.ctx.gripper.grip(a['width_mm'], a['force_n'], a['timeout_s'] or 3.0, **options)
            self.ctx.state.pending_scoop_extract = bool(result[0] and result[2] and scoop_at)
            self.ctx.state.scoop_extract_uncertain = False
            if result[0] and result[2]:
                if scoop_at:
                    scoop = self.ctx.stations.get(self.ctx.state.station_id)
                    material_id = scoop.extra.get('material_id')
                    if not isinstance(material_id, str) or not material_id:
                        raise ValueError(f'{self.ctx.state.station_id}.material_id가 필요하다')
                    self.ctx.state.held_payload = 'scoop'
                    self.ctx.state.held_material_id = material_id
                    self.ctx.state.empty_scoop_baseline_pending = True
                elif (anchor is not None and anchor.station == self.ctx.state.station_id and anchor.approach == MoveToStation.Goal.AT
                      and self.ctx.state.station_id in ('workbench', 'passbox_empty', 'passbox_done', 'reject_bin')
                      and self._pose_matches(self.ctx.arm.current_posx(), anchor.pose)
                      and joints_match(self.ctx.arm.current_posj(), anchor.joints, self.ctx.config.joint_tolerance)):
                    self.ctx.state.held_payload = 'cup'
            return result
        self.ctx.state.pending_scoop_extract = False
        self.ctx.state.scoop_extract_uncertain = False
        released = self.ctx.gripper.release(a['timeout_s'] or 3.0)
        if released:
            self.ctx.state.held_payload = 'empty'
            self.ctx.state.held_material_id = ''
            self.ctx.state.empty_scoop_force_baseline = None
            self.ctx.state.empty_scoop_baseline_pending = False
        anchor = getattr(self.ctx.state, 'motion_anchor', None)
        if released and anchor is not None and anchor.station.startswith('scoop_'):
            station = self.ctx.stations.get(anchor.station)
            if 'return_entry_posx' in station.extra:
                target = station.offset_z(station.extra['exit_mm'])
                self._taught_linear(job, target)
                self._record_arrival(station.station_id, MoveToStation.Goal.ABOVE, target)
        return released, self.ctx.gripper.width_mm() or -1.0, False

    def _require_held_scoop(self, material_id=None):
        # 역할: 원료 ID가 맞는 스쿱 파지 이력과 현재 그리퍼 피드백이 모두 확인되지 않으면 거부한다.
        if self.ctx.state.held_payload != 'scoop' or not self.ctx.state.held_material_id:
            raise RuntimeError('원료 ID가 확인된 스쿱 파지 이력이 필요하다')
        if material_id is not None and self.ctx.state.held_material_id != material_id:
            raise RuntimeError('반환 요청 원료와 파지한 스쿱의 원료 ID가 다르다')
        if getattr(self.ctx.gripper, 'backend', '') == 'dio':
            self.ctx.gripper.refresh_dio()
        state = self.ctx.gripper.state(self.ctx.now())
        if state.get('busy', False) or not state.get('grip_inferred', False):
            raise RuntimeError('스쿱 파지 상태가 불확실하다')

    @staticmethod
    def _pose_from_extra(station, key):
        # 역할: 스테이션 부가 설정에서 유한한 6축 자세를 검증하고 원본과 분리된 리스트로 반환한다.
        pose = station.extra.get(key)
        if (not isinstance(pose, list) or len(pose) != 6
                or any(isinstance(v, bool) or not isinstance(v, (int, float))
                       or not math.isfinite(float(v)) for v in pose)):
            raise ValueError(f'{station.station_id}.{key} 6개 유한 좌표가 필요하다')
        return list(pose)

