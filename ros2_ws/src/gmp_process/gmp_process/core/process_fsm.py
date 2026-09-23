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
  PICK_SCOOP → SCOOP_TARE(빈 스쿱 무게) → SCOOP → WEIGH_SCOOP(붓기 전: 퍼낸 양 → 전량 붓기 or 원료통 반환 — v1.3)
  → POUR → WEIGH_RESIDUAL(붓기 후: 스쿱 잔량 → 실제 투입량 누적 → decide) → RETURN_SCOOP
원료가 다 끝나면 VERIFY(용기를 들어 계량) → FINISH → NUDGE_WAIT(nudge_wait 로 물러나 NUDGE 대기, D-23) → DONE.
VERIFY 는 **① 제품 판정 하나만** 한다 (9/22 사용자·조장 확정 — ② 폐지):
  ① |net − Σtarget| > Σ(target×tol) → BATCH_OUT_OF_SPEC (규격 이탈)
종전 ②(계측 신뢰성, |net − Σ투입량|)는 **판정하지 않는다.** 값은 계속 계산해 detail·CellEvent 에
**관측으로만** 남긴다 — ② 를 끈다는 것은 **배치 기록 교차검증을 포기한다**는 뜻이고(제품은 규격 안인데
원료별 투입 기록이 틀린 배치를 검출할 수단이 없어진다), 그 사실이 기록에서 보이도록 수치는 남긴다.
상태 이름은 CellState.step 에 그대로 실린다 (docs/architecture.md 전이표).
"""
import math
from dataclasses import dataclass, field

from gmp_dosing.core.dosing import decide
from gmp_process.core.deviation import RULES, policy


@dataclass
class ToolFingerprint:
    """폭 지문 식별 설정 (D-20 추가 1, v1.2) — 스쿱은 원료별 기대 폭, 약통은 전 원료 공통 규격.

    스쿱 폭은 `stations.yaml` 의 `expected_scoop_width_mm` (`StationMap.widths`), 약통 폭은
    `common.yaml` `gripper.cup_width_mm` 이 그대로 기대값이다 — process_node 가 채워 넘긴다.
    tolerance_mm ≤ 0 이거나 해당 원료의 기대 폭이 없으면 검사를 건너뛴다(테스트 기본값 = 끔).
    """
    scoop_widths_mm: dict = field(default_factory=dict)   # material_id → 기대 스쿱 손잡이 폭 [mm]
    cup_width_mm: float = 0.0                              # 기대 약통 파지부 폭 [mm]
    tolerance_mm: float = 0.0                              # ±margin [mm]


@dataclass
class ItemRun:
    material_id: str
    target_g: float
    tol_pct: float
    attempts: int = 0            # 붓기까지 간 횟수 — ScoopCycle.attempt 는 process_node 가 따로 센다
    returns: int = 0             # 원료통 반환 횟수. 붓지 않았으므로 attempts 에 넣지 않는다
    last_fraction: float = 1.0   # 마지막으로 요청한 담그기 깊이 — 반환 뒤 보정의 기준
    invalid: int = 0             # **지금 단계**의 무효 횟수. 유효하거나 단계가 바뀌면 0 으로 돌아간다
    invalid_step: str = ''       # 위 카운터가 세고 있는 단계 (#213 결정 2)
    scoop_tare_g: float = 0.0    # 빈 스쿱 (SCOOP_TARE)
    scooped_g: float = 0.0       # 붓기 전 스쿱 안의 원료 (WEIGH_SCOOP)
    residual_g: float = 0.0      # 붓기 후 스쿱에 남은 원료 (WEIGH_RESIDUAL)
    actual_g: float = 0.0        # 용기에 들어간 누적 투입량 = Σ(scooped − residual)
    unmeasured: int = 0          # 계량 무효로 투입량을 모르는 채 넘어간 사이클 수 (#213).
                                 # actual_g 에는 안 들어간다 — 그래서 actual_g 가 실제보다 작다
    verdict: str = ''


@dataclass
class ProcessFSM:
    spec: object                 # RecipeSpec
    dosing_cfg: object           # DosingConfig
    scale: object                # WeightModel
    fingerprint: ToolFingerprint = field(default_factory=ToolFingerprint)
    max_returns: int = 3         # 한 원료에서 초과 반환을 허용하는 횟수. **붓기 시도 상한과 다른 것이다** —
                                 # 붓기 상한은 목표량÷스쿱 1회량에 비례해야 하고(200 g÷40 g = 5회),
                                 # 반환 상한은 깊이 보정이 수렴하는지를 보는 오류 복구 한계다. 한 상수로
                                 # 묶여 있으면 큰 레시피 때문에 붓기 상한을 올릴 때 반환 허용도 같이 올라간다 (#189).
    zero_drift_limit_n: float = 0.1   # 빈 그리퍼 영점 이동 한계 [N] — 0 이면 검사 꺼짐.
                                      # **같은 자세(workbench ABOVE) 반복 산포 기준**이다. 잠정값 0.1 N ≈ 10 g
                                      # 으로 σ_cup 0.91 g 의 11배, ① 허용 22.5 g 의 절반 아래 — 이 검사를 만든
                                      # 계기인 27 g(0.26 N) 계단을 실제로 잡는다. B 의 tool_state_check 로
                                      # 같은 자세 반복 시 빈 그리퍼 fz 산포를 받아 확정한다.
    state: str = 'IDLE'
    mode: str = 'IDLE'
    idx: int = 0
    tare_g: float = 0.0          # 빈 용기 (TARE)
    verify_net_g: float = 0.0    # VERIFY 에서 잰 용기 순량
    zero_fz_n: float = 0.0       # TARE 직전 workbench ABOVE 에서 잰 빈 그리퍼 외력 — VERIFY 직전 대조 기준.
                                 # **반드시 같은 자세끼리 비교해야 한다** — tool_force 는 자세 의존이라
                                 # 다른 자세의 값을 기준으로 쓰면 자세 차이가 그대로 '영점 이동' 으로 읽힌다
    verify_zero_drift_n: float = 0.0  # VERIFY 직전 영점 이동량 — detail 에 남는다
    verify_unmeasured: bool = False  # VERIFY 최종 계량이 무효인 채 QA 승인으로 끝났다 (#213)
    verify_detail: str = ''      # VERIFY 판정 근거 한 줄 — ①(판정)과 ②(관측) 수치.
                                 # 일탈이 안 나도 남는다 — process_node 가 CellEvent 로 발행한다
    results: list = field(default_factory=list)
    deviations: list = field(default_factory=list)
    _counts: dict = field(default_factory=dict)
    _cleanup: list = field(default_factory=list)   # CLEANUP 상태에서 아직 안 보낸 정리 요청들 (#213)
    _resume: object = None       # 인터락/QA 후 돌아갈 요청
    _qa_step: str = ''           # QA 판정을 기다리는 일탈이 난 스텝 — APPROVED/DISCARDED 뒤 경로를 가른다
    _zero_recheck: int = 0       # VERIFY 직전 영점 재확인 재측정 횟수
    _tare_invalid: int = 0       # 빈 용기 계량 무효 횟수 — TARE 시점엔 self.cur 가 없어 _invalid_or 를 못 쓴다
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
                'fraction': fraction,                 # 담그기 깊이 (계약 v1.5 depth_fraction) — 붓기는 언제나 전량이다
                'after_return': after_return}         # 반환 직후인가 — 실패 처리를 가른다 (skill_failed)

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

    def _first_fraction(self) -> float:
        """원료의 **첫** 담그기 깊이. 종전에는 1.0 고정이었다 (#221).

        목표가 스쿱 1회량보다 작으면 첫 사이클이 곧장 초과가 되어 반환으로 낭비된다 —
        목표 30 g 에 1회량 40 g 을 그대로 퍼던 것이 그 경우다. 둘째 사이클부터는
        `decide()` 가 같은 식으로 깊이를 정하므로 첫 사이클만 예외였다.

        식은 `decide()` 와 **같게** 둔다. 하한(`min_fraction`)에 눌린 요청을 조용히 올리는
        문제는 여기가 아니라 `decide()` 에 있고 B 소관이다 (#221) — 첫 사이클만 다른 규칙을
        쓰면 그 문제가 두 곳으로 갈라진다.
        """
        cfg = self.dosing_cfg
        return max(cfg.min_fraction, min(1.0, self.cur.target_g / cfg.scoop_nominal_g))

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
        """계량 무효(valid=false)면 재계량, 재시도 상한을 넘으면 WEIGH_INVALID. 유효하면 None.

        **카운터는 단계별이고 유효 결과·단계 전환에서 0 으로 돌아간다** (#213 결정 2).
        종전에는 `self.cur.invalid` 하나를 SCOOP_TARE·WEIGH_SCOOP·WEIGH_RESIDUAL 이 공유하고
        초기화도 없어서, 원료 A(사이클 5회 = 계량 11번) 중 **서로 무관한 두 번**만 무효여도
        일탈이 났다. 「같은 계량을 연속 재시도」라는 정책 의도와 달랐다.
        """
        if res.get('valid', False):
            self.cur.invalid, self.cur.invalid_step = 0, ''
            return None
        if self.cur.invalid_step != step:
            self.cur.invalid, self.cur.invalid_step = 0, step
        self.cur.invalid += 1
        if self.cur.invalid > self.dosing_cfg.max_invalid_retries:
            # 투입 전(SCOOP_TARE·WEIGH_SCOOP)은 정리 후 ERROR, 투입 뒤(WEIGH_RESIDUAL)는 QA (#213).
            # WEIGH_RESIDUAL 은 이미 부은 뒤라 되돌릴 게 없고 투입량만 모르는 상태다.
            if step in ('SCOOP_TARE', 'WEIGH_SCOOP'):
                return self._cleanup_then_error('WEIGH_INVALID', step)
            # WEIGH_RESIDUAL — 이미 부은 뒤라 되돌릴 게 없다. 이 사이클의 투입량은 **모른다**.
            # actual_g 에 0 을 더하지 않고(누산 자체를 건너뛴다) 미측정으로 센다 (#213).
            self.cur.unmeasured += 1
            return self._deviate('WEIGH_INVALID', step,
                                 detail=f'투입량 불확실 — 미측정 {self.cur.unmeasured}회')
        return retry

    def _wrong_tool_or(self, res: dict, step: str, expected_mm: float):
        """폭 지문 불일치 검사 (D-20 추가 1). 기대 폭이 없거나 margin ≤ 0 이면 건너뛴다(None).

        정책상 WRONG_TOOL 은 즉시 QA 다(재시도 없음) — 잘못 꽂힌 스쿱·약통을 로봇이 스스로
        고쳐 낄 방법이 없고, 교차오염 의심은 사람 판단이 필요하다.

        폭이 음수·비유한 값이면 검사를 건너뛴다 — DIO 백엔드는 폭 피드백이 없어 성공해도
        -1 을 돌려준다(grip_inferred 는 DI 핀으로 따로 추론). 이 값을 기대 폭과 비교하면 정상
        파지가 전부 WRONG_TOOL 로 오판된다 (A 리뷰, PR #165).
        """
        tol = self.fingerprint.tolerance_mm
        if not expected_mm or tol <= 0:
            return None
        actual_mm = float(res.get('final_width_mm', 0.0))
        if actual_mm < 0 or not math.isfinite(actual_mm):
            return None
        if abs(actual_mm - expected_mm) > tol:
            return self._deviate('WRONG_TOOL', step,
                                 detail=f'폭 {actual_mm:.1f}mm (기대 {expected_mm:.1f}±{tol:.1f}mm)')
        return None

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
        if st == 'CLEANUP':
            # 정리 중 반환이 실패하면 2차 사고다 — 원인(WEIGH_INVALID)과 따로 FORCE_LIMIT 로 남긴다
            if k == 'return_material' and not res.get('success', False):
                return self._return_failed(res.get('message', '정리 중 원료통 반환 실패'), 'CLEANUP')
            if self._cleanup:
                return self._cleanup.pop(0)
            self.state, self.mode = 'ERROR', 'ERROR'
            return {'kind': 'safe', 'then': None, 'reason': 'RECOVERY'}
        if k == 'measure' and st == 'SELF_CHECK':
            # 자가진단 전용이다. 이 값을 영점 기준으로 쓰지 않는다 — 여기 자세는 배치 시작 자세고
            # VERIFY 는 workbench 라, 자세 차이가 영점 이동으로 둔갑한다. 기준은 TARE 직전에 잡는다.
            self.state = 'PICK_CONTAINER'
            return self._carry('passbox_empty', 'workbench')
        if k == 'carry' and st == 'PICK_CONTAINER':
            if not res.get('grip_inferred', False):
                return self._deviate('GRIP_FAIL', 'PICK_CONTAINER', retry=req)
            dev = self._wrong_tool_or(res, 'PICK_CONTAINER', self.fingerprint.cup_width_mm)
            if dev is not None:
                return dev
            self.state = 'TARE'
            # `carry` 는 dst ABOVE + 그리퍼 열림으로 끝난다(모듈 docstring) — 지금 로봇은
            # **workbench ABOVE, 빈 그리퍼**다. VERIFY 직전과 같은 자세이므로 여기서 영점을 잡는다.
            return {'kind': 'measure'}
        if k == 'measure' and st == 'TARE':
            self.zero_fz_n = res.get('fz_mean_n', 0.0)
            return self._weigh_cup(0.0)
        if k == 'weigh' and st == 'TARE':
            # 빈 용기 계량도 다른 계량과 같은 유효성 규칙을 받는다. 무효한 tare 가 그냥 통과하면
            # VERIFY 의 net = gross − tare_g 가 어긋나 ①(제품 규격)·②(계측 신뢰성)이 둘 다 틀린다.
            # `_invalid_or` 는 카운터를 `self.cur` 에 두는데 이 시점엔 원료가 아직 없어(아래에서
            # 생성) 쓸 수 없다 — VERIFY 와 같은 배치 단위 카운터로 센다.
            if not res.get('valid', False):
                self._tare_invalid += 1
                if self._tare_invalid > self.dosing_cfg.max_invalid_retries:
                    return self._cleanup_then_error('WEIGH_INVALID', 'TARE')
                return req
            self.tare_g = res.get('gross_g', 0.0)
            self.cur = self._item()
            self.state = 'PICK_SCOOP'
            return {'kind': 'move', 'station': 'scoop', 'material_id': self.cur.material_id, 'approach': 'AT'}
        if k == 'move' and st == 'PICK_SCOOP':
            return {'kind': 'grip', 'close': True, 'target': 'scoop'}
        if k == 'grip' and st == 'PICK_SCOOP':
            if not res.get('grip_inferred', False):
                return self._deviate('GRIP_FAIL', 'PICK_SCOOP', retry={'kind': 'grip', 'close': True, 'target': 'scoop'})
            dev = self._wrong_tool_or(res, 'PICK_SCOOP', self.fingerprint.scoop_widths_mm.get(self.cur.material_id))
            if dev is not None:
                return dev
            self.state = 'SCOOP_TARE'
            return self._weigh_scoop()                 # 빈 스쿱 무게 — 원료마다 1회
        if k == 'weigh_scoop' and st == 'SCOOP_TARE':
            r = self._invalid_or(res, 'SCOOP_TARE', req)
            if r is not None:
                return r
            self.cur.scoop_tare_g = res.get('gross_g', 0.0)
            self.state = 'SCOOP'
            return self._scoop(self._first_fraction())
        if k == 'scoop' and st == 'SCOOP':
            if not res.get('contact_detected', True):
                return self._scoop_empty(req)
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
            if self.cur.returns >= self.max_returns:
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
            # 클램프하지 않는다 — 붓고 나면 참 잔량이 0 근처라 측정 잡음의 절반이 음수인데,
            # max(0, ...) 로 자르면 잔량이 체계적으로 과대평가되고 투입량이 그만큼 과소평가된다
            # (편향 ≈ σ/√(2π)). 회계 누산기는 편향이 없어야 한다. ② 판정이 없어져도 Σ투입량은
            # 배치 기록(ScoopCycle·dispense_result)에 그대로 남으므로 편향은 여전히 문제다.
            # TODO(영점 재확인과 같은 묶음): 잔량이 −3σ 보다 더 음수면 회계가 아니라 **유효성**
            # 문제다 (파지 이동·원료 손실·계량 오염). 재계량 또는 WEIGH_INVALID 로 거른다.
            self.cur.residual_g = res.get('gross_g', 0.0) - self.cur.scoop_tare_g
            self.cur.actual_g += self.cur.scooped_g - self.cur.residual_g
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
            # **TARE 때와 같은 자세로 옮긴 뒤** 빈 그리퍼 영점을 다시 잰다. 지금은 스쿱 거치대에
            # 서 있어서 그대로 재면 자세 차이가 영점 이동으로 읽힌다 — tool_force 는 자세 의존이다.
            return {'kind': 'move', 'station': 'workbench', 'approach': 'ABOVE'}
        if k == 'move' and st == 'VERIFY':
            # 용기를 들기 전, 그리퍼가 빈 채로 잰다. NUDGE 는 정지·재개 장치일 뿐 계량 유효성과
            # 연결돼 있지 않다 — 충격이 임계를 넘어 NUDGE 가 떠도 진행 중 계량을 무효화하지 않고,
            # 못 넘으면 감지조차 안 된다. 어느 쪽이든 오염된 값이 장부에 들어간다. 여기서 막는다.
            return {'kind': 'measure'}
        if k == 'measure' and st == 'VERIFY':
            drift_n = res.get('fz_mean_n', 0.0) - self.zero_fz_n
            if self.zero_drift_limit_n > 0 and abs(drift_n) > self.zero_drift_limit_n:
                self._zero_recheck += 1
                if self._zero_recheck > self.dosing_cfg.max_invalid_retries:
                    return self._deviate('WEIGH_INVALID', 'VERIFY',
                                         detail=f'빈 그리퍼 영점 이동 {drift_n:+.3f} N '
                                                f'(한계 {self.zero_drift_limit_n:.3f}) — 계량 오염 의심')
                return req                              # 다시 잰다 — 일시적 흔들림일 수 있다
            self.verify_zero_drift_n = drift_n
            return self._weigh_cup(self.tare_g)        # 2차 검증 — 용기를 들어 잰다 (그리퍼 비어 있음)
        if k == 'weigh' and st == 'VERIFY':
            if not res.get('valid', False):
                self._verify_invalid += 1
                if self._verify_invalid > self.dosing_cfg.max_invalid_retries:
                    # 최종 계량을 못 믿는다 → ① 판정 불가. QA 가 승인하면 값 없이 나간다 (#213).
                    self.verify_unmeasured = True
                    self.verify_detail = ('최종 계량 미측정 — 용기 순량을 모른다. '
                                          f'Σ투입량 {self.dosed_total():.1f} g (①규격 판정 불가)')
                    return self._deviate('WEIGH_INVALID', 'VERIFY', detail=self.verify_detail)
                return req
            self.verify_net_g = res.get('net_g', 0.0)
            # ① 제품 판정 — 레시피 총 목표량 대비. **유일한 판정이다** (9/22 ② 폐지).
            spec_err = self.verify_net_g - self.target_total()
            spec_tol = self.batch_tol_g()
            # 종전 ②(회계 대조)는 판정하지 않고 **관측만** 한다. 값을 계속 남기는 것은 ② 폐지가
            # 「배치 기록 교차검증을 포기한다」는 결정이기 때문이다 — 포기한 것이 무엇인지 기록에서
            # 보여야 한다. 이 수치가 커도 배치는 멈추지 않는다.
            acct_err = self.verify_net_g - self.dosed_total()
            self.verify_detail = (f'net {self.verify_net_g:.1f} g · '
                                  f'①규격 {spec_err:+.1f}/{spec_tol:.1f} · '
                                  f'②회계 {acct_err:+.1f} (관측, 판정 안 함) · '
                                  f'영점이동 {self.verify_zero_drift_n:+.3f} N')
            if abs(spec_err) > spec_tol:
                return self._deviate('BATCH_OUT_OF_SPEC', 'VERIFY', detail=self.verify_detail)
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
        if req.get('kind') == 'scoop' and req.get('after_return'):
            # 실물 skill_node 는 반환 중에 `_return_rescoop_blocked` 를 세우고 이후 Scoop 을 전부
            # 거부한다 (v1.5.1 · PR #43). 플래그는 풀리지 않으므로 같은 요청을 재시도해도 같은
            # 이유로 거부된다 — 실패 사유가 무엇이든 반환 직후 스쿱은 재시도가 의미 없다.
            # FORCE_LIMIT 2건(RETRY→FORCED)을 쌓는 대신 사유를 남기고 한 번에 끝낸다.
            # A 가 연결 경로(#64)를 구현하면 이 분기는 없어진다.
            return self._return_failed(f'반환 후 재스쿱 차단 — {detail or "스킬 거부"}', step=self.state)
        return self._deviate('FORCE_LIMIT', self.state, retry=req, detail=detail)

    # ── 투입 전 계량 무효: 손에 든 것을 정리한 뒤 멈춘다 (#213) ──────────
    def _cleanup_path(self, step: str) -> list:
        """그 단계에서 손에 뭐가 있느냐로 갈린다. 재스쿱은 하지 않는다 (#64).

        중간 경유는 `material_N.posx`(AT) 다 — 원료통 위 충돌 회피 자세이고 새 스테이션이 아니다
        (9/22 조장 확인). `return_end_posj` 는 `ReturnMaterial` 이 끝나는 자세라 여기서 안 쓴다.
        """
        if step == 'TARE':
            return []                                   # 그리퍼가 비어 있고 용기는 workbench 에 놓여 있다
        via = [{'kind': 'move', 'station': 'material', 'material_id': self.cur.material_id, 'approach': 'AT'},
               {'kind': 'move', 'station': 'scoop', 'material_id': self.cur.material_id, 'approach': 'AT'},
               {'kind': 'grip', 'close': False}]
        if step == 'SCOOP_TARE':
            return via                                  # 빈 스쿱이라 반환할 원료가 없다
        return [self._return_material()] + via          # WEIGH_SCOOP — 스쿱에 원료가 들어 있다

    def _cleanup_then_error(self, kind: str, step: str, detail: str = ''):
        """일탈을 먼저 기록하고 정리 경로로 들어간다 — 정리 도중 죽어도 원인이 남아야 한다."""
        key = (self.idx, step, kind)
        self._counts[key] = self._counts.get(key, 0) + 1
        path = self._cleanup_path(step)
        names = ' → '.join(r['kind'] for r in path) or '없음'
        self.deviations.append({'kind': kind, 'step': step, 'count': self._counts[key],
                                'action': 'FORCED',     # 자동 복구가 아니다 — 사람이 와야 한다
                                'detail': (detail + ' · ' if detail else '') + f'정리 {names}',
                                'material_id': getattr(self, 'cur', None) and self.cur.material_id})
        self._cleanup = path
        if not path:
            self.state, self.mode = 'ERROR', 'ERROR'
            return {'kind': 'safe', 'then': None, 'reason': 'RECOVERY'}
        self.state, self.mode = 'CLEANUP', 'ERROR'      # mode 는 이미 되돌릴 수 없다는 뜻으로 ERROR
        return self._cleanup.pop(0)

    def _return_failed(self, detail: str, step: str = 'RETURN_MATERIAL'):
        """반환·반환 후 재스쿱 실패는 재시도·재투입하지 않고 FORCED 로 기록한 뒤 안전 자세로 간다."""
        key = (self.idx, step, 'FORCE_LIMIT')
        self._counts[key] = self._counts.get(key, 0) + 1
        self.deviations.append({'kind': 'FORCE_LIMIT', 'step': step,
                                'count': self._counts[key], 'action': 'FORCED', 'detail': detail,
                                'material_id': getattr(self, 'cur', None) and self.cur.material_id})
        self.state, self.mode = 'ERROR', 'ERROR'
        return {'kind': 'safe', 'then': None, 'reason': 'RECOVERY'}

    def _scoop_empty(self, retry: dict):
        """접촉 실패. **보충으로 넘어가는 그 순간부터 kind 를 `MATERIAL_EMPTY` 로 올린다** (#111 A안).

        종전에는 kind 가 끝까지 `SCOOP_EMPTY` 이고 **action 만** REFILL 로 올라가서,
        BRD FR-14·3.5.4·흐름도·HMI 이름표·`DEV_TO_OUTCOME` 이 모두 전제하는 `MATERIAL_EMPTY` 를
        **아무도 내지 않았다.** 거동은 맞는데 기록이 틀린 상태였다 (9/23 조장 결정으로 A안 확정).

        「한 번 못 펐다」와 「원료통이 비었다」는 다른 사실이다 — 앞은 재시도로 풀리고 뒤는
        사람이 채워야 풀린다. 넘어가는 지점은 여기서 정하지 않고 **정책표에 물어본다**
        (`RULES['SCOOP_EMPTY']` 의 재시도 상한을 고치면 이 경계도 같이 따라온다).

        보충 뒤에도 접촉이 없으면 `SCOOP_EMPTY` 카운터는 상한에 멈춰 있으므로 계속
        `MATERIAL_EMPTY` 가 나간다 — 「채웠는데 또 비었다」가 그대로 기록된다.
        """
        step = 'SCOOP'
        nxt = self._counts.get((self.idx, step, 'SCOOP_EMPTY'), 0) + 1
        if policy('SCOOP_EMPTY', nxt)[0] == 'RETRY':
            return self._deviate('SCOOP_EMPTY', step, retry=retry)
        return self._deviate('MATERIAL_EMPTY', step, retry=retry,
                             detail=f'재시도 {RULES["SCOOP_EMPTY"][0]}회 뒤에도 원료에 닿지 않는다 — 보충 필요')

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
        if self._qa_step == 'PICK_CONTAINER':
            # WRONG_TOOL 만 여기서 QA 로 온다(GRIP_FAIL 은 FORCED 로 빠진다) — `self.cur` 가 아직
            # 없다(TARE 전). carry 는 이미 workbench 에 내려놓고 그리퍼를 연 뒤라 스쿱 반납 단계가 없다.
            if decision == 'APPROVED':
                self.state, self.mode = 'TARE', 'RUNNING'
                return self._weigh_cup(0.0)
            self.state, self.mode = 'DISCARDED', 'DONE'
            return self._carry('workbench', 'reject_bin')
        holding_scoop = self._qa_step != 'VERIFY'      # VERIFY 는 스쿱을 반납한 뒤라 그리퍼가 비어 있다
        if decision == 'APPROVED':
            if self._qa_step == 'PICK_SCOOP':
                # WRONG_TOOL 만 여기로 온다(GRIP_FAIL 은 FORCED 로 빠진다) — 스쿱을 이미 쥔 채다.
                # 승인은 "이 스쿱으로 계속 진행" 이지 원료를 건너뛰는 게 아니다. 다른 QA 지점과
                # 달리 아직 아무것도 못 퍼서 결과에 남길 게 없다 — 정상 경로(SCOOP_TARE)로 이어간다
                # (A 리뷰, PR #165 — 예전엔 빈 ItemRun 을 결과로 남기고 원료를 건너뛰었다).
                self.state = 'SCOOP_TARE'
                return self._weigh_scoop()
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
