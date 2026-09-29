"""로봇의 스테이션 이동과 그리퍼 개폐를 실행한다.

스테이션은 stations.yaml의 작업 위치다. AT는 그 위치의 작업점, ABOVE는
접근/이탈용 상부 위치를 뜻한다. TCP 자세(posx)는 그리퍼 끝의 위치와 회전이고,
관절 자세(posj)는 로봇 각 관절의 각도다. 같은 TCP 자세에도 관절 자세는 여러 개일 수 있다.
"""
import math

from .context import Job
from gmp_interfaces.action import MoveToStation
from gmp_skills.core.scooping import finite
from gmp_skills.core.transfer import (MotionAnchor, format_joints, joints_match, joints_match_or_wrist_flipped,
                                      pose_matches, validate_start, vector6)


class MotionSkills:
    def __init__(self, ctx, runtime):
        # ctx에는 로봇/그리퍼, 스테이션 설정, 작업 간에 유지할 상태가 들어 있다.
        self.ctx = ctx
        self.runtime = runtime

    def _require_scoop_extracted(self):
        # 거치대에서 스쿱을 잡은 직후에는 옆으로 빼는 인출 동작이 필요하다.
        # 그 동작이 끝나지 않았거나 성공 여부를 모르면 다른 위치로 이동하지 않는다.
        if self.ctx.state.scoop_extract_uncertain:
            raise RuntimeError('스쿱 인출 상태가 불확실하다. SafePose 후 수동 확인이 필요하다')
        if self.ctx.state.pending_scoop_extract:
            raise RuntimeError('스쿱 파지 후 WeighHeld로 +Y 인출을 먼저 수행해야 한다')

    def _do_move(self, job: Job, *, station_id=None, approach=None):
        # 이동 실패 후에는 현재 위치와 파지물을 확신할 수 없다. 다음 작업이 이전
        # 성공 이력을 근거로 움직이지 않도록 위치·파지·빈 스쿱 영점 정보를 지운다.
        self._require_scoop_extracted()
        self.ctx.state.returned_scoop_stowed = ''
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
        # 실제 그리퍼 끝의 XYZ와 회전을 목표값과 비교한다. 각각 설정된 허용오차를 쓴다.
        return pose_matches(actual, target, self.ctx.config.pose_xyz_tolerance, self.ctx.config.pose_rotation_tolerance)

    def _record_arrival(self, station_id, approach, target):
        # 로봇에서 읽은 TCP가 목표에 도달했을 때만 현재 TCP·관절각을 저장한다.
        # 이 기록(motion_anchor)은 다음 이송에서 출발 자세가 맞는지 확인하는 기준이다.
        actual = self.ctx.arm.current_posx()
        if not self._pose_matches(actual, target):
            raise RuntimeError(f'이동 위치/자세 미도달: target={target}, actual={actual}')
        self.ctx.state.motion_anchor = MotionAnchor(station_id, approach, tuple(actual),
                                           tuple(self.ctx.arm.current_posj()))
        self.ctx.state.station_id = station_id

    def _move_checked(self, job: Job, *, station_id=None, approach=None):
        # stations.yaml의 목적지 설정으로 이동 방식을 고른다: 티칭한 관절 경로,
        # 스테이션 간 전용 경로, 지정 관절 구성(solution_space), 일반 TCP 직선 이동.
        # 이동 후 실제 도착 자세를 기록해야 다음 파지·이송의 출발점을 검증할 수 있다.
        if job.cancel:
            raise RuntimeError('cancelled')
        approach = job.args['approach'] if approach is None else approach
        if approach not in (MoveToStation.Goal.ABOVE, MoveToStation.Goal.AT):
            raise ValueError('approach는 ABOVE(0) 또는 AT(1)이어야 한다')
        st = self.ctx.stations.get(station_id or job.args['station_id'])
        # approach_posj/return_entry_posx가 있으면 아래 티칭 경로를 사용한다.
        # 전용 스테이션 간 이송 경로는 가상 모드에서 사용하지 않는다.
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
            # 이 목적지는 등록된 이송 경로로만 진입한다. 같은 스테이션에서
            # 작업점(AT)과 상부점(ABOVE)을 오가는 경우에만 직선 이동을 허용한다.
            anchor = self.ctx.state.motion_anchor
            anchor_matches = (anchor is not None and anchor.station == st.station_id
                              and self._pose_matches(self.ctx.arm.current_posx(), anchor.pose)
                              and joints_match(self.ctx.arm.current_posj(), anchor.joints,
                                               self.ctx.config.joint_tolerance))
            if not anchor_matches:
                # 기존 기록이 있는데 센서와 다르면 재기록으로 덮지 않는다.
                if anchor is not None:
                    raise ValueError('저장된 출발 이력과 현재 TCP/관절각이 다르다')
                # 재기동으로 저장한 출발 이력이 없을 때만 복원을 시도한다.
                # 이때 실제 TCP 위치·회전이 요청 목표점에 이미 맞으면
                # 이동 없이 기록만 복원한다. 맞지 않으면 이동 경로를 추측하지 않는다.
                for outgoing in self.ctx.stations.transfers.values():
                    if outgoing.source != st.station_id or not outgoing.enabled:
                        continue
                    if outgoing.start_from in ('above', 'exit') and approach != MoveToStation.Goal.ABOVE:
                        continue
                    if self._pose_matches(self.ctx.arm.current_posx(), target):
                        self.ctx.state.held_payload = 'unknown'
                        self._record_arrival(st.station_id, approach, target)
                        return st.station_id
                raise ValueError('등록된 출발 이력이 없는 보호 대상 이송이다')
        self._leave_taught_station(job, st.station_id)
        safe_posj = st.extra.get('posj') if approach != MoveToStation.Goal.ABOVE else None
        if safe_posj is not None:
            job.feedback and job.feedback('HOMING')
            # MOVEJ · 관절각 목표: safe_posj
            self.ctx.arm.movej_cancellable(safe_posj, vel_scale, lambda: job.cancel,
                                       self.ctx.config.motion_timeout_s)
            # safe 스테이션의 posx는 실제 도착 TCP가 아닐 수 있다. 관절 이동이 끝난
            # 뒤 로봇에서 읽은 TCP를 도착값으로 사용한다.
            target = self.ctx.arm.current_posx()
        else:
            job.feedback and job.feedback('MOVING')
            self._leave_solution_station(job, st.station_id, vel_scale)
            if 'solution_space' in st.extra:
                self._move_solution_station(job, st, target, vel_scale)
            else:
                # MOVEL · TCP 직선 이동: target
                self.ctx.arm.movel_cancellable(target, vel_scale, lambda: job.cancel,
                                           self.ctx.config.motion_timeout_s)
        if job.cancel:
            raise RuntimeError('cancelled')
        self._record_arrival(st.station_id, approach, target)
        return st.station_id

    def _motion_scale(self, job):
        """요청 속도 배율만 검증한다. 로봇 이동이나 상태 변경은 하지 않는다."""
        scale = job.args.get('vel_scale') or self.ctx.config.vel_scale
        if not math.isfinite(scale) or not 0 < scale <= 1:
            raise ValueError('vel_scale은 0 초과 1 이하여야 한다')
        return scale

    def _leave_taught_station(self, job, destination):
        # 현재 위치를 떠날 때 저장된 TCP가 실제 위치와 같은지 확인한다.
        # 확인되면 그 스테이션의 exit_mm 높이까지 TCP를 수직으로 올린다.
        source = self.ctx.stations.stations.get(self.ctx.state.station_id)
        if source is None or source.station_id == destination:
            return
        if 'approach_posj' not in source.extra and 'return_entry_posx' not in source.extra:
            return
        anchor = self.ctx.state.motion_anchor
        if (anchor is None or anchor.station != source.station_id
                or not self._pose_matches(self.ctx.arm.current_posx(), anchor.pose)
                or not joints_match(self.ctx.arm.current_posj(), anchor.joints,
                                    self.ctx.config.joint_tolerance)):
            raise RuntimeError('티칭 경로 출발 이력이 불확실하다')
        if self.ctx.state.held_payload not in ('cup', 'empty'):
            raise RuntimeError('티칭 경로 출발 전 인출·파지 확인이 필요하다')
        self._require_transfer_payload(self.ctx.state.held_payload)
        target = list(anchor.pose)
        target[2] = source.posx[2] + source.extra['exit_mm']
        if not self._pose_matches(self.ctx.arm.current_posx(), target):
            # MOVEL · TCP 직선 이동: list(target)
            self.ctx.arm.movel_cancellable(
                list(target), self._motion_scale(job),
                lambda: job.cancel or self.runtime._cancel_requested(),
                self.ctx.config.motion_timeout_s)
        self.ctx.state.motion_anchor = None

    def _move_taught_station(self, job, station, approach):
        """설정된 관절각으로 용기에 접근하거나 스쿱 거치대에 진입·반납한다.

        외부 AT 요청은 작업점까지, ABOVE 요청은 접근 또는 이탈 높이까지 간다.
        용기 스테이션은 티칭한 관절각으로 진입한 뒤 필요하면 TCP를 직선 하강시킨다.
        """
        scale = job.args.get('vel_scale') or self.ctx.config.vel_scale
        if not math.isfinite(scale) or not 0 < scale <= 1:
            raise ValueError('vel_scale은 0 초과 1 이하여야 한다')
        if any(not math.isfinite(v) or v <= 0
               for v in (self.ctx.config.transfer_joint_vel, self.ctx.config.transfer_joint_acc)):
            raise ValueError('티칭 관절 속도·가속도는 유한한 양수여야 한다')
        anchor = self.ctx.state.motion_anchor
        local = (anchor is not None and anchor.station == station.station_id)
        if (local and (not self._pose_matches(self.ctx.arm.current_posx(), anchor.pose)
                       or not joints_match(self.ctx.arm.current_posj(), anchor.joints,
                                           self.ctx.config.joint_tolerance))):
            raise RuntimeError('티칭 스테이션 도착 후 TCP 위치/자세 또는 관절각이 변경되었다')
        if 'return_entry_posx' in station.extra:
            if self.ctx.state.held_payload == 'scoop':
                self._require_held_scoop(station.extra['material_id'])
                if approach != MoveToStation.Goal.AT:
                    raise ValueError('스쿱 반납은 AT 요청으로 실행해야 한다')
                entry = self._pose_from_extra(station, 'return_entry_posx')
                lower = list(entry)
                lower[2] -= finite(station.extra['return_lower_mm'], '반납 하강량')
                targets = [entry, lower, station.posx]
                returning = getattr(self.ctx.state, 'return_rescoop_blocked', False)
                if returning:
                    material_id = station.extra['material_id']
                    if getattr(self.ctx.state, 'returned_material', '') != material_id:
                        raise RuntimeError('원료 반환 성공이 확인되지 않아 수납 연결을 차단한다')
                    material = self.ctx.stations.for_material(material_id)
                    end = self._pose_from_extra(material, 'return_end_posj')
                    actual_joints = self.ctx.arm.current_posj()
                    # 반환 끝 확인(scooping)과 같은 기준 — 손목만 뒤집힌 해도 같은 반환 끝이다
                    if not joints_match_or_wrist_flipped(actual_joints, end, self.ctx.config.joint_tolerance):
                        raise RuntimeError('원료 반환 끝 관절 자세가 아니므로 수납 연결을 차단한다: '
                                           f'현재 {format_joints(actual_joints)} 기준 {format_joints(end)}')
                    # DRL 순서: 반환 끝 → 원료 계량 자세 → 반환 진입점 → 하강 → 삽입.
                    targets = [list(material.posx)] + targets
                    # 중간 실패 이후에는 반환 성공 이력으로 경로를 다시 시작하지 않는다.
                    self.ctx.state.returned_material = ''
                    self.ctx.state.returned_scoop_stowed = ''
                for target in targets:
                    # MOVEL · TCP 직선 이동: list(target)
                    self.ctx.arm.movel_cancellable(
                        list(target), self._motion_scale(job),
                        lambda: job.cancel or self.runtime._cancel_requested(),
                        self.ctx.config.motion_timeout_s)
            else:
                self._require_transfer_payload('empty')
                self._leave_taught_station(job, station.station_id)
                target = (station.offset_z(station.extra['exit_mm']) if local
                          and approach == MoveToStation.Goal.ABOVE else station.above(self.ctx.stations.approach_mm))
                if not local or approach == MoveToStation.Goal.ABOVE:
                    # MOVEL · TCP 직선 이동: list(target)
                    self.ctx.arm.movel_cancellable(
                        list(target), self._motion_scale(job),
                        lambda: job.cancel or self.runtime._cancel_requested(),
                        self.ctx.config.motion_timeout_s)
                if approach == MoveToStation.Goal.AT:
                    # MOVEL · TCP 직선 이동: list(station.posx)
                    self.ctx.arm.movel_cancellable(
                        list(station.posx), self._motion_scale(job),
                        lambda: job.cancel or self.runtime._cancel_requested(),
                        self.ctx.config.motion_timeout_s)
        else:
            if self.ctx.state.held_payload not in ('cup', 'empty'):
                raise RuntimeError('용기 스테이션 진입 전 파지/열림 이력이 필요하다')
            self._require_transfer_payload(self.ctx.state.held_payload)
            if local:
                target = list(anchor.pose)
                if approach == MoveToStation.Goal.AT:
                    # 빈 그리퍼로 workbench에 들어오는 관절각은 용기를 들고 들어올 때와
                    # 다르다. 저장된 회전·XY를 유지하고 Z만 작업점 높이로 내린다.
                    target[2] = station.posx[2]
                    if self.ctx.state.held_payload == 'cup':
                        target = list(station.posx)
                else:
                    target[2] = station.posx[2] + station.extra['exit_mm']
                # MOVEL · TCP 직선 이동: list(target)
                self.ctx.arm.movel_cancellable(
                    list(target), self._motion_scale(job),
                    lambda: job.cancel or self.runtime._cancel_requested(),
                    self.ctx.config.motion_timeout_s)
            else:
                self._leave_taught_station(job, station.station_id)
                empty_entry = self.ctx.state.held_payload == 'empty' and 'empty_approach_posj' in station.extra
                if empty_entry:
                    # MOVEL · TCP 직선 이동: list(self._pose_from_extra(station, 'middle_posx'))
                    self.ctx.arm.movel_cancellable(
                        list(self._pose_from_extra(station, 'middle_posx')), self._motion_scale(job),
                        lambda: job.cancel or self.runtime._cancel_requested(),
                        self.ctx.config.motion_timeout_s)
                key = 'empty_approach_posj' if empty_entry else 'approach_posj'
                joints = list(vector6(station.extra[key], key))
                # MOVEJ · 관절각 목표: joints
                self.ctx.arm.movej_cancellable(joints, scale,
                    lambda: job.cancel or self.runtime._cancel_requested(), self.ctx.config.motion_timeout_s,
                    joint_vel=self.ctx.config.transfer_joint_vel, joint_acc=self.ctx.config.transfer_joint_acc)
                self._require_transfer_payload(self.ctx.state.held_payload)
                if approach == MoveToStation.Goal.AT:
                    if empty_entry:
                        target = list(self.ctx.arm.current_posx())
                        target[2] -= station.extra['empty_descent_mm']
                    else:
                        target = station.posx
                    # MOVEL · TCP 직선 이동: list(target)
                    self.ctx.arm.movel_cancellable(
                        list(target), self._motion_scale(job),
                        lambda: job.cancel or self.runtime._cancel_requested(),
                        self.ctx.config.motion_timeout_s)
            self._require_transfer_payload(self.ctx.state.held_payload)
        if job.cancel or self.runtime._cancel_requested():
            raise RuntimeError('cancelled')
        # 장치 어댑터가 목표 도달을 확인했다. 관절각에서 TCP를 계산해 가정하지 않고
        # 로봇이 보고한 현재 TCP·관절각을 다음 출발 기록으로 저장한다.
        self._record_arrival(station.station_id, approach, self.ctx.arm.current_posx())
        if ('return_entry_posx' in station.extra and self.ctx.state.held_payload == 'scoop'
                and getattr(self.ctx.state, 'return_rescoop_blocked', False)):
            if not self._pose_matches(self.ctx.arm.current_posx(), station.posx):
                raise RuntimeError('반환 스쿱 수납 위치 미도달')
            self.ctx.state.returned_scoop_stowed = station.station_id
        return station.station_id

    def _require_solution(self, station):
        # solution_space는 같은 TCP 위치를 만드는 관절 자세의 분기 번호(0~7)다.
        # 실제 로봇의 분기 번호가 stations.yaml 지정값과 같은지 확인한다.
        sol = self.ctx.arm.solution_space()
        if sol != station.extra['solution_space']:
            raise RuntimeError(f'{station.station_id}: 관절 구성 불일치 sol={sol}')

    def _leave_solution_station(self, job, destination, vel_scale):
        """지정 관절 분기의 용기 스테이션에서 다음 위치로 가기 전 수직 이탈한다.

        저장된 도착 자세와 실제 TCP, 그리퍼 파지, 관절 분기 번호를
        확인한 뒤 station.exit()까지 TCP 직선 이동한다.
        """
        if self.ctx.state.station_id == destination or self.ctx.state.station_id not in self.ctx.stations.stations:
            return
        source = self.ctx.stations.get(self.ctx.state.station_id)
        if 'solution_space' not in source.extra:
            return
        anchor = self.ctx.state.motion_anchor
        if (anchor is None or anchor.station != source.station_id
                or not self._pose_matches(self.ctx.arm.current_posx(), anchor.pose)
                or not joints_match(self.ctx.arm.current_posj(), anchor.joints,
                                    self.ctx.config.joint_tolerance)):
            raise RuntimeError('용기 스테이션 출발 이력이 불확실하다')
        if self.ctx.state.held_payload not in ('cup', 'empty'):
            raise RuntimeError('용기 이송 전 파지 상태 확인이 필요하다')
        self._require_transfer_payload(self.ctx.state.held_payload)
        self._require_solution(source)
        # MOVEL · TCP 직선 이동: source.exit()
        self.ctx.arm.movel_cancellable(source.exit(), vel_scale, lambda: job.cancel,
                                   self.ctx.config.motion_timeout_s)
        self._require_solution(source)
        self._require_transfer_payload(self.ctx.state.held_payload)
        self.ctx.state.motion_anchor = None

    def _move_solution_station(self, job, station, target, vel_scale):
        """지정된 관절 분기로 ABOVE에 접근한 뒤 필요하면 AT까지 직선 이동한다."""
        if self.ctx.state.held_payload in ('cup', 'empty'):
            self._require_transfer_payload(self.ctx.state.held_payload)
        above = station.above(self.ctx.stations.approach_mm)
        actual = self.ctx.arm.current_posx()
        at_station = (self._pose_matches(actual, station.posx)
                      or self._pose_matches(actual, above))
        if at_station:
            # 이미 작업점(AT) 또는 상부점(ABOVE)에 있으면 관절 이동을 반복하지 않는다.
            # 작업자가 펜던트로 같은 TCP 위치에 옮겼을 수도 있으므로, 현재 관절
            # 분기 번호가 station.extra['solution_space']와 같은지는 반드시 확인한다.
            self._require_solution(station)
        else:
            # MOVEJX · TCP 목표까지 관절 이동: above
            self.ctx.arm.movejx_cancellable(above, station.extra['solution_space'], vel_scale,
                                        lambda: job.cancel, self.ctx.config.motion_timeout_s)
            self._require_solution(station)
        if self.ctx.state.held_payload in ('cup', 'empty'):
            self._require_transfer_payload(self.ctx.state.held_payload)
        if not self._pose_matches(self.ctx.arm.current_posx(), target):
            # MOVEL · TCP 직선 이동: target
            self.ctx.arm.movel_cancellable(target, vel_scale, lambda: job.cancel,
                                       self.ctx.config.motion_timeout_s)
        self._require_solution(station)

    def _require_transfer_payload(self, expected):
        # 내부 파지 기록(held_payload)과 최신 그리퍼 센서가 둘 다 expected와
        # 맞는지 확인한다. DIO는 닫힘/열림 입력, Modbus는 파지 비트와 폭을 쓴다.
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
        # route에는 출발 이탈점, 중간 관절점, 도착 방식이 저장돼 있다.
        # 실제 출발 TCP/관절각은 마지막 도착 기록과 비교하고 EXIT 도달 후 관절 경로로 연결한다.
        # 실제 출발 자세·파지물을 검증한 뒤 각 구간을 실행하고 도착을 확인한다.
        if route.arrival == 'at' and job.args['approach'] != MoveToStation.Goal.AT:
            raise ValueError('관절 직접 도착 경로는 AT 요청만 허용한다')
        validate_start(route, self.ctx.state.motion_anchor, self.ctx.arm.current_posx(),
                       self.ctx.arm.current_posj(),
                       self.ctx.state.held_payload,
                       self.ctx.config.pose_xyz_tolerance, self.ctx.config.pose_rotation_tolerance,
                       self.ctx.config.joint_tolerance)
        if any(not math.isfinite(v) or v <= 0
               for v in (self.ctx.config.transfer_joint_vel, self.ctx.config.transfer_joint_acc)):
            raise ValueError('이송 관절 속도·가속도를 먼저 설정해야 한다')
        self._require_transfer_payload(route.payload)
        self.ctx.state.motion_anchor = None
        job.feedback and job.feedback('MOVING')

        def checkpoint():
            # 각 이동 구간 사이에 취소 여부와 용기/빈 그리퍼 센서 상태를 다시 확인한다.
            if job.cancel:
                raise RuntimeError('cancelled')
            self._require_transfer_payload(route.payload)

        # 출발 이탈점에 이미 있으면 중복 이동하지 않는다. 마지막 관절점이
        # 목적지 작업점(AT)인지 상부점(ABOVE)인지는 route.arrival로 정한다.
        checkpoint()
        if not self._pose_matches(self.ctx.arm.current_posx(), route.exit_posx):
            # MOVEL · TCP 직선 이동: route.exit_posx
            self.ctx.arm.movel_cancellable(route.exit_posx, vel_scale, lambda: job.cancel,
                                       self.ctx.config.motion_timeout_s)
        if not self._pose_matches(self.ctx.arm.current_posx(), route.exit_posx):
            raise RuntimeError('직선 이탈 목표 위치/자세에 도달하지 못했다')
        for point in route.waypoints_posj:
            checkpoint()
            # MOVEJ · 관절각 목표: point
            self.ctx.arm.movej_cancellable(point, vel_scale, lambda: job.cancel, self.ctx.config.motion_timeout_s,
                                       joint_vel=self.ctx.config.transfer_joint_vel, joint_acc=self.ctx.config.transfer_joint_acc)
        checkpoint()
        destination = self.ctx.stations.get(route.destination)
        entry = (destination.posx if route.arrival == 'at'
                 else destination.above(self.ctx.stations.approach_mm))
        if not self._pose_matches(self.ctx.arm.current_posx(), entry):
            raise RuntimeError(f'마지막 관절점이 목적지 {route.arrival.upper()} 위치/자세와 일치하지 않는다')
        if route.arrival == 'above' and job.args['approach'] == MoveToStation.Goal.AT:
            # MOVEL · TCP 직선 이동: target
            self.ctx.arm.movel_cancellable(target, vel_scale, lambda: job.cancel, self.ctx.config.motion_timeout_s)
        checkpoint()
        self._record_arrival(route.destination, job.args['approach'], target)

    def _do_grip(self, job: Job):
        # 현재 위치와 실제 그리퍼 입력으로 스쿱/용기 파지를 판정한다. 스쿱을
        # 잡은 직후에는 인출 대기만 표시한다. +Y 인출은 다음 WeighHeld가 수행한다.
        a = job.args
        anchor = self.ctx.state.motion_anchor
        stowed = getattr(self.ctx.state, 'returned_scoop_stowed', '')
        clear_return = bool(
            not a['close'] and stowed and anchor is not None
            and anchor.station == stowed and anchor.approach == MoveToStation.Goal.AT
            and self.ctx.state.held_payload == 'scoop'
            and self.ctx.stations.get(stowed).extra['material_id'] == self.ctx.state.held_material_id
            and self._pose_matches(self.ctx.arm.current_posx(), anchor.pose)
            and joints_match(self.ctx.arm.current_posj(), anchor.joints, self.ctx.config.joint_tolerance))
        if not a['close'] and stowed and not clear_return:
            raise RuntimeError('반환 스쿱 수납 자세가 변경되어 열기를 차단한다')
        self.ctx.state.returned_scoop_stowed = ''
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
                # MOVEL · TCP 직선 이동: list(target)
                self.ctx.arm.movel_cancellable(
                    list(target), self._motion_scale(job),
                    lambda: job.cancel or self.runtime._cancel_requested(),
                    self.ctx.config.motion_timeout_s)
                self._record_arrival(station.station_id, MoveToStation.Goal.ABOVE, target)
        if released and clear_return and not job.cancel and not self.runtime._cancel_requested():
            self.ctx.state.return_rescoop_blocked = False
        return released, self.ctx.gripper.width_mm() or -1.0, False

    def _require_held_scoop(self, material_id=None):
        # 이전 작업이 저장한 스쿱·원료 ID와 최신 그리퍼 파지 입력을 함께 검사한다.
        # 반환 요청이라면 들고 있는 스쿱의 원료 ID도 요청값과 같아야 한다.
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
        # stations.yaml의 해당 6축 좌표가 숫자 6개인지 확인한다. 복사본을
        # 반환해 경유점 Z를 수정해도 원래 스테이션 설정은 바뀌지 않게 한다.
        pose = station.extra.get(key)
        if (not isinstance(pose, list) or len(pose) != 6
                or any(isinstance(v, bool) or not isinstance(v, (int, float))
                       or not math.isfinite(float(v)) for v in pose)):
            raise ValueError(f'{station.station_id}.{key} 6개 유한 좌표가 필요하다')
        return list(pose)
