"""시험용 가짜 skill_node — 로봇 없이 `process_node` 의 배선을 끝까지 돌려 보기 위한 대역.

A 의 `skill_node` 와 **같은 이름·같은 계약**의 서버 8개를 세우고, 그 안에서 아주 단순한
물리를 흉내 낸다 (스쿱에 담기고, 부으면 일부가 용기로 옮겨 가고, 계량은 그 질량을 돌려준다).
로봇·DSR·그리퍼는 전혀 부르지 않는다.

이것으로 확인하는 것은 **process_node 가 계약대로 부르고 계약대로 해석하는가** 하나다.
힘제어·티칭·실제 분해능은 여기서 검증되지 않는다 — 그건 G1·G4 실측의 몫이다.
"""
import json
import math
import threading
import time

import rclpy
from rclpy.action import ActionServer
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.node import Node

from gmp_interfaces.action import MoveToStation, Pour, ReturnMaterial, Scoop, WeighContainer, WeighHeld
from gmp_interfaces.msg import CellEvent, WeightReading
from gmp_interfaces.srv import MeasureForce, RecoverSafety, SafePose, SetGripper

SCOOP_MASS_G = 45.0        # 빈 스쿱
CUP_MASS_G = 120.0         # 빈 약통
NOMINAL_SCOOP_G = 40.0     # 1회 퍼올림 (dosing.scoop_nominal_g 와 맞춘다)
TRANSFER = 0.95            # 부을 때 실제로 옮겨 가는 비율 — 나머지는 스쿱 잔량이 된다
MIN_DEPTH_FRACTION = 0.15  # dosing.min_fraction 과 맞춘다 (계약 v1.5 유효 범위 하한)


