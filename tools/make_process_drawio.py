#!/usr/bin/env python3
"""docs/process_flow.md 를 draw.io 파일로 — docs/diagrams/process_flow.drawio (4 페이지).
실행: python3 tools/make_process_drawio.py
"""
import pathlib
from xml.sax.saxutils import escape as _esc

def escape(t):
    # html=1 라벨: 줄바꿈은 <br>, 속성이므로 따옴표도 이스케이프
    return _esc(t.replace('\n', '<br>'), {'"': '&quot;'})

OUT = pathlib.Path(__file__).resolve().parent.parent / 'docs/diagrams/process_flow.drawio'

# 색
INK, ACC, ACCS, WARM, WARMS, OK, OKS, RED, REDS, GRAY, GRAYS = \
    '#161B2A', '#3451D1', '#E4E9FB', '#D9782A', '#FBEBDD', '#1F7A8C', '#DDEFF3', '#C0392B', '#FADBD8', '#5F6980', '#F3F5FA'

class Page:
    def __init__(self, name):
        self.name, self.cells, self.n = name, [], 1
    def _id(self):
        self.n += 1; return f'c{self.n}'
    def box(self, x, y, w, h, text, fill=ACCS, stroke=ACC, font=INK, bold=False, dashed=False, size=12, align='center', shape='rounded=1;'):
        i = self._id()
        st = (f'{shape}whiteSpace=wrap;html=1;fillColor={fill};strokeColor={stroke};fontColor={font};fontSize={size};'
              f'align={align};verticalAlign=top;spacingTop=4;spacingLeft=6;spacingRight=6;' + ('fontStyle=1;' if bold else '') + ('dashed=1;' if dashed else ''))
        self.cells.append(f'<mxCell id="{i}" value="{escape(text)}" style="{st}" vertex="1" parent="1"><mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')
        return i
    def note(self, x, y, w, h, text, size=11):
        return self.box(x, y, w, h, text, fill='#FFFFFF', stroke=GRAY, font=GRAY, size=size, align='left', shape='rounded=0;')
    def edge(self, a, b, label='', color=INK, dashed=False, points=(), exit=None, entry=None, width=1.5, lpos=None):
        i = self._id()
        st = f'edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;endArrow=block;endFill=1;strokeColor={color};strokeWidth={width};fontColor={color};fontSize=10;labelBackgroundColor=#FFFFFF;'
        if dashed: st += 'dashed=1;'
        if exit: st += f'exitX={exit[0]};exitY={exit[1]};exitDx=0;exitDy=0;'
        if entry: st += f'entryX={entry[0]};entryY={entry[1]};entryDx=0;entryDy=0;'
        pts = ''.join(f'<mxPoint x="{px}" y="{py}"/>' for px, py in points)
        lp = f' x="{lpos[0]}" y="{lpos[1]}"' if lpos else ''
        geo = f'<mxGeometry{lp} relative="1" as="geometry">' + (f'<Array as="points">{pts}</Array>' if pts else '') + '</mxGeometry>'
        self.cells.append(f'<mxCell id="{i}" value="{escape(label)}" style="{st}" edge="1" parent="1" source="{a}" target="{b}">{geo}</mxCell>')
        return i
    def xml(self, w=1920, h=1400):
        body = ''.join(self.cells)
        return (f'<diagram id="{self.name}" name="{escape(self.name)}"><mxGraphModel dx="1400" dy="900" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="{w}" pageHeight="{h}">'
                f'<root><mxCell id="0"/><mxCell id="1" parent="0"/>{body}</root></mxGraphModel></diagram>')

