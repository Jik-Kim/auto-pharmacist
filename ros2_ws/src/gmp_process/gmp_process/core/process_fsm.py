"""공정 상태기계 — 순수 Python. 스킬 호출 대신 「요청」을 돌려주고, 결과를 이벤트로 받는다.

사용 (process_node):
    fsm = ProcessFSM(spec, dosing_cfg, weight_model)
    req = fsm.start()                         # 첫 요청
    while req:
        result = <req 를 스킬로 실행>          # nodes 가 한다
        req = fsm.on_result(req, result)      # 다음 요청 또는 None(끝)
    fsm.state, fsm.mode, fsm.deviations, fsm.results 를 발행

요청은 dict(kind=..., ...) 하나. kind: move | grip | scoop | pour | weigh | measure | safe | wait_qa | wait_interlock
상태 이름은 CellState.step 에 그대로 실린다 (docs/architecture.md 전이표).

TODO([C]) 9/17: 전이표 전부. 지금은 골격과 정상 경로의 형태만.
"""
from dataclasses import dataclass, field

from gmp_dosing.core.dosing import decide
from gmp_process.core.deviation import policy


@dataclass
class ItemRun:
    material_id: str
    target_g: float
    tol_pct: float
    scoop_id: str
    attempts: int = 0
    invalid: int = 0
    actual_g: float = 0.0
    verdict: str = ''


