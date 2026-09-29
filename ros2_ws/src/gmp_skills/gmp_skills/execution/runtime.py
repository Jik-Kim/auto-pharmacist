"""ROS 요청을 작업 큐에 넣고 단일 워커에서 순서대로 실행한다.

ROS 콜백은 _submit()에서 Job 완료를 기다리고, _worker()가 handlers를 통해
해당 실행 객체를 부른다. DSR_ROBOT2는 자체 ROS 노드를 기다리며 호출하므로
로봇 장치 메서드는 이 워커 한 곳에서만 호출한다.
"""
import math
import queue
import time

from .context import Job
from gmp_skills.core.recovery import STANDBY


class SkillRuntime:
    def __init__(self, ctx):
        # ROS 노드 대신 장치와 현재 상태가 들어 있는 ExecutionContext를 공유한다.
        self.ctx = ctx
        self.handlers = {}

    def configure(self, *, safety, motion, scooping, weighing):
        """Job.kind별 담당 메서드를 등록한다. 워커는 이 표로 실행 대상을 찾는다."""
        self.safety, self.motion = safety, motion
        self.handlers = {
            'startup': self._do_startup,
            'recover': safety._do_recover,
            'safe': safety._do_safe,
            'restore_grip': safety._do_restore_grip,
            'move': motion._do_move,
            'grip': motion._do_grip,
            'scoop': scooping._do_scoop,
            'pour': scooping._do_pour,
            'return_material': scooping._do_return_material,
            'weigh': weighing._do_weigh,
            'weigh_held': weighing._do_weigh_held,
            'measure': weighing._do_measure,
        }

    def _cancel_requested(self):
        # 장치 이동 대기 중 반복 호출된다. 종료·안전 차단·Job 취소가 있으면
        # 참을 반환해 어댑터가 이동 정지를 요청하게 한다.
        if self.ctx.state.current and self.ctx.state.current.kind not in ('startup', 'recover'):
            self.safety._poll_safety()
        return (self.ctx.state.stopping.is_set() or bool(self.ctx.state.current and self.ctx.state.current.cancel)
                or (self.ctx.state.safety_latched and bool(self.ctx.state.current)
                    and self.ctx.state.current.kind not in ('startup', 'recover')))

    def _drain_jobs_locked(self, reason):
        # 호출자가 job_lock을 잡은 상태에서 대기 요청을 모두 실패 처리한다.
        # done을 세워야 _submit()에서 기다리는 ROS 콜백이 깨어난다.
        while True:
            try:
                job = self.ctx.state.q.get_nowait()
            except queue.Empty:
                return
            job.cancel, job.error = True, reason
            job.done.set()

    def shutdown(self):
        """현재 Job을 취소하고 대기 Job을 깨운 뒤 워커 종료를 기다린다.

        로봇 정지·힘제어 해제는 워커의 finally에서 실행한다. 정해진 시간 안에
        워커가 끝나지 않으면 정상 종료로 보고하지 않는다.
        """
        with self.ctx.state.job_lock:
            self.ctx.state.stopping.set()
            if self.ctx.state.current:
                self.ctx.state.current.cancel = True
            self._drain_jobs_locked('skill_node shutdown')
        self.ctx.state.worker_thread.join(self.ctx.config.shutdown_timeout_s)
        stopped = self.ctx.state.worker_stopped.is_set()
        if not stopped:
            self.ctx.state.cleanup_error = '워커 종료 시간 초과: 정지·힘제어 해제 확인 불가'
        if self.ctx.state.cleanup_error:
            self.ctx.logger().error(self.ctx.state.cleanup_error)
        return stopped and not self.ctx.state.cleanup_error

    def _submit(self, kind: str, feedback=None, **args) -> Job:
        # ROS 콜백은 여기서 요청을 Job으로 포장한다. 종료/안전 차단/자가진단 미완료면
        # 즉시 오류를 돌려주고, 허용된 요청은 큐에 넣어 워커가 done을 세울 때까지 기다린다.
        job = Job(kind, args, feedback=feedback)
        with self.ctx.state.job_lock:
            if self.ctx.state.stopping.is_set() or self.ctx.state.worker_stopped.is_set():
                job.cancel, job.error = True, 'skill_node shutdown'
                job.done.set()
                return job
            if self.ctx.state.safety_latched and kind != 'recover':
                job.error = f'SAFETY_STOP: {self.ctx.state.safety_reason}'
                job.done.set()
                return job
            if not self.ctx.state.ready:
                job.error = '기동 자가진단 대기 중'
                job.done.set()
                return job
            self.ctx.state.q.put(job)
        job.done.wait()
        return job

    def _worker(self):
        # 로봇 명령을 실행하는 유일한 스레드다. 큐가 비면 로봇 안전 상태,
        # DIO 그리퍼 입력, 외력 넛지를 확인한다. Job마다 결과나 오류를 저장하고
        # done을 세워 기다리는 ROS 콜백에 완료를 알린다.
        try:
            while self.ctx.ok() and not self.ctx.state.stopping.is_set():
                try:
                    job = self.ctx.state.q.get(timeout=0.1)
                except queue.Empty:
                    self.safety._poll_safety()
                    if getattr(self.ctx.state, 'configured', False) and getattr(getattr(self.ctx, 'gripper', None), 'backend', '') == 'dio':
                        try:
                            self.ctx.gripper.refresh_dio()
                        except Exception as exc:
                            self.safety._latch_safety(f'그리퍼 DI 조회 실패: {exc}')
                    self.safety._poll_nudge()
                    continue
                with self.ctx.state.job_lock:
                    if job.kind not in ('safe', 'restore_grip'):
                        self.ctx.state.resume_grip = None
                        self.ctx.state.resume_grip_ready = False
                    self.ctx.state.current = job
                    if self.ctx.state.stopping.is_set():
                        job.cancel = True
                try:
                    if job.kind not in ('startup', 'recover'):
                        self.safety._poll_safety(force=True)
                    if job.cancel or (self.ctx.state.safety_latched and job.kind not in ('startup', 'recover')):
                        raise RuntimeError(f'SAFETY_STOP: {self.ctx.state.safety_reason}' if self.ctx.state.safety_latched else 'cancelled')
                    if job.kind not in ('move', 'grip', 'return_material'):
                        self.ctx.state.returned_material = ''
                        self.ctx.state.returned_scoop_stowed = ''
                    if job.kind in ('scoop', 'pour', 'return_material', 'weigh_held', 'safe'):
                        self.ctx.state.motion_anchor = None
                    if job.kind == 'weigh':
                        self.ctx.state.held_payload = 'unknown'
                        self.ctx.state.held_material_id = ''
                        self.ctx.state.empty_scoop_force_baseline = None
                        self.ctx.state.empty_scoop_baseline_pending = False
                    # configure()의 표에서 요청 종류에 해당하는 실행 메서드를 찾는다.
                    job.result = self.handlers[job.kind](job)
                except Exception as e:  # noqa: BLE001
                    if not job.cancel and job.kind != 'restore_grip':
                        self.ctx.state.resume_grip = None
                        self.ctx.state.resume_grip_ready = False
                    self.ctx.state.motion_anchor = None
                    self.ctx.state.held_payload = 'unknown'
                    self.ctx.state.held_material_id = ''
                    self.ctx.state.empty_scoop_force_baseline = None
                    self.ctx.state.empty_scoop_baseline_pending = False
                    self.ctx.state.returned_material = ''
                    self.ctx.state.returned_scoop_stowed = ''
                    job.error = f'{type(e).__name__}: {e}'
                    if not self.ctx.state.stopping.is_set():
                        if isinstance(e, TimeoutError):
                            self.safety._latch_safety(f'{job.kind} 응답 시간 초과: {e}')
                        self.ctx.event('ERROR', f'{job.kind.upper()}_FAIL', job.error)
                finally:
                    with self.ctx.state.job_lock:
                        if job.cancel:
                            self.ctx.state.returned_material = ''
                            self.ctx.state.returned_scoop_stowed = ''
                            self.ctx.state.motion_anchor = None
                            self.ctx.state.held_payload = 'unknown'
                            self.ctx.state.held_material_id = ''
                            self.ctx.state.empty_scoop_force_baseline = None
                            self.ctx.state.empty_scoop_baseline_pending = False
                        self.ctx.state.current = None
                        job.done.set()
        finally:
            # 워커가 끝날 때 로봇 이동 정지와 힘/순응 제어 해제를 각각 시도한다.
            # 한쪽이 실패해도 다른 쪽을 시도하고 실패 내용은 cleanup_error에 남긴다.
            errors = []
            for name in ('stop_motion', 'compliance_off'):
                try:
                    getattr(self.ctx.arm, name)()
                except Exception as exc:  # noqa: BLE001
                    errors.append(f'{name}: {exc}')
            with self.ctx.state.job_lock:
                self.ctx.state.stopping.set()
                self._drain_jobs_locked('skill worker stopped')
                self.ctx.state.cleanup_error = '; '.join(errors)
                self.ctx.state.worker_stopped.set()

    def check_startup(self):
        # 기동 Job이 끝났을 때 장치 초기화·자가진단 성공 여부를 확인한다.
        # 실패했으면 ready를 세우지 않아 일반 스킬 요청을 받지 않는다.
        if self.ctx.state.ready or not self.ctx.state.startup_job.done.is_set():
            return
        job = self.ctx.state.startup_job
        if job.cancel or job.error or not job.result or not job.result[0]:
            raise RuntimeError(f'자가진단 실패 — 기동 거부: {job.error or job.result}')
        self.ctx.state.ready = True
        self.ctx.event('INFO', 'SELF_CHECK', f'OK {job.result[1]}')

    def _do_startup(self, job: Job):
        # DSR 컨트롤러·그리퍼를 준비하고 등록 툴, TCP, 충돌 감도를 점검한다.
        # 작업자가 요청한 경우에만 이미 인출된 스쿱 상태를 검증해 복원한다.
        restore_requested = any(job.args.get(k) for k in
                                ('restore_material_id', 'restore_operator_id', 'restore_confirmed'))
        self.safety._poll_safety(force=True)
        if self.ctx.state.safety_latched:
            if restore_requested:
                raise RuntimeError('안전 차단 중에는 파지 이력을 복원할 수 없습니다')
            return True, '안전 복구 필요 — 일반 동작 차단'
        self.ctx.logger().info('[STARTUP] initialize 시작')
        self.ctx.arm.initialize()
        self.ctx.logger().info('[STARTUP] tool/TCP/충돌 감도 self_check 시작')
        result = self.ctx.arm.self_check(job.args['expect_tool'], job.args['expect_tcp'],
                                     self.ctx.parameter('safety.collision_sensitivity').value)
        if result[0] and restore_requested:
            self._restore_extracted_scoop(job)
        if result[0] and getattr(self.ctx.gripper, 'backend', '') == 'dio':
            if self.ctx.gripper.confirm_open_dio():
                self.ctx.state.held_payload = 'empty'
        self.ctx.state.configured = bool(result[0])
        return result

    def _restore_extracted_scoop(self, job: Job):
        """재기동 후 들고 있는 스쿱의 내부 상태만 다시 기록한다.

        작업자 확인, 로봇 대기 상태, 해당 material_N 계량 자세, 최신 Modbus
        파지·폭·안전 입력이 모두 맞아야 한다. 로봇 이동이나 그리퍼 명령은 없다.
        """
        self.ctx.state.empty_scoop_force_baseline = None
        self.ctx.state.empty_scoop_baseline_pending = False
        material_id = job.args.get('restore_material_id', '')
        operator_id = job.args.get('restore_operator_id', '')
        if (not isinstance(material_id, str) or not material_id.strip()
                or not isinstance(operator_id, str) or not operator_id.strip()
                or job.args.get('restore_confirmed') is not True):
            raise ValueError('스쿱 복원에는 원료 ID·작업자 ID·인출 완료 확인이 필요합니다')
        if self.ctx.config.mode != 'real' or self.ctx.gripper.backend != 'modbus':
            raise RuntimeError('스쿱 복원은 실물 Modbus 파지 센서가 필요합니다')
        station = self.ctx.stations.for_material(material_id)
        revision = self.ctx.state.safety_revision
        if getattr(self.ctx.state, 'return_rescoop_blocked', False):
            raise RuntimeError('반환 후 재스쿱 차단은 파지 복원으로 해제할 수 없습니다')
        self.safety._poll_safety(force=True)
        if self.ctx.state.last_robot_state != STANDBY or self.ctx.state.safety_latched:
            raise RuntimeError('스쿱 복원은 안전 차단 없는 대기 상태에서만 가능합니다')
        if not self.motion._pose_matches(self.ctx.arm.current_posx(), station.posx):
            raise RuntimeError('현재 자세가 해당 원료 계량 자세와 다릅니다. 파지 복원 거부')
        # 재기동 직후 Modbus 센서의 첫 최신 표본이 올 때까지만 기다린다.
        # 표본이 도착하면 아래에서 파지·폭·안전 상태를 검사한다.
        timeout_s = float(self.ctx.parameter('robot.startup_timeout_s').value)
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError('스쿱 복원 센서 대기 시간은 유한한 양수여야 합니다')
        deadline = self.ctx.now() + timeout_s
        while True:
            if (job.cancel or self.ctx.state.stopping.is_set() or self.ctx.state.safety_latched
                    or self.ctx.state.safety_revision != revision):
                raise RuntimeError('센서 대기 중 취소 또는 안전 상태 변경 발생')
            state = self.ctx.gripper.state(self.ctx.now())
            if state.get('fresh', False):
                break
            remaining = deadline - self.ctx.now()
            if remaining <= 0:
                raise TimeoutError(f'스쿱 복원 센서 대기 {timeout_s:g}s 초과: {state}')
            time.sleep(min(self.ctx.config.state_poll_s, remaining))
        # 센서를 기다리는 동안 위치·로봇 상태가 바뀌었을 수 있어 다시 확인한다.
        self.safety._poll_safety(force=True)
        if self.ctx.state.last_robot_state != STANDBY or not self.motion._pose_matches(self.ctx.arm.current_posx(), station.posx):
            raise RuntimeError('센서 대기 후 로봇 상태 또는 계량 자세 불일치')
        state = self.ctx.gripper.state(self.ctx.now())
        width = state.get('width_mm')
        if (not state.get('fresh', False) or state.get('busy', True) or not state.get('grip_inferred', False)
                or state.get('safety_triggered', True) or state.get('slip', False)
                or width is None or not math.isfinite(float(width)) or float(width) <= 0):
            raise RuntimeError(f'최신 파지·폭·안전 상태를 확인할 수 없어 복원을 거부합니다: {state}')
        with self.ctx.state.job_lock:
            if (job.cancel or self.ctx.state.stopping.is_set() or self.ctx.state.safety_latched
                    or self.ctx.state.safety_revision != revision):
                raise RuntimeError('복원 확인 중 취소 또는 안전 상태 변경 발생')
            self.ctx.state.held_payload = 'scoop'
            self.ctx.state.empty_scoop_baseline_pending = job.args.get('restore_empty_scoop_confirmed') is True
            self.ctx.state.held_material_id = material_id
            self.ctx.state.station_id = station.station_id
            self.ctx.state.motion_anchor = None  # 복원된 위치를 티칭 이송의 출발 검증 기록으로 쓰지 않는다.
            self.ctx.state.pending_scoop_extract = False
            self.ctx.state.scoop_extract_uncertain = False
        self.ctx.logger().info(
            f'[SCOOP_STATE_RESTORED] operator={operator_id} material={material_id} '
            f'station={station.station_id} width_mm={width}; 공정 자동 재개 없음')
