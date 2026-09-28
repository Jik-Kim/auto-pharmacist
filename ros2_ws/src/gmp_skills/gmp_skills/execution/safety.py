"""안전 감시·차단·복구와 넛지 관측. 상태 잠금 및 복구 세대 검증을 유지한다."""
import json
import math
import time

from .context import Job
from gmp_skills.core.recovery import recovery_step, STANDBY


class SafetyController:
    def __init__(self, ctx, runtime):
        # 노드 전체 대신 필요한 장치·설정·상태·콜백만 공유한다.
        self.ctx = ctx
        self.runtime = runtime

    def _observe_force(self, force6):
        # 역할: 계량·대기 중 받은 외력 표본으로 취소를 확인하고 넛지 입력을 판정한다.
        if self.runtime._cancel_requested():
            raise RuntimeError('cancelled')
        if self.ctx.state.nudge_enabled and self.ctx.state.nudge.update(force6, self.ctx.now()):
            magnitude_n = math.sqrt(sum(float(v) ** 2 for v in force6[:3]))
            self.ctx.event('INFO', 'NUDGE', f'외력 nudge 입력 감지: |F|={magnitude_n:.2f} N')

    def _poll_nudge(self):
        # 역할: 워커에서 외력을 읽어 넛지를 감시한다. 조회 실패는 안전 차단으로 연결한다.
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
        # 역할: 정해진 시간 동안 대기하되 취소와 외력 감시를 계속 수행한다.
        end_s = self.ctx.now() + max(0.0, duration_s)
        while self.ctx.now() < end_s:
            if job.cancel:
                raise RuntimeError('cancelled')
            self._poll_nudge()
            time.sleep(min(0.1, max(0.0, end_s - self.ctx.now())))

    def _latch_safety(self, reason, *, alarm=False, recovery_request=None):
        # 콜백은 상태만 저장한다. 정지·복구 명령은 워커에서만 실행한다.
        # 역할: 안전 차단을 걸고 진행·대기 작업과 위치/파지 이력을 무효화하며 정지 원인을 기록한다.
        with self.ctx.state.job_lock:
            changed = not self.ctx.state.safety_latched or reason != self.ctx.state.safety_reason
            if changed or alarm or recovery_request is not None:
                self.ctx.state.safety_revision += 1
            self.ctx.state.safety_latched = True
            self.ctx.state.safety_reason = reason
            self.ctx.state.motion_anchor = None
            self.ctx.state.held_payload = 'unknown'
            self.ctx.state.held_material_id = ''
            self.ctx.state.empty_scoop_force_baseline = None
            self.ctx.state.empty_scoop_baseline_pending = False
            self.ctx.state.scoop_extract_uncertain = True
            if self.ctx.state.current and self.ctx.state.current.kind != 'startup':
                self.ctx.state.current.cancel = True
            self.runtime._drain_jobs_locked(f'SAFETY_STOP: {reason}')
            # 상태 변경과 발행을 직렬화한다. 실제 알람에는 복구 요청 상관관계를 붙이지 않는다.
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
        # 역할: 실물 로봇 상태를 주기적으로 조회하고 허용 상태가 아니거나 조회 실패 시 차단한다.
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
        # 역할: 워커에서 승인된 복구 단계를 실행하고 상태·자가진단을 재확인한다. 배치를 자동 재개하지 않는다.
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
                # 비동기 전송 순간만 잠근다. 응답 대기 중에는 알람 콜백이 실행돼야 한다.
                # 역할: 복구 명령 전송 직전에 잠금 아래 새 알람·종료·취소 여부를 재검증한다.
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
        # STANDBY에서도 남아 있는 힘제어 해제 실패를 숨기지 않는다.
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
            # 자동 재개는 금지하고 이후 명시적 SafePose/현장 재설정을 요구한다.
            self.ctx.state.scoop_extract_uncertain = True
        return True, False, state, '로봇 복구 확인. 배치 재개·자세 이동은 수행하지 않았습니다'

    def _do_safe(self, job: Job):
        # 역할: 힘제어 해제를 시도한 뒤 지정 안전 관절 자세로 이동하고 위치·인출 상태를 재설정한다.
        try:
            self.ctx.arm.compliance_off()
        except Exception:  # noqa: BLE001 — 힘제어 중이 아니었으면 무시
            pass
        safe = self.ctx.stations.get('safe')
        posj = safe.extra.get('posj')
        if posj is None:
            raise ValueError('safe station에 posj 6개가 필요하다')
        # MOVEJ · 관절각 목표: posj
        self.ctx.arm.movej_cancellable(posj, 0.3, lambda: job.cancel, self.ctx.config.motion_timeout_s)
        self.ctx.state.station_id = 'safe'
        self.ctx.state.pending_scoop_extract = False
        self.ctx.state.scoop_extract_uncertain = False
        self.ctx.state.held_material_id = ''
        self.ctx.state.empty_scoop_force_baseline = None
        self.ctx.state.empty_scoop_baseline_pending = False
        return True

