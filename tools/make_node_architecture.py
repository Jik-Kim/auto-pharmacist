#!/usr/bin/env python3
"""현행 실물 DIO 구성: 동일 좌표로 편집용 drawio와 발표용 PNG 생성.

근거: cell.launch.py, robot.launch.py, 4개 앱 노드의 ROS 생성 코드,
common.yaml, docs/interfaces.md. 실행: python3 tools/make_node_architecture.py
의존: Pillow, Noto Sans CJK 글꼴. 도형/문구는 양쪽 산출물에 함께 반영한다.
"""
from pathlib import Path
import math
import xml.etree.ElementTree as ET
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parents[1] / 'docs/diagrams'
W, H = 2600, 2380
INK, MUTED = '#152B45', '#526579'
BLUE, TEAL, RED, GRAY = '#315DCF', '#087C80', '#C04452', '#778596'
FONT = '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'
image = Image.new('RGB', (W, H), '#FFFFFF')
d = ImageDraw.Draw(image)
doc = ET.Element('mxfile', host='app.diagrams.net')
page = ET.SubElement(doc, 'diagram', name='노드 구성과 인터페이스', id='node-architecture')
model = ET.SubElement(page, 'mxGraphModel', page='1', pageWidth=str(W), pageHeight=str(H), grid='1', gridSize='10')
root = ET.SubElement(model, 'root')
ET.SubElement(root, 'mxCell', id='0')
ET.SubElement(root, 'mxCell', id='1', parent='0')
serial = 1


def cell(style, **attrs):
    global serial
    serial += 1
    return ET.SubElement(root, 'mxCell', id=str(serial), parent='1', style=style, **attrs)


def box(x, y, w, h, fill='#FFFFFF', stroke='#D5DFE9', radius=18):
    d.rounded_rectangle((x,y,x+w,y+h), radius, fill=fill, outline=stroke, width=2)
    c = cell(f'rounded=1;arcSize=10;fillColor={fill};strokeColor={stroke};strokeWidth=2;', vertex='1', value='')
    ET.SubElement(c, 'mxGeometry', x=str(x), y=str(y), width=str(w), height=str(h), **{'as':'geometry'})
    return c.get('id')


def text(x,y,w,lines,size=26,color=INK):
    if isinstance(lines,str): lines=lines.split('\n')
    font = ImageFont.truetype(FONT,size)
    pitch = int(size*1.5)
    for i,line in enumerate(lines):
        assert d.textlength(line,font=font) <= w, (line,w)
        d.text((x,y+i*pitch),line,font=font,fill=color)
    c=cell(f'text;html=0;whiteSpace=wrap;align=left;verticalAlign=top;fontFamily=Noto Sans CJK KR;fontSize={size};fontColor={color};spacing=0;strokeColor=none;fillColor=none;',vertex='1',value='\n'.join(lines))
    ET.SubElement(c,'mxGeometry',x=str(x),y=str(y),width=str(w),height=str(len(lines)*pitch+8),**{'as':'geometry'})


def line(points,color=BLUE,both=False,dashed=False):
    for a,b in zip(points,points[1:]):
        if not dashed:
            d.line([a,b],fill=color,width=4)
        else:
            length=math.dist(a,b)
            for s in range(0,int(length),20):
                end=min(s+11,length)
                d.line([(a[0]+(b[0]-a[0])*s/length,a[1]+(b[1]-a[1])*s/length),(a[0]+(b[0]-a[0])*end/length,a[1]+(b[1]-a[1])*end/length)],fill=color,width=3)
    def arrow(a,b):
        angle=math.atan2(b[1]-a[1],b[0]-a[0]); r=16
        d.polygon([b,(b[0]-r*math.cos(angle-.45),b[1]-r*math.sin(angle-.45)),(b[0]-r*math.cos(angle+.45),b[1]-r*math.sin(angle+.45))],fill=color)
    arrow(points[-2],points[-1])
    if both: arrow(points[1],points[0])
    attrs={'edge':'1','value':''}
    # PNG와 drawio의 꺾임 좌표를 동일하게 유지한다.
    style=f'edgeStyle=none;rounded=0;endArrow=block;endFill=1;strokeColor={color};strokeWidth=3;'
    if both: style+='startArrow=block;startFill=1;'
    if dashed: style+='dashed=1;'
    c=cell(style,**attrs); g=ET.SubElement(c,'mxGeometry',relative='1',**{'as':'geometry'})
    for role,p in [('sourcePoint',points[0]),('targetPoint',points[-1])]:
        ET.SubElement(g,'mxPoint',x=str(p[0]),y=str(p[1]),**{'as':role})
    if len(points)>2:
        ar=ET.SubElement(g,'Array',**{'as':'points'})
        for p in points[1:-1]: ET.SubElement(ar,'mxPoint',x=str(p[0]),y=str(p[1]))