# ───────────────────────── 페이지 1: 노드 입출력 ─────────────────────────
p1 = Page('1 노드 입출력')
p1.box(40, 20, 1860, 40, 'gmp_process — process_node 입출력 (계약 v1.2 · 9/18 확정 · ns /cell)', fill='#FFFFFF', stroke='none', bold=True, size=18, align='left')
hmi = p1.box(40, 140, 200, 420, 'hmi_web_node (D)\n\n웹 HMI · Flask :5000\n\n주문 제출\nQA 승인 / 폐기\n인터락 ENTER / EXIT', fill=GRAYS, stroke=GRAY, bold=True)
proc = p1.box(560, 100, 700, 560, 'process_node (C)', fill='#FFFFFF', stroke=ACC, bold=True, size=14)
cb = p1.box(590, 150, 300, 200, 'rclpy 콜백 스레드\n\n_srv_submit → FSM 생성, run_loop 시작\n_srv_qa → _qa_decision 저장, _qa.set()\n_srv_interlock → ENTER: cancel→safe_pose→granted\n                        EXIT: _interlock_exit.set()\nevent(NUDGE) 구독 → 게이트 토글\n\n콜백은 값만 저장한다. 로봇을 부르지 않는다', fill=ACCS, align='left', size=11)
loop = p1.box(930, 150, 300, 200, 'run_loop 스레드 (배치마다 1개)\n\nreq = fsm.start()\nwhile req:\n    res = _execute(req)   ← 스킬 한 번에 하나\n    req = fsm.on_result(req, res)\n    발행(state·weight·scoop_cycle·result·deviation·event)\n\nwait_qa / wait_interlock 는 Event.wait()', fill=ACCS, align='left', size=11)
fsm = p1.box(590, 390, 640, 240, 'core/process_fsm.py — ProcessFSM (ROS 비의존, pytest)\n\nstate · mode · idx(원료) · tare_g · verify_net_g · cur(ItemRun: attempts·invalid·scoop_tare·scooped·residual·actual·verdict) · results · deviations · _counts · _resume · _qa_step · slot\n\nstart() → 첫 요청     on_result(req, res) → 다음 요청 dict 또는 None(끝)\n_deviate(kind, step) → deviation.py policy(kind, count) → RETRY / REFILL / QA / FORCED\n_carry(src, dst) → {"kind":"carry", src, dst, slot}\n\n의존: gmp_dosing.core.dosing.decide(target, net, tol, attempts, valid, invalid, cfg) → DONE / SCOOP(fraction) / DEVIATION(kind)\n        core/recipe.py parse() → RecipeSpec(product, items[Item(material_id, target_g, tol_pct)])', fill=OKS, stroke=OK, align='left', size=11)
p1.edge(cb, loop, 'Event / 플래그', color=GRAY, dashed=True)
p1.edge(loop, fsm, 'dict 요청 ↔ dict 결과', color=OK)
skill = p1.box(1580, 100, 320, 560, 'skill_node (A)\n\n로봇을 만지는 유일한 노드\nDSR 워커 스레드 1개 — 직렬\n\nAction 서버 5\n  move_to_station · scoop · pour\n  weigh_container · weigh_held [v1.2]\nService 서버 3\n  set_gripper · measure_force · safe_pose\n\n발행: gripper_state · event(NUDGE)', fill=GRAYS, stroke=GRAY, bold=True)
rec = p1.box(560, 900, 700, 110, 'record_node (D) — SQLite 단일 기록자        hmi_web_node (D) — 화면 표시\n\n구독: state · weight · scoop_cycle · dispense_result · deviation · event', fill=GRAYS, stroke=GRAY, bold=True)
# HMI → process (Service)
p1.edge(hmi, proc, 'Service submit_order\nSubmitOrder: Recipe → accepted, batch_id\n실행 중이면 accepted=false', color=ACC, exit=(1, 0.2), entry=(0, 0.12), lpos=(0, -24))
p1.edge(hmi, proc, 'Service qa_decision\ndeviation_id, decision, operator_id → accepted\n대기 중 ID 불일치·DEVIATION 아니면 거부', color=ACC, exit=(1, 0.45), entry=(0, 0.3), lpos=(0, -24))
p1.edge(hmi, proc, 'Service interlock\nENTER=1 / EXIT=2, reason → granted\nENTER 는 safe_pose 성공 후 granted', color=ACC, exit=(1, 0.7), entry=(0, 0.48), lpos=(0, -24))
p1.edge(hmi, proc, 'Action run_batch (RunBatch)\nCLI·시험용, 우선순위 낮음', color=GRAY, dashed=True, exit=(1, 0.92), entry=(0, 0.66), lpos=(0, -18))
# process → skill
p1.edge(proc, skill, 'Action move_to_station\nstation_id, approach ABOVE/AT, vel_scale\n→ success, reached', color=WARM, exit=(1, 0.1), entry=(0, 0.1), lpos=(0, -24))
p1.edge(proc, skill, 'Action scoop\nmaterial_id, attempt\n→ contact, max force, insertion depth', color=WARM, exit=(1, 0.22), entry=(0, 0.22), lpos=(0, -24))
p1.edge(proc, skill, 'Action pour\nfraction → success (목적지: 고정 scale)', color=WARM, exit=(1, 0.34), entry=(0, 0.34), lpos=(0, -18))
p1.edge(proc, skill, 'Action WeighContainer (고정 scale 용기 들어, 그리퍼 비어야) · WeighHeld (든 채로, v1.2)\ntare_g → reading: gross / net / std / valid / subject', color=WARM, exit=(1, 0.46), entry=(0, 0.46), lpos=(0, -24))
p1.edge(proc, skill, 'Service set_gripper\nclose, width_mm, force_n, timeout_s\n→ success, final_width_mm, grip_inferred', color=WARM, exit=(1, 0.58), entry=(0, 0.58), lpos=(0, -24))
p1.edge(proc, skill, 'Service measure_force\nsamples, settle_s\n→ force[6], fz_mean_n, fz_std_n, valid', color=WARM, exit=(1, 0.7), entry=(0, 0.7), lpos=(0, -24))
p1.edge(proc, skill, 'Service safe_pose\nreason → success', color=WARM, exit=(1, 0.82), entry=(0, 0.82), lpos=(0, -18))
p1.edge(skill, proc, 'Topic event  code=NUDGE\n사람 접촉 (D-21) → 루프 게이트', color=RED, dashed=True, exit=(0, 0.94), entry=(1, 0.94), lpos=(0, 18))
# process → record (Topics)
p1.edge(proc, rec, 'Topic state\nCellState · 0.5 s + 전이\nTRANSIENT_LOCAL', color=OK, exit=(0.08, 1), entry=(0.08, 0), lpos=(-0.65, 0))
p1.edge(proc, rec, 'Topic weight\nWeightReading · weigh 마다', color=OK, exit=(0.24, 1), entry=(0.24, 0), lpos=(-0.3, 0))
p1.edge(proc, rec, 'Topic scoop_cycle\n스쿠핑 시도마다', color=OK, exit=(0.4, 1), entry=(0.4, 0), lpos=(0.1, 0))
p1.edge(proc, rec, 'Topic dispense_result\n원료 종료 시', color=OK, exit=(0.56, 1), entry=(0.56, 0), lpos=(0.55, 0))
p1.edge(proc, rec, 'Topic deviation\nTRANSIENT_LOCAL · QA 후 재발행', color=OK, exit=(0.72, 1), entry=(0.72, 0), lpos=(-0.5, 0))
p1.edge(proc, rec, 'Topic event\nBATCH_START/END · STEP\nINTERLOCK_* · INTERVENTION_FORCED', color=OK, exit=(0.88, 1), entry=(0.88, 0), lpos=(0.1, 0))
p1.note(40, 600, 440, 230, '읽는 법\n\n파란 실선 = HMI 가 부르는 Service (process 가 서버)\n주황 = process 가 부르는 스킬 (skill_node 가 서버)\n청록 = process 가 발행하는 Topic\n빨강 점선 = skill_node → process 구독\n\n원칙: 스킬은 한 번에 하나 · 콜백은 값만 저장 · FSM 은 로봇을 모른다')
p1.note(1580, 900, 320, 110, '미합의 1건 (A 와)\n용기 반송 슬롯 번호를 MoveToStation 에 어떻게 넘길지\n— station_id 접미사 "magazine#1"  vs  계약 v1.2 uint8 slot')

