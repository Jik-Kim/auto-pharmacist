"""공정 상태기계 — 순수 Python. 스킬 호출 대신 「요청」을 돌려주고, 결과를 이벤트로 받는다.

사용 (process_node):
    fsm = ProcessFSM(spec, dosing_cfg, weight_model)
    req = fsm.start()                         # 첫 요청
    while req:
        result = <req 를 스킬로 실행>          # nodes 가 한다
        req = fsm.on_result(req, result)      # 다음 요청 또는 None(끝)
    fsm.state, fsm.mode, fsm.deviations, fsm.results 를 발행

요청은 dict(kind=..., ...) 하나.
kind: move | grip | carry | scoop | pour | weigh | weigh_scoop | measure | safe | wait_qa | wait_interlock
  carry       용기 반송(src 슬롯 → dst) 복합 요청. process_node 가 MoveToStation(ABOVE→AT) → SetGripper(close, cup) → ABOVE →
              dst(ABOVE→AT) → SetGripper(open) → ABOVE 로 조합한다. 결과 grip_inferred=false 면 GRIP_FAIL (D-18).
  weigh       용기를 들어 계량 (WeighContainer: 파지 → 계량 자세 → 읽기 → 내려놓기). 그리퍼가 비어 있어야 한다.
  weigh_scoop 지금 들고 있는 스쿱을 계량 자세로 가져가 그대로 잰다 (파지·내려놓기 없음). 결과 gross_g·valid.
              계약 v1.2 항목 — A 와 합의 (docs/issues.md I-007).

스쿱은 원료통 아래에 원료별로 둔다 (9/18 확정 — scoop_rack 폐지). FSM 은 station='scoop' + material_id 만 넘기고,
실제 스테이션(stations.yaml 의 scoop_N)은 process_node 가 material_id 로 찾는다.

원료 1종의 흐름 (SOT D-22, 9/17 팀 합의 — 로봇이 저울이므로 스쿱을 든 채 재는 것이 가장 싸다):
  PICK_SCOOP → SCOOP_TARE(빈 스쿱 무게) → SCOOP → WEIGH_SCOOP(붓기 전: 퍼낸 양 → 붓기 비율 = 1차 폐루프)
  → POUR → WEIGH_RESIDUAL(붓기 후: 스쿱 잔량 → 실제 투입량 누적 → decide) → RETURN_SCOOP
원료가 다 끝나면 VERIFY(용기를 들어 계량) → FINISH → NUDGE_WAIT(nudge_wait 로 물러나 NUDGE 대기, D-23) → DONE. VERIFY 는 두 가지를 본다 (9/17 조장 합의):
  ① 제품 판정   |net − Σtarget| > Σ(target×tol)     → BATCH_OUT_OF_SPEC (규격 이탈)
  ② 계측 신뢰성 |net − Σ투입량| > min_resolvable_g  → VERIFY_MISMATCH   (스쿱 계량을 못 믿는다)
②만으로는 개별 원료가 전부 같은 방향으로 치우친 경우를 못 잡는다 — 두 값이 함께 낮아 서로 일치하기 때문이다.
상태 이름은 CellState.step 에 그대로 실린다 (docs/architecture.md 전이표).
"""
from dataclasses import dataclass, field

from gmp_dosing.core.dosing import decide
from gmp_process.core.deviation import policy


@dataclass
class ItemRun:
    material_id: str
    target_g: float
    tol_pct: float
    attempts: int = 0            # 붓기까지 간 횟수 — ScoopCycle.attempt 는 process_node 가 따로 센다
    returns: int = 0             # 원료통 반환 횟수. 붓지 않았으므로 attempts 에 넣지 않는다
    last_fraction: float = 1.0   # 마지막으로 요청한 담그기 깊이 — 반환 뒤 보정의 기준
    invalid: int = 0
    scoop_tare_g: float = 0.0    # 빈 스쿱 (SCOOP_TARE)
    scooped_g: float = 0.0       # 붓기 전 스쿱 안의 원료 (WEIGH_SCOOP)
    residual_g: float = 0.0      # 붓기 후 스쿱에 남은 원료 (WEIGH_RESIDUAL)
    actual_g: float = 0.0        # 용기에 들어간 누적 투입량 = Σ(scooped − residual)
    verdict: str = ''