@dataclass
class ProcessFSM:
    spec: object                 # RecipeSpec
    dosing_cfg: object           # DosingConfig
    scale: object                # WeightModel
    state: str = 'IDLE'
    mode: str = 'IDLE'
    idx: int = 0
    tare_g: float = 0.0
    results: list = field(default_factory=list)
    deviations: list = field(default_factory=list)
    _counts: dict = field(default_factory=dict)
    _resume: object = None       # 인터락/QA 후 돌아갈 요청

    # ── 진입 ─────────────────────────────────────────────────────────
    def start(self):
        self.state, self.mode = 'SELF_CHECK', 'RUNNING'
        return {'kind': 'measure'}                      # 빈 그리퍼 외력 — 자가진단 겸 영점 후보

    def _item(self) -> ItemRun:
        it = self.spec.items[self.idx]
        return ItemRun(it.material_id, it.target_g, it.tol_pct, it.scoop_id)

    # ── 전이 ─────────────────────────────────────────────────────────
    def on_result(self, req: dict, res: dict):
        k, st = req['kind'], self.state
        # 종료·대기 전이 — 요청에 then 이 명시된 경우가 우선
        if 'then' in req and k in ('safe', 'move'):
            nxt = req['then']
            if nxt is None:
                return None                      # ERROR / DISCARDED / (FINISH 대체) 종료
            if nxt == 'wait_interlock':
                return {'kind': 'wait_interlock'}
        if k == 'measure' and st == 'SELF_CHECK':
            self.state = 'TARE'
            return {'kind': 'weigh', 'station': 'scale', 'tare_g': 0.0}
        if k == 'weigh' and st == 'TARE':
            self.tare_g = res.get('gross_g', 0.0)
            self.scale.set_tare(self.tare_g)
            self.cur = self._item()
            self.state = 'PICK_SCOOP'
            return {'kind': 'move', 'station': 'scoop_rack', 'approach': 'AT'}
        if k == 'move' and st == 'PICK_SCOOP':
            return {'kind': 'grip', 'close': True, 'target': 'scoop'}
        if k == 'grip' and st == 'PICK_SCOOP':
            if not res.get('grip_inferred', False):
                return self._deviate('GRIP_FAIL', 'PICK_SCOOP', retry={'kind': 'grip', 'close': True, 'target': 'scoop'})
            self.state = 'SCOOP'
            self.cur.attempts += 1
            return {'kind': 'scoop', 'material_id': self.cur.material_id, 'attempt': self.cur.attempts}
        if k == 'scoop' and st == 'SCOOP':
            if not res.get('contact_detected', True):
                return self._deviate('SCOOP_EMPTY', 'SCOOP', retry=req)
            self.state = 'POUR'
            return {'kind': 'pour', 'station': 'scale', 'fraction': res.get('fraction', 1.0)}
        if k == 'pour' and st == 'POUR':
            self.state = 'WEIGH'
            return {'kind': 'weigh', 'station': 'scale', 'tare_g': self.tare_g}
        if k == 'weigh' and st == 'WEIGH':
            d = decide(self.cur.target_g, res.get('net_g', 0.0), self.cur.tol_pct, self.cur.attempts,
                       res.get('valid', False), self.cur.invalid, self.dosing_cfg)
            self.cur.actual_g, self.cur.verdict = res.get('net_g', 0.0), d.verdict
            if d.action == 'DONE':
                self.results.append(self.cur)
                self.state = 'RETURN_SCOOP'
                return {'kind': 'move', 'station': 'scoop_rack', 'approach': 'AT'}
            if d.action == 'SCOOP':
                if d.verdict == 'INVALID':
                    self.cur.invalid += 1
                    return {'kind': 'weigh', 'station': 'scale', 'tare_g': self.tare_g}
                self.state, self.cur.attempts = 'SCOOP', self.cur.attempts + 1
                return {'kind': 'scoop', 'material_id': self.cur.material_id, 'attempt': self.cur.attempts,
                        'fraction': d.fraction}
            return self._deviate(d.kind, 'WEIGH')
        if k == 'move' and st == 'RETURN_SCOOP':
            return {'kind': 'grip', 'close': False}
        if k == 'grip' and st == 'RETURN_SCOOP':
            self.idx += 1
            if self.idx < len(self.spec.items):
                self.cur, self.state = self._item(), 'PICK_SCOOP'
                return {'kind': 'move', 'station': 'scoop_rack', 'approach': 'AT'}
            self.state = 'FINISH'
            return {'kind': 'move', 'station': 'output_tray', 'approach': 'AT'}
        if k == 'move' and st == 'FINISH':
            self.state, self.mode = 'DONE', 'DONE'
            return None
        if k == 'wait_qa':
            return self._after_qa(res.get('decision'))
        if k == 'wait_interlock':
            self.state, self.mode = self._resume_state, 'RUNNING'
            return self._resume
        raise RuntimeError(f'전이 없음: state={st} req={k}')   # 전이표 밖 = 버그. 조용히 넘기지 않는다

    # ── 일탈 ─────────────────────────────────────────────────────────
    def _deviate(self, kind: str, step: str, retry: dict | None = None):
        key = (self.idx, step, kind)
        self._counts[key] = self._counts.get(key, 0) + 1
        action, needs_qa = policy(kind, self._counts[key])
        self.deviations.append({'kind': kind, 'step': step, 'count': self._counts[key], 'action': action,
                                'material_id': getattr(self, 'cur', None) and self.cur.material_id})
        if action == 'RETRY' and retry:
            return retry
        if action == 'REFILL':
            self._resume, self._resume_state = retry or {'kind': 'scoop', 'material_id': self.cur.material_id,
                                                         'attempt': self.cur.attempts}, self.state
            self.state, self.mode = 'PAUSED', 'PAUSED'
            return {'kind': 'safe', 'then': 'wait_interlock', 'reason': 'REFILL'}
        if action == 'QA':
            self.state, self.mode = 'DEVIATION', 'DEVIATION'
            return {'kind': 'wait_qa', 'deviation': self.deviations[-1]}
        self.state, self.mode = 'ERROR', 'ERROR'
        return {'kind': 'safe', 'then': None, 'reason': 'RECOVERY'}

    def _after_qa(self, decision: str):
        if decision == 'APPROVED':
            self.results.append(self.cur)
            self.state, self.mode = 'RETURN_SCOOP', 'RUNNING'
            return {'kind': 'move', 'station': 'scoop_rack', 'approach': 'AT'}
        self.state, self.mode = 'DISCARDED', 'DONE'
        return {'kind': 'move', 'station': 'reject_bin', 'approach': 'AT', 'then': None}