# ───────────────────────── 페이지 2: 상태 전이도 ─────────────────────────
p2 = Page('2 상태 전이도')
p2.box(40, 20, 1860, 40, 'ProcessFSM 상태 전이도 — core/process_fsm.py (상태 이름 = CellState.step)', fill='#FFFFFF', stroke='none', bold=True, size=18, align='left')
W, H = 190, 72
X = [40, 320, 600, 880, 1160, 1440]          # 열
Y = [120, 400, 680, 960]                      # 행
def S(c, r, name, sub='', kind='n'):
    fill, stroke = {'n': (ACCS, ACC), 'p': (WARMS, WARM), 'e': (REDS, RED), 'd': (OKS, OK), 'i': (GRAYS, GRAY)}[kind]
    return p2.box(X[c], Y[r], W, H, name + ('\n' + sub if sub else ''), fill=fill, stroke=stroke, bold=True, size=12)
cx = lambda c, f=0.5: X[c] + W * f          # 상자 안 x 좌표
idle = S(0, 0, 'IDLE', 'submit_order 대기', 'i')
selfc = S(1, 0, 'SELF_CHECK', 'req: measure — 빈 그리퍼 외력\n(영점·센서 확인, 용기 아님)')
pickc = S(2, 0, 'PICK_CONTAINER', 'req: carry magazine→scale')
tare = S(3, 0, 'TARE', 'req: weigh — 빈 용기 들어\n풍량 (배치 1회)')
picks = S(0, 1, 'PICK_SCOOP', 'req: move(scoop_N) → grip\n스쿱은 원료통 아래 (D-24)')
stare = S(1, 1, 'SCOOP_TARE', 'req: weigh_scoop — 빈 스쿱\n든 채 (원료마다 1회)')
scoop = S(2, 1, 'SCOOP', 'req: scoop(material, attempt)')
wscoop = S(3, 1, 'WEIGH_SCOOP', 'req: weigh_scoop — 붓기 전\n퍼낸 양 → 붓기 비율')
pour = S(4, 1, 'POUR', 'req: pour(fraction)')
wres = S(5, 1, 'WEIGH_RESIDUAL', 'req: weigh_scoop — 붓기 후 스쿱 잔량\n투입량 = 전 − 후 → decide()')
ret = S(1, 2, 'RETURN_SCOOP', 'req: move(scoop_N) → grip(open)\n전용 스쿱 = 교차오염 방지')
verify = S(3, 2, 'VERIFY', 'req: weigh — 용기 들어 총량 − 풍량\n배치 끝 1회. ① 레시피 총량 ② Σ투입량')
finish = S(4, 2, 'FINISH', 'req: carry scale→output_tray')
done = S(5, 2, 'DONE', 'event BATCH_END', 'd')
err = S(0, 3, 'ERROR', 'req: safe(then None)\nevent INTERVENTION_FORCED', 'e')
paused = S(2, 3, 'PAUSED', 'req: safe → wait_interlock', 'p')
dev = S(4, 3, 'DEVIATION', 'req: wait_qa (QA 원격 판정)', 'p')
disc = S(5, 3, 'DISCARDED', 'req: (스쿱 반납 →) carry scale→reject_bin', 'e')
# ── 정상 경로 (파랑) — 가로선 라벨은 선 위(-), 세로·꺾인 선은 lpos 로 위치 지정
p2.edge(idle, selfc, 'submit_order 수락\nbatch_id 발급 · event BATCH_START', color=ACC, lpos=(0, -22))
p2.edge(selfc, pickc, 'measure valid', color=ACC, lpos=(0, -14))
p2.edge(pickc, tare, 'carry grip_inferred=true', color=ACC, lpos=(0, -14))
p2.edge(tare, picks, 'tare_g 저장 · scale.set_tare() · cur = 원료 0 · weight 발행', color=ACC,
        exit=(0.5, 1), entry=(0.5, 0), points=((cx(3), 300), (cx(0), 300)), lpos=(0, -14))