@dataclass
class ProcessFSM:
    spec: object                 # RecipeSpec
    dosing_cfg: object           # DosingConfig
    scale: object                # WeightModel
    state: str = 'IDLE'
    mode: str = 'IDLE'
    idx: int = 0
    tare_g: float = 0.0          # 빈 용기 (TARE)
    verify_net_g: float = 0.0    # VERIFY 에서 잰 용기 순량
    results: list = field(default_factory=list)
    deviations: list = field(default_factory=list)
    _counts: dict = field(default_factory=dict)
    _resume: object = None       # 인터락/QA 후 돌아갈 요청
    _qa_step: str = ''           # QA 판정을 기다리는 일탈이 난 스텝 — APPROVED/DISCARDED 뒤 경로를 가른다
    _verify_invalid: int = 0
    _final: str = 'DONE'         # NUDGE_WAIT 뒤 끝나는 상태 — DONE(완성품) | DISCARDED(폐기)
    slot: int = 0                # 매거진·트레이 슬롯 (process_node 가 배치마다 올린다)

    # ── 진입 ─────────────────────────────────────────────────────────
    def start(self):
        self.state, self.mode = 'SELF_CHECK', 'RUNNING'
        return {'kind': 'measure'}                      # 빈 그리퍼 외력 — 자가진단 겸 영점 후보

    def _item(self) -> ItemRun:
        it = self.spec.items[self.idx]
        return ItemRun(it.material_id, it.target_g, it.tol_pct)

    # ── 요청 생성 ─────────────────────────────────────────────────────
    def _weigh_scoop(self) -> dict:
        # 스쿱 계량은 원료통 위의 해당 material_N 자세에서 수행한다. 실제 위치 선택은
        # held scoop material context 를 아는 skill_node 가 하며, 이 값은 기록·검증용이다.
        return {'kind': 'weigh_scoop', 'station': 'material', 'material_id': self.cur.material_id,
                'tare_g': self.cur.scoop_tare_g}

    def _weigh_cup(self, tare_g: float) -> dict:
        return {'kind': 'weigh', 'station': 'workbench', 'tare_g': tare_g}

    def _scoop(self, fraction: float = 1.0, *, after_return: bool = False) -> dict:
        # attempts 는 '붓기까지 간 횟수' 다. 반환은 약통에 아무것도 넣지 않았으므로 같은 시도의
        # 연장으로 보고 번호를 올리지 않는다 — 올리면 반환 한 번이 붓기 기회 하나를 먹는다.
        if not after_return:
            self.cur.attempts += 1
        self.cur.last_fraction = fraction
        return {'kind': 'scoop', 'material_id': self.cur.material_id, 'attempt': self.cur.attempts,
                'fraction': fraction}                 # 담그기 깊이 힌트일 뿐 — 붓기 비율은 WEIGH_SCOOP 가 정한다

    def _return_material(self) -> dict:
        """초과 스쿱을 약통에 붓지 않고 원래 원료통으로 되돌린다."""
        return {'kind': 'return_material', 'material_id': self.cur.material_id,
                'attempt': self.cur.attempts, 'scooped_g': self.cur.scooped_g}

    def _rescoop_fraction(self) -> float:
        """반환 뒤 다시 풀 깊이. 비율 자체가 아니라 **직전 깊이에 대한 보정**이다 —
        이미 얕게 펐는데 또 초과했다면 그 얕은 깊이에서 더 줄여야 수렴한다.
        """
        if self.cur.scooped_g <= 0.0:
            return self.dosing_cfg.min_fraction
        remaining = max(0.0, self.cur.target_g - self.cur.actual_g)
        ratio = remaining / self.cur.scooped_g
        return max(self.dosing_cfg.min_fraction, min(1.0, self.cur.last_fraction * ratio))

    def _scoop_allowance_g(self) -> float:
        """남은 목표량에 허용하는 스쿱량 여유. 원래 목표량 기준의 절대 허용오차다."""
        return self.cur.target_g * self.cur.tol_pct / 100.0

    def _carry(self, src: str, dst: str) -> dict:
        return {'kind': 'carry', 'src': src, 'dst': dst, 'slot': self.slot, 'target': 'cup'}

    def _park(self, final: str) -> dict:
        """세트 끝 — nudge_wait 로 이동해 NUDGE 를 기다린다. final 은 그 뒤의 종료 상태."""
        self._final = final
        self.state, self.mode = 'NUDGE_WAIT', 'RUNNING'
        return {'kind': 'move', 'station': 'nudge_wait', 'approach': 'AT'}

    def _invalid_or(self, res: dict, step: str, retry: dict):
        """계량 무효(valid=false)면 재계량, 상한을 넘으면 WEIGH_INVALID → QA. 유효하면 None."""
        if res.get('valid', False):
            return None
        self.cur.invalid += 1
        if self.cur.invalid >= self.dosing_cfg.max_invalid:
            return self._deviate('WEIGH_INVALID', step)
        return retry

    # ── 전이 ─────────────────────────────────────────────────────────
    def on_result(self, req: dict, res: dict):
        k, st = req['kind'], self.state
        # 종료·대기 전이 — 요청에 then 이 명시된 경우가 우선
        if 'then' in req and k in ('safe', 'move', 'carry'):
            nxt = req['then']
            if nxt is None:
                return None                      # ERROR 종료
            if nxt == 'wait_interlock':
                return {'kind': 'wait_interlock'}
        if k == 'measure' and st == 'SELF_CHECK':
            self.state = 'PICK_CONTAINER'
            return self._carry('passbox_empty', 'workbench')
        if k == 'carry' and st == 'PICK_CONTAINER':
            if not res.get('grip_inferred', False):
                return self._deviate('GRIP_FAIL', 'PICK_CONTAINER', retry=req)
            self.state = 'TARE'
            return self._weigh_cup(0.0)
        if k == 'weigh' and st == 'TARE':
            self.tare_g = res.get('gross_g', 0.0)
            self.scale.set_tare(self.tare_g)
            self.cur = self._item()
            self.state = 'PICK_SCOOP'
            return {'kind': 'move', 'station': 'scoop', 'material_id': self.cur.material_id, 'approach': 'AT'}
        if k == 'move' and st == 'PICK_SCOOP':
            return {'kind': 'grip', 'close': True, 'target': 'scoop'}
        if k == 'grip' and st == 'PICK_SCOOP':
            if not res.get('grip_inferred', False):
                return self._deviate('GRIP_FAIL', 'PICK_SCOOP', retry={'kind': 'grip', 'close': True, 'target': 'scoop'})
            self.state = 'SCOOP_TARE'
            return self._weigh_scoop()                 # 빈 스쿱 무게 — 원료마다 1회
        if k == 'weigh_scoop' and st == 'SCOOP_TARE':
            r = self._invalid_or(res, 'SCOOP_TARE', req)
            if r is not None:
                return r
            self.cur.scoop_tare_g = res.get('gross_g', 0.0)
            self.state = 'SCOOP'
            return self._scoop()
        if k == 'scoop' and st == 'SCOOP':
            if not res.get('contact_detected', True):
                return self._deviate('SCOOP_EMPTY', 'SCOOP', retry=req)
            self.state = 'WEIGH_SCOOP'
            return self._weigh_scoop()                 # 붓기 전 — 퍼낸 양
        if k == 'weigh_scoop' and st == 'WEIGH_SCOOP':
            r = self._invalid_or(res, 'WEIGH_SCOOP', req)
            if r is not None:
                return r
            self.cur.scooped_g = max(0.0, res.get('gross_g', 0.0) - self.cur.scoop_tare_g)
            remaining = max(0.0, self.cur.target_g - self.cur.actual_g)
            # 스쿱량이 남은 목표량과 절대 허용오차의 합보다 크면 부분 투입으로 맞추지 않는다.
            # 원료통에 되돌린 뒤 다시 스쿱해야 실제 투입량과 반환량이 섞이지 않는다.
            if self.cur.scooped_g > remaining + self._scoop_allowance_g():
                self.state = 'RETURN_MATERIAL'
                return self._return_material()
            self.state = 'POUR'
            return {'kind': 'pour', 'station': 'workbench', 'fraction': 1.0}
        if k == 'return_material' and st == 'RETURN_MATERIAL':
            if not res.get('success', False):
                return self._return_failed(res.get('message', '원료통 반환 실패'))
            # 반환이 끝난 스쿱만 다시 쓸 수 있다. 마지막 허용 시도도 일단 반환해 원료와
            # 약통 투입량을 분리한 뒤 TIMEOUT 일탈로 멈춘다.
            self.cur.returns += 1
            if self.cur.returns >= self.dosing_cfg.max_attempts:
                return self._deviate('TIMEOUT', 'RETURN_MATERIAL')
            self.state = 'SCOOP'
            # 같은 깊이로 다시 푸면 초과가 그대로 재현된다. 직전 깊이를 남은 목표량과
            # 실제 퍼올린 양의 비로 줄여서 다시 푼다 (min_fraction 하한 유지).
            return self._scoop(self._rescoop_fraction(), after_return=True)
        if k == 'pour' and st == 'POUR':
            self.state = 'WEIGH_RESIDUAL'
            return self._weigh_scoop()                 # 붓기 후 — 스쿱 잔량
        if k == 'weigh_scoop' and st == 'WEIGH_RESIDUAL':
            r = self._invalid_or(res, 'WEIGH_RESIDUAL', req)
            if r is not None:
                return r
            self.cur.residual_g = max(0.0, res.get('gross_g', 0.0) - self.cur.scoop_tare_g)
            self.cur.actual_g += max(0.0, self.cur.scooped_g - self.cur.residual_g)
            d = decide(self.cur.target_g, self.cur.actual_g, self.cur.tol_pct, self.cur.attempts,
                       True, self.cur.invalid, self.dosing_cfg)
            self.cur.verdict = d.verdict
            if d.action == 'DONE':
                self.results.append(self.cur)
                self.state = 'RETURN_SCOOP'
                return {'kind': 'move', 'station': 'scoop', 'material_id': self.cur.material_id, 'approach': 'AT'}
            if d.action == 'SCOOP':
                self.state = 'SCOOP'
                return self._scoop(d.fraction)
            return self._deviate(d.kind, 'WEIGH_RESIDUAL')
        if k == 'move' and st == 'RETURN_SCOOP':
            return {'kind': 'grip', 'close': False}
        if k == 'grip' and st == 'RETURN_SCOOP':
            self.idx += 1
            if self.idx < len(self.spec.items):
                self.cur, self.state = self._item(), 'PICK_SCOOP'
                return {'kind': 'move', 'station': 'scoop', 'material_id': self.cur.material_id, 'approach': 'AT'}
            self.state = 'VERIFY'
            return self._weigh_cup(self.tare_g)        # 2차 검증 — 용기를 들어 잰다 (그리퍼 비어 있음)
        if k == 'weigh' and st == 'VERIFY':
            if not res.get('valid', False):
                self._verify_invalid += 1
                if self._verify_invalid >= self.dosing_cfg.max_invalid:
                    return self._deviate('WEIGH_INVALID', 'VERIFY')
                return req
            self.verify_net_g = res.get('net_g', 0.0)
            # ① 제품 판정 — 레시피 총 목표량 대비. 개별 원료가 전부 같은 방향으로 치우치면
            #    순량과 Σ투입량이 함께 낮아 ②로는 안 잡힌다 (9/17 조장 합의)
            if abs(self.verify_net_g - self.target_total()) > self.batch_tol_g():
                return self._deviate('BATCH_OUT_OF_SPEC', 'VERIFY')
            # ② 계측 신뢰성 — 스쿱 누적 투입량 대비. 흘림·스쿱 풍량 편향을 잡는다.
            #    min_resolvable_g < Σ(target×tol) 일 때만 의미가 있다 — 아니면 ①이 먼저 걸려 ②는 안 운다.
            #    G1 확정(9/19, SOT Q-11): 19 < 22.5(데모 레시피) → 살아있다. 표본 간격 조정 후 14 여도 결론은 같다.
            if abs(self.verify_net_g - self.dosed_total()) > self.scale.cfg.min_resolvable_g:
                return self._deviate('VERIFY_MISMATCH', 'VERIFY')
            self.state = 'FINISH'
            return self._carry('workbench', 'passbox_done')
        if k == 'carry' and st == 'FINISH':
            if not res.get('grip_inferred', False):
                return self._deviate('GRIP_FAIL', 'FINISH', retry=req)
            return self._park('DONE')
        # NUDGE_WAIT — 세트가 끝나면 nudge_wait 로 물러나 사람이 건드리기를 기다린다 (D-23 반자동, D-24 스테이션).
        # 다음 세트(주문)는 그 NUDGE 뒤에만 받는다. 이 대기는 예외가 아니라 설계다.
        if k == 'move' and st == 'NUDGE_WAIT':
            self.mode = 'PAUSED'                       # 로봇은 섰다 — 주문은 거부, HMI 는 사유를 본다
            return {'kind': 'wait_nudge'}
        if k == 'wait_nudge' and st == 'NUDGE_WAIT':
            self.state, self.mode = self._final, 'DONE'
            return None
        # DISCARDED — 스쿱을 든 채였다면 먼저 반납하고(move → grip open) 용기째 폐기함으로
        if k == 'move' and st == 'DISCARDED':
            return {'kind': 'grip', 'close': False}
        if k == 'grip' and st == 'DISCARDED':
            return self._carry('workbench', 'reject_bin')
        if k == 'carry' and st == 'DISCARDED':
            if not res.get('grip_inferred', False):
                return self._deviate('GRIP_FAIL', 'DISCARDED', retry=req)
            return self._park('DISCARDED')             # 폐기도 세트의 끝 — 같은 자리에서 기다린다
        if k == 'wait_qa':
            return self._after_qa(res.get('decision'))
        if k == 'wait_interlock':
            self.state, self.mode = self._resume_state, 'RUNNING'
            return self._resume
        raise RuntimeError(f'전이 없음: state={st} req={k}')   # 전이표 밖 = 버그. 조용히 넘기지 않는다

    # ── VERIFY 판정 근거 ──────────────────────────────────────────────
    def target_total(self) -> float:
        """레시피 총 목표량."""
        return sum(i.target_g for i in self.spec.items)

    def batch_tol_g(self) -> float:
        """총량 허용치 = Σ(target × tol). 개별 원료가 전부 제 오차 안이면 총량은 자동으로 이 안에 든다
        — 즉 이 검사는 개별 판정이 못 본 것(흘림·편향)이 있을 때만 울린다."""
        return sum(i.target_g * i.tol_pct / 100.0 for i in self.spec.items)

    def dosed_total(self) -> float:
        """스쿱 계량으로 누적한 투입량의 합."""
        return sum(r.actual_g for r in self.results)

    # ── 일탈 ─────────────────────────────────────────────────────────
    def skill_failed(self, req: dict, detail: str = ''):
        """스킬이 실패로 돌아왔다(또는 부를 수 없었다) → FORCE_LIMIT 일탈.

        RULES 상 1회 RETRY 후 FORCED(ERROR) 라 무한 재시도가 되지 않는다. 카운터는
        (원료, 스텝, kind) 별이므로 다른 스텝에서 또 실패하면 거기서 다시 1회 준다.
        """
        if self.state == 'RETURN_MATERIAL' and req.get('kind') == 'return_material':
            # skill_node 는 반환 실패 때 held material 이력을 무효화한다. 같은 반환 Action 을
            # 자동 재시도하면 원료통·스쿱의 대응을 보장할 수 없으므로 즉시 안전 경로로 끝낸다.
            return self._return_failed(detail or '원료통 반환 실패')
        return self._deviate('FORCE_LIMIT', self.state, retry=req, detail=detail)

    def _return_failed(self, detail: str):
        """반환 스킬 실패는 재시도·재투입하지 않고 RETURN_FAILED 기록 후 안전 자세로 간다."""
        key = (self.idx, 'RETURN_MATERIAL', 'FORCE_LIMIT')
        self._counts[key] = self._counts.get(key, 0) + 1
        self.deviations.append({'kind': 'FORCE_LIMIT', 'step': 'RETURN_MATERIAL',
                                'count': self._counts[key], 'action': 'FORCED', 'detail': detail,
                                'material_id': getattr(self, 'cur', None) and self.cur.material_id})
        self.state, self.mode = 'ERROR', 'ERROR'
        return {'kind': 'safe', 'then': None, 'reason': 'RECOVERY'}

    def _deviate(self, kind: str, step: str, retry: dict | None = None, detail: str = ''):
        key = (self.idx, step, kind)
        self._counts[key] = self._counts.get(key, 0) + 1
        action, needs_qa = policy(kind, self._counts[key])
        self.deviations.append({'kind': kind, 'step': step, 'count': self._counts[key], 'action': action,
                                'detail': detail,
                                'material_id': getattr(self, 'cur', None) and self.cur.material_id})
        if action == 'RETRY' and retry:
            return retry
        if action == 'REFILL':
            self._resume, self._resume_state = retry or {'kind': 'scoop', 'material_id': self.cur.material_id,
                                                         'attempt': self.cur.attempts}, self.state
            self.state, self.mode = 'PAUSED', 'PAUSED'
            return {'kind': 'safe', 'then': 'wait_interlock', 'reason': 'REFILL'}
        if action == 'QA':
            self._qa_step = step
            self.state, self.mode = 'DEVIATION', 'DEVIATION'
            return {'kind': 'wait_qa', 'deviation': self.deviations[-1]}
        self.state, self.mode = 'ERROR', 'ERROR'
        return {'kind': 'safe', 'then': None, 'reason': 'RECOVERY'}

    def _after_qa(self, decision: str):
        holding_scoop = self._qa_step != 'VERIFY'      # VERIFY 는 스쿱을 반납한 뒤라 그리퍼가 비어 있다
        if decision == 'APPROVED':
            if not holding_scoop:                      # 대조 불일치를 QA 가 승인 → 그대로 완료품으로
                self.state, self.mode = 'FINISH', 'RUNNING'
                return self._carry('workbench', 'passbox_done')
            self.results.append(self.cur)              # 원료 단위 일탈 승인 → 결과에 남기고 스쿱 반납
            self.state, self.mode = 'RETURN_SCOOP', 'RUNNING'
            return {'kind': 'move', 'station': 'scoop', 'material_id': self.cur.material_id, 'approach': 'AT'}
        self.state, self.mode = 'DISCARDED', 'DONE'
        if holding_scoop:                              # 스쿱부터 반납해야 용기를 잡을 수 있다
            return {'kind': 'move', 'station': 'scoop', 'material_id': self.cur.material_id, 'approach': 'AT'}
        return self._carry('workbench', 'reject_bin')      # 용기째 폐기 — 결과는 on_result 의 DISCARDED 분기