def tag(x,y,w,label,color=BLUE):
    box(x,y,w,46,'#FFFFFF','#FFFFFF',8)
    text(x+8,y+3,w-16,label,24,color)


def node(x,y,w,title,subtitle,body,fill='#EFF4FF',stroke=BLUE):
    box(x,y,w,170,fill,stroke)
    text(x+24,y+16,w-48,title,33)
    text(x+24,y+68,w-48,subtitle,22,stroke)
    text(x+24,y+109,w-48,body,23,MUTED)


text(80,35,2440,'조제 칭량 셀 · 노드 구성 및 통신 인터페이스',49)
text(82,112,2440,'현재 브랜치 구현 · 계약 v1.11 · 2026.09.30 · 실물 DIO 구성',25,MUTED)
box(55,185,2490,1145,'#F7F9FC','#C7D4E3')
text(85,199,1500,'작업 PC 1대  /  ROS 2 애플리케이션 네임스페이스: /cell',27)
text(85,246,1500,'화살표: 요청 / 발행 방향   ·   Service 응답, Action 피드백·결과는 역방향',22,MUTED)
# 연결선을 먼저 그린다.
line([(570,430),(1000,430)])
tag(650,375,300,'① 주문 · QA · 인터락')
line([(1480,430),(1900,430)])
tag(1530,375,340,'② 스킬 실행 · 안전복구')
line([(360,350),(360,315),(2150,315),(2150,350)],RED)
tag(1000,287,660,'③ HMI → A 직접 안전 요청',RED)
line([(570,490),(720,490),(720,735),(1000,735)],TEAL,both=True)
tag(735,635,255,'구독 ④⑤⑥',TEAL)
tag(735,680,255,'발행 ⑥',TEAL)
line([(1230,520),(1230,680)],TEAL,both=True)
tag(1250,545,340,'발행 ④⑥ / 구독 ⑥',TEAL)
line([(1900,490),(1730,490),(1730,755),(1480,755)],TEAL)
tag(1745,610,340,'발행 ⑤⑥',TEAL)
line([(1000,815),(570,815)],TEAL)
tag(640,760,320,'구독 ④⑥',TEAL)
line([(335,910),(335,1040)],GRAY)
tag(350,935,320,'배치 DB 단일 기록',GRAY)
line([(115,1100),(85,1100),(85,445),(115,445)],GRAY,dashed=True)
text(93,980,190,'읽기 전용',20,GRAY)
line([(2150,520),(2465,520),(2465,910),(2380,910)],GRAY,both=True)
tag(2050,610,400,'DSR API / ROS Service',GRAY)
line([(2380,965),(2505,965),(2505,1340),(2150,1340),(2150,1365)],GRAY,both=True)
tag(2170,1240,325,'Ethernet / DRFL SDK',GRAY)
node(115,350,455,'hmi_web_node','gmp_hmi  ·  D','주문 · QA · 모니터링 · 감사')
node(1000,350,480,'process_node','gmp_process  ·  C','배치 FSM · 스킬 호출 · 판정')
node(1900,350,500,'skill_node','gmp_skills  ·  A','단일 워커 · 로봇 제어 · 계량')
box(1000,680,480,215,'#EAF7F5',TEAL)
text(1024,700,432,'ROS 2 Topic 채널',32,TEAL)
text(1024,751,432,['④ 공정 데이터  ⑤ 파지 상태','⑥ 공통 이벤트','통신 경로 묶음 · 별도 노드 아님'],23)
node(115,740,455,'record_node','gmp_hmi  ·  D','토픽 기록 · 배치 JSON 내보내기')
box(115,1040,455,150,'#F0F3F7',GRAY)
text(140,1058,400,'SQLite  /  cell.db',31)
text(140,1110,400,['배치 · 계량 · 일탈 · 이벤트','events: append-only'],22,MUTED)
box(1000,1000,640,210,'#F2EFF9','#8976AC')
text(1025,1018,590,'gmp_dosing  ·  B',31)
text(1025,1070,590,['Python 라이브러리 · 독립 노드 아님','C: dosing.decide()  /  A: WeightModel','프로세스 내부 import · ROS 통신 없음'],24,MUTED)
node(1900,830,480,'DSR ROS 드라이버','/dsr01  ·  dsr_controller2','ROS 2 control + gmp_dsr_controller',fill='#F0F3F7',stroke=GRAY)
text(1900,1035,505,['skill_node의 DSR 클라이언트 사용','error Topic: RobotError → A','감도 조회: GetCollisionSensitivity','이동·외력·DIO: DSR_ROBOT2'],22,MUTED)
text(640,1238,1200,'웹 브라우저 ↔ HMI: HTTP :5000  |  PC 내부 노드 간: ROS 2 DDS',25,MUTED)
box(1800,1365,680,110,'#F0F3F7',GRAY)
text(1830,1380,620,'두산 제어기 · M0609 · RG2',29)
text(1830,1427,620,'컨트롤러 DIO 개폐·완료 신호 / 힘·상태',22,MUTED)
text(80,1380,1620,'통신 상세  /  아래 이름은 모두 /cell/ 접두사',32)
text(80,1434,1600,'기본 타입: gmp_interfaces/{action, srv, msg}/타입명  ·  예외는 별도 표기',22,MUTED)