p2.edge(picks, stare, 'grip_inferred=true', color=ACC, lpos=(0, 16))
p2.edge(stare, scoop, 'scoop_tare_g 저장\nattempts=1', color=ACC, lpos=(0, 22))
p2.edge(scoop, wscoop, 'contact_detected=true', color=ACC, lpos=(0, 16))
p2.edge(wscoop, pour, 'scooped = gross − tare\nfraction = min(1, 부족량/scooped)', color=ACC, lpos=(0, 22))
p2.edge(pour, wres, '', color=ACC)
p2.edge(wres, ret, 'OK (|err| ≤ tol) · actual += scooped − residual → dispense_result 발행', color=OK,
        exit=(0.15, 1), entry=(1, 0.7), points=((cx(5, 0.15), 600), (X[1] + W + 40, 600), (X[1] + W + 40, Y[2] + 45)), lpos=(-0.25, -14))
p2.edge(ret, picks, 'grip(open) · idx+1 · 다음 원료 있음', color=ACC,
        exit=(0.5, 0), entry=(0.5, 1), points=((cx(1), Y[2] - 40), (cx(0), Y[2] - 40)), lpos=(0, -14))
p2.edge(ret, verify, '마지막 원료였음 (그리퍼 비어 있음)', color=ACC, exit=(1, 0.3), entry=(0, 0.3), lpos=(0, -14))
p2.edge(verify, finish, '|net − Σactual| ≤ min_resolvable_g', color=OK, lpos=(0, -14))
p2.edge(finish, done, 'carry ok', color=OK, lpos=(0, -14))
# ── 보정·재계량 (주황·회색) — 1행 위 복도 (y 340)
p2.edge(wres, scoop, 'UNDER, attempts<3 → scoop(attempt+1, fraction 힌트)', color=WARM,
        exit=(0.3, 0), entry=(0.75, 0), points=((cx(5, 0.3), 340), (cx(2, 0.75), 340)), lpos=(0, -14))
# 계량 무효 자기 루프 (회색) — 상자 아래 y+H+35
def rew(node, c):
    p2.edge(node, node, 'INVALID ≤2 → 재계량\n3회 WEIGH_INVALID → QA', color=GRAY, exit=(0.3, 1), entry=(0.7, 1),
            points=((cx(c, 0.3), Y[1] + H + 35), (cx(c, 0.7), Y[1] + H + 35)), lpos=(0, 22))
rew(stare, 1); rew(wscoop, 3); rew(wres, 5)
p2.edge(verify, verify, 'INVALID ≤2 → 재계량', color=GRAY, exit=(0.3, 0), entry=(0.7, 0),
        points=((cx(3, 0.3), Y[2] - 30), (cx(3, 0.7), Y[2] - 30)), lpos=(0, -12))
# ── 일탈 → DEVIATION (주황) — 오른쪽 복도 x=1690, 2·3행 사이 복도 y=840
p2.edge(wres, dev, 'OVER → OVERFILL\nUNDER 4회째 → TIMEOUT', color=WARM,
        exit=(1, 0.6), entry=(0.7, 0), points=((X[5] + W + 60, Y[1] + 38), (X[5] + W + 60, 840), (cx(4, 0.7), 840)), lpos=(-0.5, -60))
p2.edge(verify, dev, '① 규격 이탈 → BATCH_OUT_OF_SPEC\n② 계측 불일치 → VERIFY_MISMATCH', color=WARM,
        exit=(0.7, 1), entry=(0.5, 0), points=((cx(3, 0.7), 870), (cx(4, 0.5), 870)), lpos=(0, 14))
# ── 재시도 자기 루프 (주황) — 상자 위 y-35
def loop(node, c, label, ex=0.35, en=0.75, lp=-14):
    p2.edge(node, node, label, color=WARM, exit=(ex, 0), entry=(en, 0),
            points=((cx(c, ex), Y[1 if node in (picks, scoop) else 0] - 35), (cx(c, en), Y[1 if node in (picks, scoop) else 0] - 35)), lpos=(0, lp))
loop(pickc, 2, 'GRIP_FAIL ≤3 → 같은 carry 재시도')
loop(picks, 0, 'GRIP_FAIL ≤3 → grip 재시도\n[추가1] 폭 불일치 → WRONG_TOOL → DEVIATION', lp=-22)
loop(scoop, 2, 'SCOOP_EMPTY ≤3 → scoop 재시도', ex=0.2, en=0.55)
p2.edge(finish, finish, 'GRIP_FAIL ≤3', color=WARM, exit=(0.3, 0), entry=(0.7, 0), points=((cx(4, 0.3), Y[2] - 30), (cx(4, 0.7), Y[2] - 30)), lpos=(0, -12))
# ── 보충 인터락 (PAUSED) — 2열 2행은 비어 있어 세로로 지난다
p2.edge(scoop, paused, 'SCOOP_EMPTY 4회 = MATERIAL_EMPTY → REFILL\n_resume = 이 scoop 요청', color=WARM,
        exit=(0.15, 1), entry=(0.15, 0), points=((cx(2, 0.15), Y[3] - 20),), lpos=(0.65, 0))
