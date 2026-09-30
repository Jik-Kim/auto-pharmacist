"""로봇 알람과 외력을 감시하고 안전 차단·작업자 복구를 처리한다.

넛지는 사람이 로봇에 준 외력을 입력으로 인식하는 기능이다. safety_latched는
새 작업을 막는 차단 상태이고, safety_revision은 그 상태가 바뀔 때 증가한다.
복구 요청은 시작 시의 revision과 현재 값을 비교해 중간 알람을 놓치지 않는다.
"""
import json
import math
import time

from .context import Job
from gmp_skills.core.transfer import MotionAnchor, joints_match, pose_matches
from gmp_skills.core.recovery import recovery_step, STANDBY


class SafetyController:
    def __init__(self, ctx, runtime):
        # 노드 전체 대신 필요한 장치·설정·상태·콜백만 공유한다.
        self.ctx = ctx
        self.runtime = runtime

    def _observe_force(self, force6):
        # 외력 6축 표본 중 앞의 3개 힘 성분을 넛지 판정에 사용한다.
        # 취소가 먼저 들어왔으면 입력 이벤트를 내지 않고 현재 작업을 중단한다.
        if self.runtime._cancel_requested():
            raise RuntimeError('cancelled')
        if self.ctx.state.nudge_enabled and self.ctx.state.nudge.update(force6, self.ctx.now()):
            magnitude_n = math.sqrt(sum(float(v) ** 2 for v in force6[:3]))
            self.ctx.event('INFO', 'NUDGE', f'외력 nudge 입력 감지: |F|={magnitude_n:.2f} N')

    def _poll_nudge(self):
        # 워커가 로봇의 현재 외력을 읽어 넛지 입력으로 처리한다.
        # 센서 조회가 실패하면 입력을 신뢰할 수 없어 안전 차단을 건다.
        if not self.ctx.state.nudge_enabled or self.ctx.state.safety_latched or not self.ctx.state.configured:
            return
        try:
            force = self.ctx.arm.tool_force()
            if force is None:
                raise RuntimeError('외력 조회 결과 없음')
            self._observe_force(force)
            self.ctx.state.nudge_fault_logged = False
        except Exception as exc:  # noqa: BLE001 — 유휴 감시 실패가 워커를 죽이면 안 된다
            if not self.ctx.state.nudge_fault_logged:
                self.ctx.event('WARN', 'NUDGE_UNAVAILABLE', f'외력 감시 실패: {exc}')
                self.ctx.state.nudge_fault_logged = True
            self._latch_safety(f'외력 조회 실패: {exc}')
            # 스킬 수행 중에는 이 실패를 삼키고 다음 이동을 이어가지 않는다.
            if self.ctx.state.current is not None:
                raise

    def _wait_with_nudge(self, duration_s: float, job: Job):
        # Pour 자세 유지 등 대기 중에도 취소와 넛지 외력 감시를 계속한다.
        end_s = self.ctx.now() + max(0.0, duration_s)
        while self.ctx.now() < end_s:
            if job.cancel:
                raise RuntimeError('cancelled')
            self._poll_nudge()
            time.sleep(min(0.1, max(0.0, end_s - self.ctx.now())))

    def _latch_safety(self, reason, *, alarm=False, recovery_request=None):
        # 알람 콜백에서도 호출되므로 여기서는 공유 상태와 대기 Job만 변경한다.
        # 현재 작업을 취소하고 위치·파지 기록을 지운다. 장치 정지·복구 명령은
        # DSR 호출을 소유한 워커에서 실행한다.
        with self.ctx.state.job_lock:
            changed = not self.ctx.state.safety_latched or reason != self.ctx.state.safety_reason
            if changed or alarm or recovery_request is not None:
                self.ctx.state.safety_revision += 1
            self.ctx.state.safety_latched = True
            self.ctx.state.safety_reason = reason
            self.ctx.state.motion_anchor = None
            self.ctx.state.held_payload = 'unknown'
            self.ctx.state.held_material_id = ''
            self.ctx.state.resume_grip = None
            self.ctx.state.resume_grip_ready = False
            self.ctx.state.empty_scoop_force_baseline = None
            self.ctx.state.empty_scoop_baseline_pending = False
            self.ctx.state.scoop_extract_uncertain = True
            if self.ctx.state.current and self.ctx.state.current.kind != 'startup':
                self.ctx.state.current.cancel = True
            self.runtime._drain_jobs_locked(f'SAFETY_STOP: {reason}')
            # 잠금 안에서 revision 갱신과 이벤트 발행을 같은 순서로 처리한다.
            # 실제 로봇 알람에는 특정 작업자의 복구 요청 ID를 붙이지 않는다.
            if changed or alarm or recovery_request is not None:
                detail = dict(robot_state=self.ctx.state.last_robot_state, reason=reason,
                              origin='robot_alarm' if alarm else 'state_monitor',
                              safety_session=self.ctx.state.safety_session,
                              safety_revision=self.ctx.state.safety_revision)
                if recovery_request is not None:
                    detail.update(origin='recovery_request',
                                  request_id=recovery_request.request_id,
                                  operator_id=recovery_request.operator_id)
                self.ctx.event('ERROR', 'ROBOT_SAFETY_STOP', json.dumps(detail, ensure_ascii=False))
            return self.ctx.state.safety_revision

    def _poll_safety(self, force=False):
        # 실물 컨트롤러의 상태 번호를 읽는다. 허용된 상태 1/2가 아니거나
        # 조회에 실패하면 새로운 이동을 막는 안전 차단을 건다.
        if self.ctx.config.mode == 'virtual':
            return
        now = self.ctx.now()
        if not force and now - self.ctx.state.last_state_poll < self.ctx.config.state_poll_s:
            return
        self.ctx.state.last_state_poll = now
        try:
            self.ctx.state.last_robot_state = self.ctx.arm.robot_state()
        except Exception as exc:  # noqa: BLE001 — 조회 불가도 동작 허용 근거가 아니다.
            self.ctx.state.last_robot_state = -1
            self._latch_safety(f'로봇 상태 조회 실패: {exc}')
            return
        if self.ctx.state.last_robot_state not in (1, 2):
            self._latch_safety(f'로봇 상태 {self.ctx.state.last_robot_state}: 작업자 복구 필요')

    def _do_recover(self, job):
        # 작업자가 확인한 현재 상태에 맞는 두산 복구 명령만 워커에서 보낸다.
        # 기대 상태 도달과 툴·TCP·충돌 감도를 다시 확인해야 차단을 해제한다.
        args = job.args
        if self.ctx.config.mode == 'virtual':
            return False, True, -1, '가상 모드의 안전 복구는 실물 복구 성공으로 처리하지 않습니다'
        revision = args.get('safety_revision', self.ctx.state.safety_revision)
        if revision != self.ctx.state.safety_revision:
            return False, True, self.ctx.state.last_robot_state, '복구 대기 중 새 정지 발생. 차단 유지'
        state = self.ctx.arm.robot_state()
        self.ctx.state.last_robot_state = state
        if state != args['expected_state']:
            return False, True, state, '로봇 상태가 요청 이후 변경됐습니다. 상태를 확인하고 새 요청을 보내세요'
        step = recovery_step(state, args['operator_confirmed'])
        if step.control is not None:
            if self.ctx.state.stopping.is_set() or job.cancel:
                raise RuntimeError('복구 요청 취소됨')
            def dispatch(operation):
                # 복구 명령을 보내는 순간에만 잠근다. 전송 직전 revision·종료·취소를
                # 다시 검사하고, 응답 대기 중에는 새 알람 콜백이 실행되도록 잠금을 푼다.
                with self.ctx.state.job_lock:
                    if (self.ctx.state.safety_revision != revision or self.ctx.state.stopping.is_set()
                            or job.cancel):
                        raise RuntimeError('복구 명령 전 새 알람·종료·취소 발생')
                    return operation()
            self.ctx.arm.recover_control(step.control, self.ctx.config.recovery_timeout_s, dispatch)
        deadline = self.ctx.now() + self.ctx.config.recovery_timeout_s
        while True:
            if self.ctx.state.stopping.is_set() or job.cancel:
                raise RuntimeError('복구 요청 취소됨')
            state = self.ctx.arm.robot_state()
            self.ctx.state.last_robot_state = state
            if state == step.target:
                break
            if self.ctx.now() >= deadline:
                return False, True, state, '복구 후 기대 상태 미도달. 작업자 확인 필요'
            time.sleep(self.ctx.config.state_poll_s)
        if step.manual_required:
            return False, True, state, '복구 모드 진입. 펜던트에서 원인 제거·자세 교정 후 새 복구 요청 필요'
        # 로봇 상태가 STANDBY(대기)여도 힘/순응 제어가 남아 있을 수 있다.
        # 해제 실패 시 복구 성공으로 보고하지 않는다.
        self.ctx.arm.compliance_off()
        if not self.ctx.state.configured:
            self.ctx.arm.initialize()
        # 복구 때도 감도 변경·조회 실패를 확인한 뒤에만 차단을 해제한다.
        ok, detail = self.ctx.arm.self_check(self.ctx.parameter('robot.tool_name').value,
                                         self.ctx.parameter('robot.tcp_name').value,
                                         self.ctx.parameter('safety.collision_sensitivity').value)
        if not ok:
            self.ctx.state.configured = False
            raise RuntimeError(f'복구 후 자가진단 실패: {detail}')
        self.ctx.state.configured = True
        state = self.ctx.arm.robot_state()
        with self.ctx.state.job_lock:
            if (self.ctx.state.safety_revision != revision or self.ctx.state.stopping.is_set()
                    or job.cancel or state != STANDBY):
                return False, True, state, '복구 중 새 알람·정지·상태 변경 발생. 차단 유지'
            self.ctx.state.safety_latched = False
            self.ctx.state.safety_reason = ''
            self.ctx.state.station_id = ''
            self.ctx.state.motion_anchor = None
            self.ctx.state.held_payload = 'unknown'
            self.ctx.state.held_material_id = ''
            self.ctx.state.empty_scoop_force_baseline = None
            self.ctx.state.empty_scoop_baseline_pending = False
            self.ctx.state.pending_scoop_extract = False
        # 로봇 상태 복구는 이전 배치의 재개가 아니다. 스쿱 인출 여부를 불확실하게
        # 표시해 이후 이동 전 SafePose와 현장 확인을 요구한다.
            self.ctx.state.scoop_extract_uncertain = True
        return True, False, state, '로봇 복구 확인. 배치 재개·자세 이동은 수행하지 않았습니다'

    def _do_safe(self, job: Job):
        # 남은 힘/순응 제어 해제를 시도하고 stations.yaml의 safe.posj로
        # 관절 이동한다. 이후 내부 위치·스쿱 인출 기록을 새 상태로 갱신한다.
        try:
            self.ctx.arm.compliance_off()
        except Exception:  # noqa: BLE001 — 힘제어 중이 아니었으면 무시
            pass
        safe = self.ctx.stations.get('safe')
        posj = safe.extra.get('posj')
        if posj is None:
            raise ValueError('safe station에 posj 6개가 필요하다')
        # 안전 자세 요청이 시작되면 이전 스테이션에서 검증한 TCP/관절 출발 이력은 더는
        # 유효하지 않다. 이동이 실패해도 중간 자세일 수 있으므로 성공 뒤까지 보존하지 않는다.
        self.ctx.state.motion_anchor = None
        self._retreat_before_safe(job)
        # MOVEJ · 관절각 목표: posj
        self.ctx.arm.movej_cancellable(posj, 0.3, lambda: job.cancel, self.ctx.config.motion_timeout_s)
        self.ctx.state.station_id = 'safe'
        # 관절각이 safe.posj 에 도착했으면 그 실제 TCP·관절각을 출발 이력으로 남긴다 — safe → nudge_wait
        # 이송(폐기 뒤 넛지 대기, 9/30)이 이 이력으로 출발을 검증한다. safe 에는 approach_posj·solution_space
        # 가 없어 다른 이동은 이 이력을 읽지 않는다. safe.posx 는 실제 TCP 와 달라(9/30 실측) 관절각으로 판정한다.
        actual_joints = self.ctx.arm.current_posj()
        if joints_match(actual_joints, posj, self.ctx.config.joint_tolerance):
            self.ctx.state.motion_anchor = MotionAnchor('safe', 1, tuple(self.ctx.arm.current_posx()),
                                                        tuple(actual_joints))
        self.ctx.state.pending_scoop_extract = False
        self.ctx.state.scoop_extract_uncertain = False
        self.ctx.state.held_material_id = ''
        self.ctx.state.empty_scoop_force_baseline = None
        self.ctx.state.empty_scoop_baseline_pending = False
        self.ctx.state.resume_grip_ready = True
        return True

    # 용기를 놓는 자리 — 놓는 자세(AT)에서 안전 자세로 곧장 관절 이동하면 팔이 걸린다(9/30 폐기함)
    CUP_PLACE_STATIONS = ('reject_bin', 'passbox_done', 'passbox_empty', 'workbench')
    RETREAT_VEL_SCALE = 0.3

    def _retreat_before_safe(self, job: Job):
        """SafePose 의 movej 전에 걸릴 자리에서 먼저 빠진다 (9/30 실물 충돌 2건).

        A. 용기 자리의 놓는 자세(AT)에서 용기를 쥐고 있으면 → 열어서 놓고 → 그 자리의 이탈 높이로 수직 상승.
           9/30: 폐기함 놓는 자세에서 ENTER → SafePose movej → 팔이 폐기함에 걸려 다시 안전 정지.
        B. 원료통 반환 끝(스쿱 기운 자세)이면 → 원료 계량 자세로 직선 이동(스쿱 수납 연결의 첫 구간과 같다).
           9/30: 반환 끝에서 SafePose movej → 원료통을 치고 스쿱을 문 채 복귀.
        그 밖(스쿱 거치대 안 포함 — 옆으로 빠져야 해 수직 상승이면 걸린다)은 종전대로 곧장 movej.
        """
        try:
            station = self.ctx.stations.get(self.ctx.state.station_id)
            pose = list(self.ctx.arm.current_posx())
        except Exception:  # noqa: BLE001 — 위치를 모르면 추측해 움직이지 않는다
            return
        if len(pose) != 6:
            return
        cancel = lambda: job.cancel
        timeout = self.ctx.config.motion_timeout_s
        if station.station_id in self.CUP_PLACE_STATIONS and 'exit_mm' in station.extra:
            if not pose_matches(pose, station.posx, 5.0, 3.0):
                return
            state = self.ctx.gripper.state(self.ctx.now()) if self.ctx.gripper else {}
            if state.get('grip_inferred', False):
                if not self.ctx.gripper.release(3.0):
                    raise RuntimeError('안전 자세 전 용기 놓기(그리퍼 열기) 미확인 — 현장 확인 필요')
                self.ctx.state.held_payload = 'empty'
            target = list(pose)
            target[2] = station.exit()[2]
            # MOVEL · TCP 직선 이동: target (놓는 자세 → 이탈 높이, 수직)
            self.ctx.arm.movel_cancellable(target, self.RETREAT_VEL_SCALE, cancel, timeout)
            return
        end = station.extra.get('return_end_posx')
        if end is not None and pose_matches(pose, end, 10.0, 5.0):
            # MOVEL · TCP 직선 이동: station.posx (반환 끝 → 원료 계량 자세)
            self.ctx.arm.movel_cancellable(list(station.posx), self.RETREAT_VEL_SCALE, cancel, timeout)

    def _do_restore_grip(self, job: Job):
        """안전 자세에서 개폐 없이 파지 이력을 복구한다. 불명확한 물체는 거부한다."""
        state = self.ctx.state
        saved = state.resume_grip
        requested = job.args.get('expected_payload', '')
        material = job.args.get('expected_material_id', '')
        if requested not in ('', 'empty', 'cup', 'scoop'):
            raise RuntimeError('잘못된 기대 파지 상태')
        if (requested == 'scoop' and not material) or (requested != 'scoop' and material):
            raise RuntimeError('기대 원료는 scoop 기대 시에만 필수다')
        if not state.resume_grip_ready or saved is None:
            raise RuntimeError('안전 자세 완료 및 중단 전 파지 이력이 필요하다')
        if not saved['resumable'] or saved['pending'] or saved['uncertain']:
            raise RuntimeError('중단 작업/스쿱 인출 결과가 불확실하여 자동 재개할 수 없다')
        safe = self.ctx.stations.get('safe').extra['posj']
        if state.station_id != 'safe' or not joints_match(
                self.ctx.arm.current_posj(), safe, self.ctx.config.joint_tolerance):
            raise RuntimeError('안전 자세 이탈 — 파지 복구 후 재개 불가')
        revision = state.safety_revision
        gripper = self.ctx.gripper
        if gripper.backend == 'dio':
            opened = gripper.confirm_open_dio()
        else:
            opened = False
        sensor = gripper.state(self.ctx.now())
        width = sensor.get('width_mm')
        if gripper.backend != 'dio':
            opened = (width is not None and math.isfinite(width)
                      and abs(width - gripper.open_width_mm) <= gripper.grip_margin_mm
                      and not sensor.get('grip_inferred', False))
        if sensor.get('busy', True) or sensor.get('safety_triggered') or sensor.get('slip'):
            raise RuntimeError('그리퍼 센서 상태가 불확실하여 재개할 수 없다')
        expected = saved['payload']
        material_id = ''
        if opened and expected in ('empty', 'unknown'):
            payload = 'empty'
        elif not opened and sensor.get('grip_inferred') and expected in ('cup', 'scoop'):
            payload = expected
            if payload == 'scoop':
                material_id = saved['material_id']
                if not material_id:
                    raise RuntimeError('스쿱 원료 이력 없음 — 자동 재개 불가')
        else:
            raise RuntimeError('센서와 중단 전 파지 이력이 불일치한다. 물체 확인이 필요하다')
        if requested and (payload != requested or material_id != material):
            raise RuntimeError('C의 기대 파지/원료와 복구 상태가 불일치한다')
        with state.job_lock:
            if job.cancel or state.stopping.is_set() or state.safety_latched or revision != state.safety_revision:
                raise RuntimeError('파지 복구 중 취소/안전 상태 변경 — 재개 불가')
            state.held_payload, state.held_material_id = payload, material_id
            state.pending_scoop_extract = False
            state.scoop_extract_uncertain = False
        return payload, material_id