class FakeSkillNode(Node):
    def __init__(self, scoop_of=None):
        super().__init__('fake_skill_node')
        self.cb = ReentrantCallbackGroup()
        self.lock = threading.Lock()
        self.scoop_of = scoop_of or {}          # 스테이션 ID → 원료 ID (스쿱 칸 구분용)
        self.station = ''
        self.held = None                        # 지금 쥔 스쿱 스테이션 ID
        self.content = {}                       # 스쿱 스테이션 ID → 안에 든 원료 [g]
        self.in_cup = 0.0
        self.cup_bias = 0.0                     # 용기 계량에만 더하는 편향 (VERIFY ① 유발용).
                                                # 스쿱에서 빼지 않는다 — TARE 뒤에만 실린다.
        self.calls = []                         # 부른 순서 — 테스트가 본다
        self.safety_revision = 0
        self.fail = {}                          # 스킬 이름 → 앞으로 실패시킬 횟수 (실패 경로 시험용)
        self.empty = 0                          # 앞으로 몇 번 contact_detected=false 로 답할지 (원료 소진 시험용)
        self.delay = {}                         # 스킬 이름 → 응답 전 대기 [s] (인터락 끼어들기 시험용)
        self.transfer = TRANSFER                # 붓기 전달률. **1 을 넘기지 않는다** — 넘기면 스쿱 내용물이
                                                # 음수가 되어 물리적으로 불가능한 잔량이 나온다. VERIFY ① 을
                                                # 유발하려면 `cup_bias` 를 쓴다 (test_process_fsm 의 Cell 과 같은 규약)
                                                #   → 스쿱 계량으로는 안 잡히고 VERIFY ① BATCH_OUT_OF_SPEC 이 잡는다
        self.missing_scoop = False              # 스쿱이 거치대에 없다 — 파지해도 물리지 않는다 (T6(a) 고의 장애)
        self.weigh_invalid = {}                 # 계량 단계 → 앞으로 `valid=false` 로 답할 횟수.
                                                # 'TARE'·'VERIFY'(용기 계량) · 'SCOOP_TARE'·'WEIGH_SCOOP'·
                                                # 'WEIGH_RESIDUAL'(스쿱 계량). {'WEIGH_RESIDUAL': 3} 이면
                                                # 최초 1 + 재시도 2 가 다 무효라 일탈까지 간다.
        self.invalid_std_g = 12.5               # 무효일 때 싣는 σ. 실물은 `scale.py:135` 가
                                                # `valid = valid_src and std_g <= max_std_g`(기본 8.0) 로 판정하므로
                                                # **σ 가 게이트 위여야** 기록이 자기모순이 아니다
                                                # (valid=false 인데 σ 0.4 로 남으면 나중에 기록을 보는 사람이 막힌다)
        self._poured = False                    # 마지막 붓기 이후 다시 퍼지 않았다 = 다음 스쿱 계량은 **잔량**이다.
                                                # 계약상 세 스쿱 계량은 같은 요청이라(`WeighHeld.action`) goal 로는
                                                # 구분되지 않는다. FSM 상태를 흉내 내는 대신 **물리적 사실**로 가른다 —
                                                # 붓고 나면 스쿱에 남은 것이 잔량이라는 건 로봇 밖에서도 참이다.
        self.scoop_gain = 1.0                   # 깊이당 퍼올림 배율. 크게 주면 min_fraction 으로도 남은 양을 넘겨
                                                #   반환만 반복하다 붓기 전에 TIMEOUT 이 난다 (붓기 전 일탈 시험용)
        self.block_rescoop = False              # 실물처럼 반환 뒤 Scoop 을 거부할지 (v1.5.1 · PR #43)
        self._rescoop_blocked = False           #   반환이 세운다. 실물과 같이 풀리지 않는다
        self.cancelled = False                  # safe_pose 가 세운다 — 진행 중 스킬 1건이 실패로 끝난다
        self.attendant = True                   # 세트 끝 NUDGE_WAIT 에서 사람이 건드려 준다 (D-23). 대기 자체를 시험하면 False
        self._attend_stop = threading.Event()
        self.recover_result = (True, False, 1, 'ok')   # recover_safety 응답 — 테스트가 덮어쓴다 (v1.4)

        # 진짜 skill_node 처럼 event 로 알린다 — NUDGE 는 여기로 나간다 (D-21)
        self.pub_event = self.create_publisher(CellEvent, 'event', 100)

        ActionServer(self, MoveToStation, 'move_to_station', self._move, callback_group=self.cb)
        ActionServer(self, Scoop, 'scoop', self._scoop, callback_group=self.cb)
        ActionServer(self, Pour, 'pour', self._pour, callback_group=self.cb)
        ActionServer(self, ReturnMaterial, 'return_material', self._return_material, callback_group=self.cb)
        ActionServer(self, WeighContainer, 'weigh_container', self._weigh_cup, callback_group=self.cb)
        ActionServer(self, WeighHeld, 'weigh_held', self._weigh_held, callback_group=self.cb)
        self.create_service(SetGripper, 'set_gripper', self._set_gripper, callback_group=self.cb)
        self.create_service(MeasureForce, 'measure_force', self._measure, callback_group=self.cb)
        self.create_service(SafePose, 'safe_pose', self._safe, callback_group=self.cb)
        self.create_service(RecoverSafety, 'recover_safety', self._recover, callback_group=self.cb)

    def attend(self, proc):
        """반자동 운전의 사람 — process 가 nudge_wait 에서 기다리면 잠시 뒤 건드린다."""
        def run():
            while not self._attend_stop.is_set():
                if self.attendant and getattr(proc, '_nudge_waiting', False):
                    time.sleep(0.15)
                    if self.attendant and getattr(proc, '_nudge_waiting', False):
                        self.nudge()
                        while getattr(proc, '_nudge_waiting', False) and not self._attend_stop.is_set():
                            time.sleep(0.02)
                time.sleep(0.03)
        threading.Thread(target=run, daemon=True, name='attendant').start()

    def stop_attending(self):
        self._attend_stop.set()

    def nudge(self):
        """사람이 로봇을 툭 건드렸다. 실물에서는 워커가 get_tool_force 폴링으로 낸다 (D-21)."""
        m = CellEvent(level=CellEvent.WARN, code='NUDGE', text='사람 접촉')
        m.header.stamp = self.get_clock().now().to_msg()
        self.pub_event.publish(m)
        with self.lock:
            self.calls.append('nudge')

    def safety_stop(self, reason='vendor alarm', robot_state=5, **correlation):
        """A 가 SAFE_STOP 류를 감지했다고 알린다 (v1.4, docs/interfaces.md 8절)."""
        self.safety_revision += 1
        detail = dict(robot_state=robot_state, reason=reason, origin='robot_alarm',
                      safety_session='fake-skill-session', safety_revision=self.safety_revision)
        detail.update(correlation)
        m = CellEvent(level=CellEvent.ERROR, code='ROBOT_SAFETY_STOP',
                     text=json.dumps(detail, ensure_ascii=False))
        m.header.stamp = self.get_clock().now().to_msg()
        self.pub_event.publish(m)

    def safety_recovery(self, success, manual_required, robot_state=1, message='',
                        request_id='r1', operator_id='op'):
        """A 의 복구 결과를 알린다 (v1.4). 배치 소유자가 아니라 batch_id 는 비운다."""
        self.safety_stop('복구 시작', origin='recovery_request',
                         request_id=request_id, operator_id=operator_id)
        level = CellEvent.INFO if success else CellEvent.WARN
        m = CellEvent(level=level, code='ROBOT_SAFETY_RECOVERY', text=json.dumps(
            {'request_id': request_id, 'operator_id': operator_id, 'success': success,
             'safety_session': 'fake-skill-session', 'safety_revision': self.safety_revision,
             'manual_required': manual_required, 'robot_state': robot_state, 'message': message},
            ensure_ascii=False))
        m.header.stamp = self.get_clock().now().to_msg()
        self.pub_event.publish(m)

    def _hold(self, name: str):
        """이 스킬이 도는 데 걸리는 시간. 스킬 **중간**에 사람이 끼어드는 상황을 만든다.

        calls 에 이름을 적은 **뒤** 재운다 — 테스트가 "이 스킬이 시작됐다"를 보고 끼어들 수 있게.
        """
        d = self.delay.get(name, 0.0)
        if d:
            time.sleep(d)

    def _take_cancel(self) -> bool:
        """skill_node 의 SafePose 처럼 — 취소가 걸렸으면 진행 중 1건이 실패로 끝난다."""
        if not self.cancelled:
            return False
        self.cancelled = False
        return True

    def _fails(self, name: str) -> bool:
        left = self.fail.get(name, 0)
        if left <= 0:
            return False
        self.fail[name] = left - 1
        return True

    def _takes_invalid(self, stage: str) -> bool:
        """`weigh_invalid` 에서 한 번 꺼내 쓴다 — `_fails` 와 같은 규약(소진되면 정상으로 돌아온다)."""
        with self.lock:
            left = self.weigh_invalid.get(stage, 0)
            if left <= 0:
                return False
            self.weigh_invalid[stage] = left - 1
            return True

    def _reading(self, gross: float, tare: float, subject: str, station: str = 'workbench',
                 stage: str = '') -> WeightReading:
        bad = self._takes_invalid(stage)
        m = WeightReading(gross_g=float(gross), tare_g=float(tare), net_g=float(gross - tare),
                          std_g=self.invalid_std_g if bad else 0.4, samples=20,
                          valid=not bad, station=station, subject=subject)
        m.header.stamp = self.get_clock().now().to_msg()
        return m

    # ── Action ───────────────────────────────────────────────────────
    def _move(self, gh):
        with self.lock:
            self.calls.append(f'move:{gh.request.station_id}:{gh.request.approach}')
        self._hold('move')
        with self.lock:
            if self._take_cancel():
                gh.abort()
                return MoveToStation.Result(success=False, message='cancelled by safe_pose')
            if self._fails('move'):
                gh.abort()
                return MoveToStation.Result(success=False, message='경로 실패')
            self.station = gh.request.station_id
        gh.succeed()
        return MoveToStation.Result(success=True, reached=gh.request.station_id)

    def _scoop(self, gh):
        with self.lock:
            self.calls.append(f'scoop:{gh.request.material_id}:{gh.request.attempt}'
                              f':{gh.request.depth_fraction:.3f}')
        depth = float(gh.request.depth_fraction)
        # 계약 v1.5 — 범위 밖 깊이는 이동 전에 거부한다. 실제 Z 변환은 A 가 실물 뒤 확정한다.
        if not math.isfinite(depth) or not MIN_DEPTH_FRACTION <= depth <= 1.0:
            gh.abort()
            return Scoop.Result(success=False, message=f'담그기 깊이 비율 범위 밖: {depth}')
        # v1.5.1 (PR #43) — 실물은 반환 중 `_return_rescoop_blocked` 를 세우고 이후 Scoop 을 전부
        # 거부한다. 플래그는 풀리지 않는다(연결 경로 미구현, #64). 같은 문구로 흉내 낸다.
        with self.lock:
            blocked = self._rescoop_blocked
        if blocked:
            gh.abort()
            return Scoop.Result(success=False,
                                message='반환 후 재스쿱 연결 경로 미구현: 자동 Scoop을 차단합니다')
        self._hold('scoop')
        with self.lock:
            if self._take_cancel():
                gh.abort()
                return Scoop.Result(success=False, message='cancelled by safe_pose')
            if self._fails('scoop'):
                gh.abort()
                return Scoop.Result(success=False, message='담그기 중 힘 상한')
            if self.empty > 0:
                self.empty -= 1
                gh.succeed()
                return Scoop.Result(success=True, contact_detected=False)
            self._poured = False         # 다시 펐으니 다음 스쿱 계량은 **붓기 전**이다
            if self.held:
                # 깊이 비율만큼 퍼올린다 — 계약 v1.5 의 depth_fraction 이 실제로 쓰이는 지점
                self.content[self.held] = (self.content.get(self.held, 0.0)
                                           + NOMINAL_SCOOP_G * depth * self.scoop_gain)
        gh.succeed()
        return Scoop.Result(success=True, contact_detected=True, max_contact_force_n=7.2,
                            insertion_depth_mm=21.0)

    def _pour(self, gh):
        f = float(gh.request.fraction)
        with self.lock:
            self.calls.append(f'pour:{f:.3f}')
        self._hold('pour')
        with self.lock:
            if self._take_cancel():
                gh.abort()
                return Pour.Result(success=False, message='cancelled by safe_pose')
            if self._fails('pour'):
                gh.abort()
                return Pour.Result(success=False, message='기울임 중 힘 상한')
            have = self.content.get(self.held, 0.0)
            moved = min(have, max(0.0, have * f * self.transfer))   # 스쿱에 있는 것보다 많이 못 붓는다
            self.content[self.held] = have - moved
            self.in_cup += moved
            self._poured = True          # 이후의 스쿱 계량은 **잔량**이다 (재계량으로 여러 번 와도 그렇다)
        gh.succeed()
        return Pour.Result(success=True)

    def _return_material(self, gh):
        with self.lock:
            self.calls.append(f'return_material:{gh.request.material_id}')
            if self._fails('return_material'):
                gh.abort()
                return ReturnMaterial.Result(success=False, message='원료통 반환 경로 실패')
            if self.block_rescoop:
                self._rescoop_blocked = True    # 실물처럼 기울인 자세에서 세우고 풀지 않는다
            if self.held:
                self.content[self.held] = 0.0
        gh.succeed()
        return ReturnMaterial.Result(success=True)

    def _weigh_cup(self, gh):
        with self.lock:
            self.calls.append('weigh_container')
        self._hold('weigh_container')
        with self.lock:
            gross = CUP_MASS_G + self.in_cup + (self.cup_bias if self.in_cup > 0 else 0.0)
        gh.succeed()
        stage = 'TARE' if gh.request.tare_g == 0.0 else 'VERIFY'
        return WeighContainer.Result(success=True,
                                     reading=self._reading(gross, gh.request.tare_g, 'container', stage=stage))

    def _weigh_held(self, gh):
        with self.lock:
            self.calls.append('weigh_held')
        self._hold('weigh_held')
        with self.lock:
            if self.held is None:                     # 계약: 빈 그리퍼면 success=false
                gh.abort()
                return WeighHeld.Result(success=False, message='그리퍼가 비어 있다')
            gross = SCOOP_MASS_G + self.content.get(self.held, 0.0)
            suffix = self.held.rsplit('_', 1)[-1]
            station = f'material_{suffix}'
            poured = self._poured
        gh.succeed()
        stage = 'SCOOP_TARE' if gh.request.tare_g == 0.0 else ('WEIGH_RESIDUAL' if poured else 'WEIGH_SCOOP')
        return WeighHeld.Result(success=True,
                                reading=self._reading(gross, gh.request.tare_g, 'scoop', station, stage=stage))

    # ── Service ──────────────────────────────────────────────────────
    def _set_gripper(self, req, res):
        with self.lock:
            self.calls.append(f'grip:{"close" if req.close else "open"}:{req.width_mm:.0f}')
        self._hold('set_gripper')
        with self.lock:
            if req.close:
                if self.missing_scoop and self.station.startswith('scoop'):
                    # 거치대가 비어 있어 손가락이 끝까지 닫힌다 — 실물은 grip 비트가 안 선다 (T6(a))
                    res.final_width_mm = 0.0
                    res.grip_inferred = False
                    res.success = True
                    return res
                if self.station.startswith('scoop'):
                    self.held = self.station
                res.final_width_mm = req.width_mm
                res.grip_inferred = True
            else:
                if self.held and self.station == self.held:
                    self.held = None
                res.final_width_mm = 100.0
                res.grip_inferred = False
        res.success = True
        return res

    def _measure(self, req, res):
        with self.lock:
            self.calls.append('measure_force')
        res.force = [0.0, 0.0, -13.0, 0.0, 0.0, 0.0]
        res.fz_mean_n, res.fz_std_n, res.valid = -13.0, 0.03, True
        return res

    def _safe(self, req, res):
        with self.lock:
            self.calls.append(f'safe:{req.reason}')
            self.cancelled = True        # 진행 중 스킬 1건을 실패로 끝낸다 (skill_node 와 같은 규칙)
        res.success = True
        return res

    def _recover(self, req, res):
        with self.lock:
            self.calls.append(f'recover:{req.request_id}')
        res.success, res.manual_required, res.robot_state, res.message = self.recover_result
        return res