p2.edge(paused, scoop, 'interlock EXIT → _resume 재실행\n(REFILL 은 개입으로 세지 않는다)', color=OK,
        exit=(0.85, 0), entry=(0.85, 1), points=((cx(2, 0.85), Y[3] - 20),), lpos=(0.65, 0))
# ── QA 판정 (DEVIATION)
p2.edge(dev, ret, 'APPROVED (원료 일탈) → 결과에 남기고 스쿱 반납', color=OK,
        exit=(0.3, 0), entry=(0.85, 1), points=((cx(4, 0.3), 900), (cx(1, 0.85), 900)), lpos=(0.2, 14))
p2.edge(dev, finish, 'APPROVED\n(VERIFY 불일치)', color=OK, exit=(0.9, 0), entry=(0.9, 1), lpos=(0, 30))
p2.edge(dev, disc, 'DISCARDED\n스쿱 든 채면 먼저 반납', color=RED, lpos=(0, -22))
# ── 강제 개입 (ERROR) — 왼쪽 복도
p2.edge(pickc, err, 'GRIP_FAIL 4회 (FORCED)', color=RED,
        exit=(0.15, 1), entry=(0.3, 0), points=((cx(2, 0.15), 230), (X[0] - 25, 230), (X[0] - 25, Y[3] - 30), (cx(0, 0.3), Y[3] - 30)), lpos=(-0.6, -14))
p2.edge(picks, err, 'GRIP_FAIL 4회 (FORCED)', color=RED,
        exit=(0.15, 1), entry=(0.15, 0), points=((cx(0, 0.15), Y[3] - 60),), lpos=(0.3, -40))
p2.edge(selfc, err, 'measure invalid (TODO)', color=RED,
        exit=(0, 0.7), entry=(0.7, 0), points=((cx(0, 0.7), Y[0] + 45),), lpos=(0.5, -14))
p2.note(1440, 120, 380, 150, '루프 게이트 (FSM 밖, D-21 추가 7)\n\nskill_node 가 event NUDGE 를 쏘면 run_loop 가 다음 요청 전에 멈추고\nstate.mode = PAUSED(note="NUDGE") 발행, 두 번째 NUDGE 로 재개.\n인터락 중·계량 대기 중에만 감지된다 (블로킹 movel 중은 두산 충돌 감지 담당)')
p2.note(1660, 880, 240, 270, '일탈 정책 (deviation.py)\n\nGRIP_FAIL  3회 RETRY → FORCED\nSCOOP_EMPTY  3회 RETRY → REFILL\nMATERIAL_EMPTY  즉시 REFILL\nOVERFILL / TIMEOUT  즉시 QA\nWEIGH_INVALID  2회 RETRY → QA\nVERIFY_MISMATCH [v1.2]  즉시 QA (계측)\nBATCH_OUT_OF_SPEC [v1.2]  즉시 QA (규격)\nSLIP 2 · SAFETY_SWITCH 1 · FORCE_LIMIT 1 → FORCED\nWRONG_TOOL [v1.2]  즉시 QA\n\ncount = 같은 배치·같은 스텝·같은 kind')
p2.note(40, 1180, 1860, 90, 'D-22 원료 1종 = 6단계: 빈 스쿱 계량 → 퍼올림 → 붓기 전 계량(퍼낸 양 → 붓기 비율, 초과 예방) → 붓기 → 붓기 후 계량(잔량 → 투입량 누적 → 판정) → 반납. 로봇이 저울이라 스쿱을 든 채 재는 것이 가장 싸고, 용기 계량(들어 올림)은 그리퍼가 비어야 해서 배치 끝 VERIFY 한 번.\n범례   파랑 = 정상 경로   주황 = 일탈·재시도   청록 = 복구·완료   빨강 = 강제 개입·폐기(종료)   회색 = 대기·재계량\n전이표 밖 조합은 on_result 가 RuntimeError("전이 없음") 를 던진다 — 새 상태·kind 를 넣으면 전이와 테스트(test_process_fsm.py)를 같이 넣는다')

