#!/usr/bin/env python3
"""docs/process_flow.md 를 draw.io 파일로 — docs/diagrams/process_flow.drawio (3 페이지).
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
    def edge(self, a, b, label='', color=INK, dashed=False, points=(), exit=None, entry=None, width=1.5):
        i = self._id()
        st = f'edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;endArrow=block;endFill=1;strokeColor={color};strokeWidth={width};fontColor={color};fontSize=10;labelBackgroundColor=#FFFFFF;'
        if dashed: st += 'dashed=1;'
        if exit: st += f'exitX={exit[0]};exitY={exit[1]};exitDx=0;exitDy=0;'
        if entry: st += f'entryX={entry[0]};entryY={entry[1]};entryDx=0;entryDy=0;'
        pts = ''.join(f'<mxPoint x="{px}" y="{py}"/>' for px, py in points)
        geo = f'<mxGeometry relative="1" as="geometry">' + (f'<Array as="points">{pts}</Array>' if pts else '') + '</mxGeometry>'
        self.cells.append(f'<mxCell id="{i}" value="{escape(label)}" style="{st}" edge="1" parent="1" source="{a}" target="{b}">{geo}</mxCell>')
        return i
    def xml(self, w=1654, h=1169):
        body = ''.join(self.cells)
        return (f'<diagram id="{self.name}" name="{escape(self.name)}"><mxGraphModel dx="1400" dy="900" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="{w}" pageHeight="{h}">'
                f'<root><mxCell id="0"/><mxCell id="1" parent="0"/>{body}</root></mxGraphModel></diagram>')

# ───────────────────────── 페이지 1: 노드 입출력 ─────────────────────────
p1 = Page('1 노드 입출력')
p1.box(40, 20, 1560, 40, 'gmp_process — process_node 입출력 (계약 v1.1 · ns /cell)', fill='#FFFFFF', stroke='none', bold=True, size=18, align='left')
hmi = p1.box(40, 140, 220, 420, 'hmi_web_node (D)\n\n웹 HMI · Flask :5000\n\n주문 제출\nQA 승인 / 폐기\n인터락 ENTER / EXIT', fill=GRAYS, stroke=GRAY, bold=True)
proc = p1.box(420, 100, 700, 560, 'process_node (C)', fill='#FFFFFF', stroke=ACC, bold=True, size=14)
cb = p1.box(450, 150, 300, 200, 'rclpy 콜백 스레드\n\n_srv_submit → FSM 생성, run_loop 시작\n_srv_qa → _qa_decision 저장, _qa.set()\n_srv_interlock → ENTER: cancel→safe_pose→granted\n                        EXIT: _interlock_exit.set()\nevent(NUDGE) 구독 → 게이트 토글\n\n콜백은 값만 저장한다. 로봇을 부르지 않는다', fill=ACCS, align='left', size=11)
loop = p1.box(790, 150, 300, 200, 'run_loop 스레드 (배치마다 1개)\n\nreq = fsm.start()\nwhile req:\n    res = _execute(req)   ← 스킬 한 번에 하나\n    req = fsm.on_result(req, res)\n    발행(state·weight·result·deviation·event)\n\nwait_qa / wait_interlock 는 Event.wait()', fill=ACCS, align='left', size=11)
fsm = p1.box(450, 390, 640, 240, 'core/process_fsm.py — ProcessFSM (ROS 비의존, pytest)\n\nstate · mode · idx(원료) · tare_g · cur(ItemRun: attempts·invalid·actual·verdict) · results · deviations · _counts · _resume · slot\n\nstart() → 첫 요청     on_result(req, res) → 다음 요청 dict 또는 None(끝)\n_deviate(kind, step) → deviation.py policy(kind, count) → RETRY / REFILL / QA / FORCED\n_carry(src, dst) → {"kind":"carry", src, dst, slot}\n\n의존: gmp_dosing.core.dosing.decide(target, net, tol, attempts, valid, invalid, cfg) → DONE / SCOOP(fraction) / DEVIATION(kind)\n        core/recipe.py parse() → RecipeSpec(product, items[Item(material_id, target_g, tol_pct, grade, scoop_id)])', fill=OKS, stroke=OK, align='left', size=11)
p1.edge(cb, loop, 'Event / 플래그', color=GRAY, dashed=True)
p1.edge(loop, fsm, 'dict 요청 ↔ dict 결과', color=OK)
skill = p1.box(1280, 100, 320, 560, 'skill_node (A)\n\n로봇을 만지는 유일한 노드\nDSR 워커 스레드 1개 — 직렬\n\nAction 서버 4\n  move_to_station · scoop · pour · weigh_container\nService 서버 3\n  grip · measure_force · safe_pose\n\n발행: gripper_state · event(NUDGE)', fill=GRAYS, stroke=GRAY, bold=True)
rec = p1.box(420, 720, 700, 110, 'record_node (D) — SQLite 단일 기록자        hmi_web_node (D) — 화면 표시\n\n구독: state · weight · dispense_result · deviation · event', fill=GRAYS, stroke=GRAY, bold=True)
# HMI → process (Service)
p1.edge(hmi, proc, 'Service  submit_order (SubmitOrder: Recipe → accepted, batch_id)\n실행 중이면 accepted=false', color=ACC, exit=(1, 0.2), entry=(0, 0.12))
p1.edge(hmi, proc, 'Service  qa_decision (batch_id, deviation_id, decision, operator_id → accepted)\nDEVIATION 아니면 거부', color=ACC, exit=(1, 0.45), entry=(0, 0.3))
p1.edge(hmi, proc, 'Service  interlock (ENTER=1 / EXIT=2, reason → granted)\nENTER 는 safe_pose 성공 후 granted', color=ACC, exit=(1, 0.7), entry=(0, 0.48))
p1.edge(hmi, proc, 'Action  run_batch (RunBatch) — CLI·시험용, 우선순위 낮음', color=GRAY, dashed=True, exit=(1, 0.92), entry=(0, 0.66))
# process → skill
p1.edge(proc, skill, 'Action  move_to_station (station_id, approach ABOVE/AT, vel_scale → success, reached)', color=WARM, exit=(1, 0.1), entry=(0, 0.1))
p1.edge(proc, skill, 'Action  scoop (material_id, attempt → success, contact_detected)', color=WARM, exit=(1, 0.22), entry=(0, 0.22))
p1.edge(proc, skill, 'Action  pour (target_station, fraction → success)', color=WARM, exit=(1, 0.34), entry=(0, 0.34))
p1.edge(proc, skill, 'Action  weigh_container (container_station, tare_g → reading: gross/net/std/valid)', color=WARM, exit=(1, 0.46), entry=(0, 0.46))
p1.edge(proc, skill, 'Service  grip (close, width_mm, force_n, timeout_s → success, final_width_mm, grip_inferred)', color=WARM, exit=(1, 0.58), entry=(0, 0.58))
p1.edge(proc, skill, 'Service  measure_force (samples, settle_s → fz_mean_n, fz_std_n, valid)', color=WARM, exit=(1, 0.7), entry=(0, 0.7))
p1.edge(proc, skill, 'Service  safe_pose (reason → success)', color=WARM, exit=(1, 0.82), entry=(0, 0.82))
p1.edge(skill, proc, 'Topic  event  code=NUDGE (사람 접촉, D-21) → 루프 게이트', color=RED, dashed=True, exit=(0, 0.94), entry=(1, 0.94))
# process → record (Topics)
p1.edge(proc, rec, 'Topic  state (CellState, 0.5 s + 전이, TRANSIENT_LOCAL)', color=OK, exit=(0.1, 1), entry=(0.1, 0))
p1.edge(proc, rec, 'Topic  weight (WeightReading, weigh 마다)', color=OK, exit=(0.3, 1), entry=(0.3, 0))
p1.edge(proc, rec, 'Topic  dispense_result (원료 종료 시)', color=OK, exit=(0.5, 1), entry=(0.5, 0))
p1.edge(proc, rec, 'Topic  deviation (TRANSIENT_LOCAL, QA 후 재발행)', color=OK, exit=(0.7, 1), entry=(0.7, 0))
p1.edge(proc, rec, 'Topic  event (BATCH_START/END, STEP, INTERLOCK_*, INTERVENTION_FORCED)', color=OK, exit=(0.9, 1), entry=(0.9, 0))
p1.note(40, 600, 340, 230, '읽는 법\n\n파란 실선 = HMI 가 부르는 Service (process 가 서버)\n주황 = process 가 부르는 스킬 (skill_node 가 서버)\n청록 = process 가 발행하는 Topic\n빨강 점선 = skill_node → process 구독\n\n원칙: 스킬은 한 번에 하나 · 콜백은 값만 저장 · FSM 은 로봇을 모른다')
p1.note(1280, 720, 320, 110, '미합의 1건 (A 와)\n용기 반송 슬롯 번호를 MoveToStation 에 어떻게 넘길지\n— station_id 접미사 "magazine#1"  vs  계약 v1.2 uint8 slot')

# ───────────────────────── 페이지 2: 상태 전이도 ─────────────────────────
p2 = Page('2 상태 전이도')
p2.box(40, 20, 1560, 40, 'ProcessFSM 상태 전이도 — core/process_fsm.py (상태 이름 = CellState.step)', fill='#FFFFFF', stroke='none', bold=True, size=18, align='left')
W, H = 170, 60
def S(x, y, name, sub='', kind='n'):
    fill, stroke = {'n': (ACCS, ACC), 'p': (WARMS, WARM), 'e': (REDS, RED), 'd': (OKS, OK), 'i': (GRAYS, GRAY)}[kind]
    return p2.box(x, y, W, H, name + ('\n' + sub if sub else ''), fill=fill, stroke=stroke, bold=True, size=12)
idle = S(40, 120, 'IDLE', 'submit_order 대기', 'i')
selfc = S(280, 120, 'SELF_CHECK', 'req: measure')
pickc = S(520, 120, 'PICK_CONTAINER', 'req: carry magazine→scale')
tare = S(780, 120, 'TARE', 'req: weigh(tare 0)')
picks = S(280, 320, 'PICK_SCOOP', 'req: move(scoop_rack) → grip')
scoop = S(520, 320, 'SCOOP', 'req: scoop(material, attempt)')
pour = S(780, 320, 'POUR', 'req: pour(scale, fraction)')
weigh = S(1040, 320, 'WEIGH', 'req: weigh(scale, tare) → decide()')
ret = S(520, 520, 'RETURN_SCOOP', 'req: move(scoop_rack) → grip(open)')
finish = S(1040, 520, 'FINISH', 'req: carry scale→output_tray')
done = S(1300, 520, 'DONE', 'event BATCH_END', 'd')
paused = S(520, 720, 'PAUSED', 'req: safe → wait_interlock', 'p')
dev = S(1040, 720, 'DEVIATION', 'req: wait_qa (QA 원격 판정)', 'p')
disc = S(1300, 720, 'DISCARDED', 'req: carry scale→reject_bin', 'e')
err = S(40, 720, 'ERROR', 'req: safe(then None)\nevent INTERVENTION_FORCED', 'e')
# 정상 경로
p2.edge(idle, selfc, 'submit_order 수락\nbatch_id 발급 · event BATCH_START', color=ACC)
p2.edge(selfc, pickc, 'measure valid', color=ACC)
p2.edge(pickc, tare, 'carry grip_inferred=true', color=ACC)
p2.edge(tare, picks, 'tare_g 저장 · scale.set_tare()\ncur = 원료 0 · weight 발행', color=ACC, exit=(0.5, 1), entry=(0.5, 0), points=((865, 260), (365, 260)))
p2.edge(picks, scoop, 'grip_inferred=true\nattempts=1', color=ACC)
p2.edge(scoop, pour, 'contact_detected=true\nfraction 전달', color=ACC)
p2.edge(pour, weigh, '', color=ACC)
p2.edge(weigh, ret, 'OK (|err| ≤ tol)\ndispense_result 발행', color=OK, exit=(0.5, 1), entry=(1, 0.5), points=((1125, 550),))
p2.edge(weigh, scoop, 'UNDER, attempts<3\nreq scoop(fraction = 부족량/스쿱1회)', color=WARM, exit=(0.3, 0), entry=(0.7, 0), points=((1091, 290), (639, 290)))
p2.edge(weigh, weigh, 'INVALID (valid=false) ≤2 → 재계량', color=GRAY, exit=(0.85, 0), entry=(1, 0.3), points=((1185, 290), (1240, 290), (1240, 338)))
p2.edge(weigh, dev, 'OVER → OVERFILL\nUNDER 4회째 → TIMEOUT\nINVALID 3회 → WEIGH_INVALID', color=WARM, exit=(0.75, 1), entry=(0.75, 0), points=((1167, 620),))
p2.edge(ret, picks, 'grip(open) · idx+1\n다음 원료 있음', color=ACC, exit=(0, 0.5), entry=(0.5, 1), points=((365, 550),))
p2.edge(ret, finish, '마지막 원료였음', color=ACC)
p2.edge(finish, done, 'carry grip_inferred=true', color=OK)
# 일탈·복구
p2.edge(pickc, pickc, 'GRIP_FAIL ≤3 → 같은 carry 재시도', color=WARM, exit=(0.5, 0), entry=(0.85, 0), points=((605, 90), (665, 90)))
p2.edge(picks, picks, 'GRIP_FAIL ≤3 → grip 재시도\n[추가1] 폭 불일치 → WRONG_TOOL → DEVIATION', color=WARM, exit=(0.2, 0), entry=(0.6, 0), points=((314, 290), (382, 290)))
p2.edge(scoop, scoop, 'SCOOP_EMPTY ≤3 → scoop 재시도', color=WARM, exit=(0.2, 0), entry=(0.55, 0), points=((554, 290), (613, 290)))
p2.edge(scoop, paused, 'SCOOP_EMPTY 4회 = MATERIAL_EMPTY → REFILL\n_resume = 이 scoop 요청', color=WARM, exit=(0.3, 1), entry=(0.3, 0), points=((571, 660),))
p2.edge(paused, scoop, 'interlock EXIT → _resume 재실행\n(REFILL 은 개입으로 세지 않는다)', color=OK, exit=(0.7, 0), entry=(0.7, 1), points=((639, 660),))
p2.edge(dev, ret, 'APPROVED → 결과에 남기고 스쿱 반납', color=OK, exit=(0, 0.5), entry=(0.5, 1), points=((605, 750),))
p2.edge(dev, disc, 'DISCARDED → 용기째 폐기함', color=RED)
p2.edge(pickc, err, 'GRIP_FAIL 4회 (FORCED)', color=RED, exit=(0.15, 1), entry=(0.5, 0), points=((545, 200), (200, 200), (200, 690), (125, 690)))
p2.edge(picks, err, 'GRIP_FAIL 4회 (FORCED)', color=RED, exit=(0, 0.5), entry=(1, 0.3), points=((200, 350), (200, 738)))
p2.edge(selfc, err, 'measure invalid (TODO)', color=RED, exit=(0, 0.5), entry=(0.5, 0), points=((125, 150),))
p2.note(1300, 120, 300, 160, '루프 게이트 (FSM 밖, D-21 추가 7)\n\nskill_node 가 event NUDGE 를 쏘면\nrun_loop 가 다음 요청 전에 멈추고\nstate.mode = PAUSED(note="NUDGE") 발행,\n두 번째 NUDGE 로 재개.\n\n인터락 중·계량 대기 중에만 감지된다\n(블로킹 movel 중은 두산 충돌 감지 담당)')
p2.note(1300, 300, 300, 180, '일탈 정책 (deviation.py)\n\nGRIP_FAIL      3회 RETRY → FORCED\nSCOOP_EMPTY   3회 RETRY → REFILL\nMATERIAL_EMPTY  즉시 REFILL\nOVERFILL / TIMEOUT  즉시 QA\nWEIGH_INVALID  2회 RETRY → QA\nSLIP 2 · SAFETY_SWITCH 1 · FORCE_LIMIT 1 → FORCED\nWRONG_TOOL [v1.2]  즉시 QA\n\ncount = 같은 배치·같은 스텝·같은 kind')
p2.note(40, 860, 1560, 70, '범례   파랑 = 정상 경로   주황 = 일탈·재시도   청록 = 복구·완료   빨강 = 강제 개입·폐기(종료)   회색 = 대기·재계량\n전이표 밖 조합은 on_result 가 RuntimeError("전이 없음") 를 던진다 — 새 상태·kind 를 넣으면 전이와 테스트(test_process_fsm.py)를 같이 넣는다')

# ───────────────────────── 페이지 3: 요청 ↔ 스킬 번역 ─────────────────────────
p3 = Page('3 요청↔스킬 번역 (_execute)')
p3.box(40, 20, 1560, 40, 'process_node._execute — FSM 요청 kind → 스킬 호출 → 결과 dict', fill='#FFFFFF', stroke='none', bold=True, size=18, align='left')
kinds = [
 ('measure', '—', 'measure_force(samples 0, settle 0)', "{'valid', 'fz_std_n'}"),
 ('carry', 'src · dst · slot · target=cup', 'move(src,ABOVE) → move(src,AT) → grip(close, cup_width) →\nmove(src,ABOVE) → move(dst,ABOVE) → move(dst,AT) → grip(open) → move(dst,ABOVE)\n파지 실패면 dst 로 가지 않고 즉시 반환', "{'grip_inferred'}"),
 ('weigh', 'station · tare_g', 'weigh_container(station, tare_g)  + weight 토픽 발행', "{'gross_g','net_g','std_g','valid'}"),
 ('move', 'station · approach', 'move_to_station(station, ABOVE/AT)', "{'success','reached'} → state.station"),
 ('grip', 'close · target(scoop/cup)', 'grip(close, width = scoop_width | cup_width, force)', "{'grip_inferred','final_width_mm'}"),
 ('scoop', 'material_id · attempt · fraction', 'scoop(material_id, attempt)', "{'contact_detected','fraction'(요청값 그대로)}"),
 ('pour', 'station · fraction', 'pour(station, fraction)', "{'success'}"),
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
p3.note(820, y + 30, 780, 130, '발행 시점\n  weight            weigh 결과마다 (TARE 포함)\n  dispense_result   원료가 끝날 때 — WEIGH 에서 DONE 또는 DEVIATION 으로 갈 때\n  deviation         _deviate() 마다 (fsm.deviations 길이 증가 감지) + QA 판정 후 decision·operator_id 채워 같은 deviation_id 로 재발행\n  event             BATCH_START(product) · STEP(전이) · INTERLOCK_ENTER/EXIT · INTERVENTION_FORCED(ERROR) · BATCH_END\n  state             0.5 s 타이머 + 전이 직후 1회')

doc = '<mxfile host="app.diagrams.net" type="device">' + p1.xml() + p2.xml() + p3.xml() + '</mxfile>'
OUT.write_text(doc, encoding='utf-8')
import xml.dom.minidom; xml.dom.minidom.parseString(doc)   # well-formed 검사
print(OUT, len(doc) // 1024, 'KB')