def card(x,y,title,lines,color=BLUE):
    box(x,y,790,350,'#FFFFFF','#D5DFE9')
    text(x+24,y+14,742,title,29,color)
    text(x+24,y+66,742,lines,23)

card(80,1510,'① HMI → 공정  |  Action · Service',[
 'A  run_batch : RunBatch',
 'S  qa_decision : QaDecision',
 'S  interlock : InterlockRequest',
 'S  request_safety_recovery : RecoverSafety',
 'Goal recipe → Feedback state / last_result → Result',
 'submit_order : SubmitOrder는 별도 클라이언트용',
 'HMI 운영 주문은 RunBatch 사용'])
card(905,1510,'② 공정 → 스킬  |  Action 6종',[
 'A  move_to_station : MoveToStation',
 'A  scoop : Scoop',
 'A  pour : Pour',
 'A  return_material : ReturnMaterial',
 'A  weigh_container : WeighContainer',
 'A  weigh_held : WeighHeld',
 'Goal → Feedback / Result · 취소 지원'])
card(1730,1510,'② · ③ 스킬 Service',[
 '② set_gripper : SetGripper',
 '② measure_force : MeasureForce',
 '② restore_grip : RestoreGrip',
 '② recover_safety : RecoverSafety',
 '②③ safe_pose : SafePose',
 '③ emergency_stop : std_srvs/srv/Trigger',
 '③ HMI 직접 요청 / 복구는 ① → ② 중계'],RED)
card(80,1890,'④ 공정 → HMI · 기록  |  Topic',[
 'state : CellState',
 'weight : WeightReading',
 'scoop_cycle : ScoopCycle',
 'dispense_result : DispenseResult',
 'deviation : Deviation',
 '상태 · 계량 · 스쿱 시도 · 원료 결과 · 일탈',
 '공정 이벤트는 공통 event 채널 ⑥ 사용'],TEAL)
card(905,1890,'⑤ 스킬 → HMI  |  Topic',[
 'gripper_state : GripperState',
 'busy / grip_inferred / safety_triggered 등',
 '현행 DIO: 폭 -1 = 미측정',
 '파지력 명령 없음 / force_cmd_n = 0',
 '공정·기록 노드는 이 Topic을 구독하지 않음',
 '공정의 파지 판정은 스킬 응답으로 전달',
 'QoS: BEST_EFFORT · depth 1'],TEAL)
card(1730,1890,'⑥ 공통 이벤트  |  Topic',[
 'event : CellEvent',
 '발행: HMI · 공정 · 스킬',
 '구독: HMI · 공정 · 기록',
 'HMI: HMI_* 조작 감사',
 '공정: 배치 / 단계 / 일탈 처리 / 예약',
 '스킬: NUDGE / ROBOT_SAFETY_STOP',
 '         ROBOT_SAFETY_RECOVERY 등'],TEAL)
text(80,2274,2440,'운영 기준: 모든 노드는 한 PC에서 실행 · 현재 gripper.backend=dio · Modbus/가상 시험 전용 경로는 생략',24,MUTED)
text(80,2320,2440,'근거: cell.launch.py · robot.launch.py · 각 nodes/*.py · common.yaml · docs/interfaces.md v1.11',22,MUTED)
OUT.mkdir(parents=True,exist_ok=True)
ET.indent(doc,space='  ')
ET.ElementTree(doc).write(OUT/'node_architecture.drawio',encoding='utf-8',xml_declaration=True)
image.save(OUT/'node_architecture.png')
print('생성 완료: node_architecture.drawio / node_architecture.png')