# ───────────────────────── 페이지 3: 요청 ↔ 스킬 번역 ─────────────────────────
p3 = Page('3 요청↔스킬 번역 (_execute)')
p3.box(40, 20, 1860, 40, 'process_node._execute — FSM 요청 kind → 스킬 호출 → 결과 dict', fill='#FFFFFF', stroke='none', bold=True, size=18, align='left')
kinds = [
 ('measure', '—', 'measure_force(samples 0, settle 0)', "{'valid', 'fz_std_n'}"),
 ('carry', 'src · dst · slot · target=cup', 'move(src,ABOVE) → move(src,AT) → set_gripper(close, cup_width) →\nmove(src,ABOVE) → move(dst,ABOVE) → move(dst,AT) → set_gripper(open) → move(dst,ABOVE)\n파지 실패면 dst 로 가지 않고 즉시 반환', "{'grip_inferred'}"),
 ('weigh', 'station · tare_g', 'WeighContainer act(tare_g) — 고정 scale의 용기를 들어 잰다 (TARE · VERIFY). 그리퍼가 비어 있어야 한다 + weight 토픽 발행', "{'gross_g','net_g','std_g','valid','subject=container'}"),
  ('weigh_scoop', 'station · tare_g(빈 스쿱)', 'WeighHeld act [v1.2 확정] — 들고 있는 스쿱을 계량 자세로 → 읽기 (SCOOP_TARE · WEIGH_SCOOP · WEIGH_RESIDUAL 이 같은 요청)\n계량 후 계량 자세에 머문다 (복귀 없음) · 빈 그리퍼면 success=false', "{'gross_g','net_g','std_g','valid','subject=scoop'}"),
 ('move', 'station · approach', 'move_to_station(station, ABOVE/AT)', "{'success','reached'} → state.station"),
 ('grip', 'close · target(scoop/cup)', 'set_gripper(close, width = scoop_width | cup_width, force)', "{'grip_inferred','final_width_mm'}"),
 ('scoop', 'material_id · attempt · fraction', 'scoop(material_id, attempt)   fraction 은 담그기 깊이 힌트', "{'contact_detected'}"),
 ('pour', 'station · fraction', 'pour(station, fraction)   fraction = min(1, 부족량/퍼낸 양) — WEIGH_SCOOP 가 정함', "{'success'}"),
 ('safe', 'reason · then', 'safe_pose(reason)   전이는 then 이 정한다', '{}'),
 ('wait_qa', 'deviation', '스킬 없음 — _qa.clear(); _qa.wait()', "{'decision': APPROVED|DISCARDED}"),
 ('wait_interlock', '—', '스킬 없음 — _interlock_exit.clear(); wait()', '{}'),
]
y = 90
p3.box(40, y, 160, 30, 'kind', fill=INK, stroke=INK, font='#FFFFFF', bold=True, shape='rounded=0;')
p3.box(200, y, 260, 30, '요청 필드', fill=INK, stroke=INK, font='#FFFFFF', bold=True, shape='rounded=0;')
p3.box(460, y, 760, 30, '노드가 부르는 스킬 (skill_node)', fill=INK, stroke=INK, font='#FFFFFF', bold=True, shape='rounded=0;')
p3.box(1220, y, 380, 30, '돌려줄 결과 dict (FSM 이 읽는 키)', fill=INK, stroke=INK, font='#FFFFFF', bold=True, shape='rounded=0;')
y += 30
for k, f, s, r in kinds:
    h = 70 if '\n' in s else 44
    fill = WARMS if k.startswith('wait') else (ACCS if k in ('carry',) else '#FFFFFF')
    p3.box(40, y, 160, h, k, fill=fill, stroke=GRAY, bold=True, shape='rounded=0;', align='left')
    p3.box(200, y, 260, h, f, fill=fill, stroke=GRAY, shape='rounded=0;', align='left', size=11)
    p3.box(460, y, 760, h, s, fill=fill, stroke=GRAY, shape='rounded=0;', align='left', size=11)
    p3.box(1220, y, 380, h, r, fill=fill, stroke=GRAY, shape='rounded=0;', align='left', size=11)
    y += h
p3.note(40, y + 30, 760, 130, '_call_action(client, goal) 보조 함수 하나로 통일\n  1) client.wait_for_server(5 s)   2) send_goal_async → goal_handle   3) get_result_async → Future\n  4) Event 로 동기 대기 (run_loop 는 executor 스레드가 아니므로 spin 하지 말 것 — MultiThreadedExecutor 가 콜백을 돌린다)\n  5) 인터락 ENTER 가 오면 goal_handle.cancel_goal_async()  → 취소 결과 후 safe_pose\n\n실패(success=false)는 표에 없다 → FORCE_LIMIT 일탈로 _deviate(RETRY 1회 → FORCED) 권장')
p3.note(820, y + 30, 780, 145, '발행 시점\n  weight            weigh 결과마다 (TARE 포함)\n  scoop_cycle       정상은 WEIGH_RESIDUAL 후, 실패는 실패 확정 시\n  dispense_result   원료가 끝날 때 — WEIGH_RESIDUAL 에서 DONE 또는 DEVIATION 으로 갈 때\n  deviation         _deviate() 마다 + QA 판정 후 decision·operator_id 채워 같은 deviation_id 로 재발행\n  event             BATCH_START(product) · STEP(전이) · INTERLOCK_ENTER/EXIT · INTERVENTION_FORCED(ERROR) · BATCH_END\n  state             0.5 s 타이머 + 전이 직후 1회')


# ───────────────────────── 페이지 4: 상태별 노드·토픽 흐름 (A·B·D) ─────────────────────────
p4 = Page('4 상태별 노드·토픽 흐름')
p4.box(40, 20, 1860, 40, '상태마다 무엇이 들어오고(HMI 서비스 · NUDGE 이벤트) 무엇이 나가는가(스킬 호출 · 도징 라이브러리 · 기록 토픽) — 2페이지 전이도를 세로로 편 것. 전이 조건은 2페이지', fill='#FFFFFF', stroke='none', bold=True, size=16, align='left')
LX = {'rec': (40, 360), 'hmi': (440, 300), 'st': (800, 220), 'skill': (1080, 380), 'dos': (1500, 360)}   # 레인 x, 폭
LANE_HDR = {
    'rec':   ('D record_node — 토픽 (나감) → SQLite\n/cell/state · weight · scoop_cycle · dispense_result · deviation · event', GRAYS, GRAY),
    'hmi':   ('D hmi_web_node — Service (들어옴)\n/cell/submit_order · qa_decision · interlock', GRAYS, GRAY),
    'st':    ('C process_node\n상태 (CellState.step)', ACCS, ACC),
    'skill': ('A skill_node — Action / Service (나감 → 결과 돌아옴)\n/cell/move_to_station · scoop · pour · weigh_container · weigh_held[v1.2] · set_gripper · measure_force · safe_pose', WARMS, WARM),
    'dos':   ('B gmp_dosing — 라이브러리 (import, ROS 없음)\ncore/scale.py · core/dosing.py', OKS, OK),
}
Y0, PITCH = 150, 150
for k, (x, w) in LX.items():
    t, fill, stroke = LANE_HDR[k]
    p4.box(x, 70, w, 60, t, fill=fill, stroke=stroke, bold=True, size=11)
    p4.box(x, Y0 - 10, w, 19 * PITCH, '', fill='#FFFFFF', stroke=stroke, dashed=True, shape='rounded=0;')
KIND = {'n': (ACCS, ACC), 'p': (WARMS, WARM), 'e': (REDS, RED), 'd': (OKS, OK), 'i': (GRAYS, GRAY)}
# (상태, 종류, HMI→, →skill, →dosing, →record, skill→state 역방향 여부)
ROWS = [
 ('IDLE', 'i', 'SubmitOrder srv\nrecipe → accepted, batch_id\nevent HMI_ORDER(actor) → audit', None,
  'recipe.parse() / from_msg() 검증\nscale.resolvable(target, tol)', 'state IDLE→ACCEPTED\nevent BATCH_START(product)', False),
 ('SELF_CHECK', 'n', None, 'MeasureForce srv (빈 그리퍼)\nsamples, settle_s → fz_mean, fz_std, valid', None, 'state · event STEP', False),
 ('PICK_CONTAINER', 'n', None, 'carry = MoveToStation act ×4 + SetGripper srv ×2\nmagazine(slot) → scale, cup_width\n→ grip_inferred', None, 'state\ndeviation(GRIP_FAIL 시)', False),
 ('TARE', 'n', None, 'WeighContainer act (고정 scale 용기 들어)\ntare 0 → reading(gross, std, valid)', 'scale.set_tare(gross)', 'weight(gross, valid=…) · state', False),
 ('PICK_SCOOP', 'n', None, 'MoveToStation act (scoop_N, AT)\nSetGripper srv (close, scoop_width, force)\n→ grip_inferred, final_width_mm', None, 'state\ndeviation(GRIP_FAIL · WRONG_TOOL[v1.2])', False),
 ('SCOOP_TARE', 'n', None, 'WeighHeld act [v1.2] (빈 스쿱, 든 채)\n→ gross, std, valid', None, 'weight(스쿱 풍량, subject=scoop) · state', False),
 ('SCOOP', 'n', None, 'Scoop act\nmaterial_id, attempt → contact_detected', None, 'state\ndeviation(SCOOP_EMPTY · MATERIAL_EMPTY)', False),
 ('WEIGH_SCOOP', 'n', None, 'WeighHeld act [v1.2] (붓기 전, 든 채)\n→ gross, valid', '_pour_fraction(need, scooped)\n= min(1, 부족량/퍼낸 양)  [B 로 이관 예정]', 'weight(퍼낸 양) · state', False),
 ('POUR', 'n', None, 'Pour act\nfraction → success (고정 scale)', None, 'state', False),
 ('WEIGH_RESIDUAL', 'n', None, 'WeighHeld act [v1.2] (붓기 후, 든 채)\n→ gross, valid', 'decide(target, actual, tol, attempts,\nvalid, invalid, cfg)\n→ DONE / SCOOP(fraction) / DEVIATION(kind)',
  'weight(잔량) · scoop_cycle(시도 1건) · state\ndispense_result (DONE·DEVIATION 시)\ndeviation(OVERFILL · TIMEOUT · WEIGH_INVALID)', False),
 ('RETURN_SCOOP', 'n', None, 'MoveToStation act (scoop_N, AT — 원료통 아래)\nSetGripper srv (open)', None, 'state', False),
 ('VERIFY', 'n', None, 'WeighContainer act (고정 scale 용기 들어, 배치 1회)\ntare_g → reading(net, subject=container)', '① Σ(target×tol) — 레시피 총량 대조\n② min_resolvable_g — Σ투입량 대조', 'weight(net) · state\ndeviation(VERIFY_MISMATCH[v1.2])', False),
 ('FINISH', 'n', None, 'carry scale → output_tray(slot)\nSafePose srv', None, 'state DONE · event BATCH_END\n(record_node 가 JSON 내보내기)', False),
 ('DONE', 'd', None, None, None, '(발행 없음 — HMI 는 DB 를 읽어\n이력·KPI 표시)', False),
 ('DEVIATION', 'p', 'QaDecision srv\nAPPROVE / DISCARD, operator_id\nevent HMI_QA_APPROVE/DISCARD → audit', '(로봇 대기 — 호출 없음)', None, 'deviation 재발행\n(decision, operator_id, 같은 id) · state', False),
 ('PAUSED (REFILL)', 'p', 'InterlockRequest srv\nENTER(reason) → granted / EXIT\nevent HMI_INTERLOCK_ENTER/EXIT → audit', 'SafePose srv (ENTER 시)\n진행 중 Action 은 cancel_goal 먼저 (I-004)', None, 'event INTERLOCK_ENTER/EXIT\nstate PAUSED', False),
 ('PAUSED (NUDGE)', 'p', None, 'event NUDGE ← skill_node 발행\n(get_tool_force 폴링, D-21)\nprocess 는 구독 → 루프 게이트 토글', None, 'state PAUSED(note NUDGE)\nevent NUDGE (skill 이 낸 것을 record 가 저장)', True),
 ('ERROR', 'e', None, 'SafePose srv (then None)', None, 'event INTERVENTION_FORCED\nstate ERROR', False),
 ('DISCARDED', 'e', None, 'MoveToStation + SetGripper(open) (스쿱 반납)\ncarry scale → reject_bin', None, 'state DONE(DISCARDED)\nevent BATCH_END', False),
]
def lane_box(k, y, text, h, fill, stroke):
    x, w = LX[k]
    return p4.box(x + 10, y, w - 20, h, text, fill=fill, stroke=stroke, size=11, align='left')
for i, (name, kind, hmi_t, skill_t, dos_t, rec_t, rev) in enumerate(ROWS):
    y = Y0 + i * PITCH
    fill, stroke = KIND[kind]
    sx, sw = LX['st']
    st = p4.box(sx + 10, y, sw - 20, 50, name, fill=fill, stroke=stroke, bold=True, size=12)
    lines = lambda t: 16 + 15 * (t.count('\n') + 1)
    corridor = y + 95            # 같은 행에서 상자를 건너뛰어야 할 때 쓰는 아래 복도
    if hmi_t:
        b = lane_box('hmi', y, hmi_t, lines(hmi_t), GRAYS, GRAY)
        p4.edge(b, st, 'srv 요청', color=GRAY, exit=(1, 0.5), entry=(0, 0.3), lpos=(0, -10))
    if skill_t:
        b = lane_box('skill', y, skill_t, lines(skill_t), WARMS, WARM)
        if rev:
            p4.edge(b, st, 'event (구독)', color=WARM, exit=(0, 0.5), entry=(1, 0.5), lpos=(0, -10))
        else:
            p4.edge(st, b, '호출 → 결과', color=WARM, exit=(1, 0.5), entry=(0, 0.5), lpos=(0, -10))
    if dos_t:
        b = lane_box('dos', y, dos_t, lines(dos_t), OKS, OK)
        bx, bw = LX['dos']
        if skill_t:              # A 상자를 건너뛴다 — 아래 복도로
            p4.edge(st, b, 'import 호출', color=OK, exit=(1, 0.9), entry=(0.5, 1),
                    points=((sx + sw + 15, corridor), (bx + bw / 2, corridor)), lpos=(0.35, 12))
        else:
            p4.edge(st, b, 'import 호출', color=OK, exit=(1, 0.7), entry=(0, 0.5), lpos=(0, -10))
    if rec_t:
        b = lane_box('rec', y, rec_t, lines(rec_t), GRAYS, GRAY)
        rx, rw = LX['rec']
        if hmi_t:                # HMI 상자를 건너뛴다 — 아래 복도로
            p4.edge(st, b, 'topic 발행', color=GRAY, exit=(0, 0.9), entry=(0.5, 1),
                    points=((sx - 5, corridor), (rx + rw / 2, corridor)), lpos=(-0.35, 12))
        else:
            p4.edge(st, b, 'topic 발행', color=GRAY, exit=(0, 0.5), entry=(1, 0.5), lpos=(0, -10))
p4.note(40, Y0 + 19 * PITCH + 20, 1820, 90,
        '읽는 법   왼쪽 = 나가는 토픽(D record_node 가 SQLite 에 씀, HMI 는 DB 를 읽는다) · 가운데 왼쪽 = HMI 가 부르는 서비스(들어옴) · 오른쪽 = process 가 부르는 A 스킬(요청 → 결과) · 맨 오른쪽 = B 라이브러리 함수(같은 프로세스 안, 토픽 없음)\n'
        'WeighHeld · VERIFY_MISMATCH · BATCH_OUT_OF_SPEC · WRONG_TOOL 은 계약 v1.2 (9/18 확정, I-007 해소). state 는 전 상태에서 2 Hz + 전이 직후 발행. 모든 발행은 /cell/ 아래, QoS 는 docs/interfaces.md 3절.\n'
        '이 페이지의 상자 문구는 docs/process_flow.md 1·2·4절과 같은 내용이다 — 바뀌면 tools/make_process_drawio.py 를 고쳐 다시 생성한다')

doc = '<mxfile host="app.diagrams.net" pages="4">' + p1.xml() + p2.xml() + p3.xml() + p4.xml(h=3300) + '</mxfile>'
import xml.etree.ElementTree as ET
root = ET.fromstring(doc)                                  # well-formed 검사
ET.indent(root, space='  ')
OUT.write_text(ET.tostring(root, encoding='unicode'), encoding='utf-8')
print(OUT, OUT.stat().st_size // 1024, 'KB')
