#!/usr/bin/env python3
"""auto-pharmacist 소개 2D 영상 — 평면도 위 로봇팔 애니메이션 (PIL + ffmpeg).
실행: python3 tools/make_intro_video.py  →  docs/diagrams/intro.mp4 (약 45 s, 1280x720)
"""
import math, subprocess, pathlib
from PIL import Image, ImageDraw, ImageFont

W, H, FPS = 1280, 720, 24
OUT = pathlib.Path(__file__).resolve().parent.parent / 'docs/diagrams/intro.mp4'
FB = '/usr/share/fonts/truetype/nanum/NanumSquareB.ttf'
FR = '/usr/share/fonts/truetype/nanum/NanumSquareR.ttf'
f = lambda p, s: ImageFont.truetype(p, s)
INK, MUTED, LINE, ACC, ACCS, WARM, WARMS, OK = '#161B2A', '#5F6980', '#C3CADB', '#3451D1', '#E4E9FB', '#D9782A', '#FBEBDD', '#1F7A8C'

# 판 820x650 mm → 화면. 베이스 (-50,320)
SX, OX, OY = 0.72, 150, 60
P = lambda x, y: (OX + x * SX, OY + y * SX)
ST = {  # 이름: (x, y, 라벨)
    'passbox_empty': (550, 320, '빈 약통'), 'workbench': (350, 320, '칭량·조제'), 'scoop_rack': (200, 300, '스쿱'),
    'material_1': (270, 140, '원료 A'), 'material_2': (450, 120, '원료 B'), 'material_3': (270, 500, '원료 C'),
    'passbox_done': (580, 520, '완료품'), 'passbox': (640, 320, '패스박스'), 'reject_bin': (200, 560, '폐기함'),
}
BASE = (-50, 320)
L1, L2 = 410, 400   # 2링크 팔 (mm)

def arm(x, y):
    dx, dy = x - BASE[0], y - BASE[1]
    d = min(math.hypot(dx, dy), L1 + L2 - 1)
    a = math.atan2(dy, dx)
    c = (L1**2 + d**2 - L2**2) / (2 * L1 * d)
    e = (BASE[0] + L1 * math.cos(a + math.acos(max(-1, min(1, c)))), BASE[1] + L1 * math.sin(a + math.acos(max(-1, min(1, c)))))
    return e

def ease(t): return t * t * (3 - 2 * t)

def frame(pos, carry, cap, sub, state, weight=None, hold=None, hmi=None):
    im = Image.new('RGB', (W, H), '#F3F5FA'); d = ImageDraw.Draw(im)
    # 판·사람 구역
    d.rectangle([P(0, 0), P(820, 650)], fill='#FFFFFF', outline=LINE, width=2)
    d.rectangle([P(700, 0), P(820, 650)], fill=WARMS)
    d.text(P(760, 20), '사람 구역', fill=WARM, font=f(FB, 15), anchor='mt')
    d.text(P(760, 44), '로봇 도달 불가', fill=MUTED, font=f(FR, 12), anchor='mt')
    # 스테이션
    for k, (x, y, lb) in ST.items():
        c = P(x, y); r = 30
        fill = ACCS if k == 'workbench' else '#FFFFFF'
        d.rounded_rectangle([c[0]-r-14, c[1]-r, c[0]+r+14, c[1]+r], 8, fill=fill, outline=ACC if k == 'workbench' else LINE, width=2)
        d.text((c[0], c[1]), lb, fill=INK, font=f(FB, 14), anchor='mm')
    # 팔
    b = P(*BASE); e = P(*arm(*pos)); t = P(*pos)
    d.ellipse([b[0]-18, b[1]-18, b[0]+18, b[1]+18], fill=INK)
    d.line([b, e, t], fill='#3A4258', width=14, joint='curve')
    d.line([b, e, t], fill='#8AA2FF', width=6, joint='curve')
    d.ellipse([t[0]-9, t[1]-9, t[0]+9, t[1]+9], fill=ACC)
    if carry == 'scoop':
        d.line([(t[0], t[1]), (t[0]+22, t[1]-14)], fill=WARM, width=5); d.ellipse([t[0]+16, t[1]-26, t[0]+38, t[1]-6], fill=WARM)
    if carry == 'cup':
        d.rounded_rectangle([t[0]-14, t[1]-14, t[0]+14, t[1]+14], 5, fill='#FFFFFF', outline=WARM, width=3)
    # 상태 패널
    d.rounded_rectangle([760, 60, 1240, 300], 12, fill='#FFFFFF', outline=LINE, width=2)
    d.text((780, 78), 'HMI · 상태', fill=MUTED, font=f(FB, 13))
    col = {'RUNNING': OK, 'DEVIATION': WARM, 'PAUSED': WARM, 'DONE': ACC}.get(state, INK)
    d.rounded_rectangle([780, 100, 780 + 16 * len(state) + 20, 128], 6, fill=col); d.text((790, 106), state, fill='#FFFFFF', font=f(FB, 15))
    if weight is not None:
        d.text((780, 150), f'{weight:.1f} g', fill=INK, font=f(FB, 40)); d.text((780, 200), '목표 60 g · 허용 ±5 %', fill=MUTED, font=f(FR, 14))
    if hmi:
        d.text((780, 150), hmi, fill=INK, font=f(FB, 20))
        d.rounded_rectangle([780, 220, 900, 262], 8, fill=ACC); d.text((840, 241), '승인', fill='#FFFFFF', font=f(FB, 16), anchor='mm')
        d.rounded_rectangle([916, 220, 1036, 262], 8, fill='#FFFFFF', outline=WARM, width=2); d.text((976, 241), '폐기', fill=WARM, font=f(FB, 16), anchor='mm')
    # 자막
    d.rectangle([0, 600, W, H], fill=INK)
    d.text((40, 625), cap, fill='#FFFFFF', font=f(FB, 30))
    d.text((40, 670), sub, fill='#B7C0D6', font=f(FR, 18))
    return im

def title(t1, t2, t3=''):
    im = Image.new('RGB', (W, H), INK); d = ImageDraw.Draw(im)
    d.text((W/2, 280), t1, fill='#FFFFFF', font=f(FB, 56), anchor='mm')
    d.text((W/2, 350), t2, fill='#8AA2FF', font=f(FR, 26), anchor='mm')
    d.text((W/2, 420), t3, fill='#B7C0D6', font=f(FR, 20), anchor='mm')
    return im

frames = []
def hold(im, s): frames.extend([im] * int(s * FPS))
def move(a, b, s, carry, cap, sub, state='RUNNING'):
    n = int(s * FPS)
    for i in range(n):
        k = ease((i + 1) / n)
        frames.append(frame((a[0] + (b[0]-a[0])*k, a[1] + (b[1]-a[1])*k), carry, cap, sub, state))

S = lambda k: ST[k][:2]
safe = (300, 0)
hold(title('auto-pharmacist', '협동로봇 조제 칭량 셀 — M0609 + RG2', '카메라 없이 · 무인 · 레시피대로 퍼서 재고 스스로 검증'), 3)
hold(frame(safe, None, '사람은 셀 밖에서만', '빈 약통·원료는 패스박스로 반입 · 이후 사람은 셀에 손대지 않는다', 'IDLE'), 2.5)
move(safe, S('passbox_empty'), 1.2, None, '1  약통 반송', '매거진 슬롯의 빈 약통을 파지 — 정지 폭으로 파지 성공 판정')
move(S('passbox_empty'), S('workbench'), 1.2, 'cup', '1  약통 반송', '칭량 위치로 가져온다')
hold(frame(S('workbench'), None, '2  풍량 측정 (tare)', '용기를 들어 올려 get_workpiece_weight 로 빈 무게 기록', 'RUNNING', weight=28.4), 1.6)
move(S('workbench'), S('scoop_rack'), 1.0, None, '3  전용 스쿱 픽업', '원료별 전용 스쿱 — 교차오염 방지 (GMP)')
move(S('scoop_rack'), S('material_1'), 1.0, 'scoop', '4  원료 퍼올리기', 'Z 힘 제어로 원료면까지 내려가 접촉 감지 → 퍼낸다')
hold(frame(S('material_1'), 'scoop', '4  원료 퍼올리기', 'task_compliance_ctrl · set_desired_force · check_force_condition', 'RUNNING'), 1.4)
move(S('material_1'), S('workbench'), 1.0, 'scoop', '5  투입', 'movesx 곡선으로 붓고, move_periodic 진동으로 조금씩 털어낸다')
hold(frame(S('workbench'), 'scoop', '6  무게 검증 — 2차 폐루프', '실측 41.2 g < 목표 60 g → UNDER → 보정 투입', 'RUNNING', weight=41.2), 1.6)
move(S('workbench'), S('material_1'), 0.9, 'scoop', '6  UNDER → 보정 투입', '투입 비율을 줄여 다시 퍼온다 (최대 3회)')
move(S('material_1'), S('workbench'), 0.9, 'scoop', '6  UNDER → 보정 투입', '')
hold(frame(S('workbench'), 'scoop', '6  무게 검증 — OK', '59.3 g · 오차 -1.2 % ≤ ±5 % → 다음 원료', 'RUNNING', weight=59.3), 1.6)
move(S('workbench'), S('scoop_rack'), 0.9, 'scoop', '7  스쿱 반납', '원료 B · C 도 3 → 7 반복')
hold(frame(S('scoop_rack'), None, '일탈 시나리오 — 과다 투입', '원료 C: 34.8 g > 목표 30 g × 1.05 → OVER — 되돌릴 수 없다', 'DEVIATION', weight=34.8), 1.8)
hold(frame(safe, None, '일탈 → 셀 밖 QA 원격 판정', '로봇은 대기. HMI 에 계량값·편차·이력 표시 → 승인 / 폐기', 'DEVIATION', hmi='OVERFILL  원료 C  +16 %'), 2.4)
move(safe, S('workbench'), 1.0, None, '8  완료품 적재', 'QA 승인 → 용기째 완료품 트레이로 (폐기면 폐기함)', 'RUNNING')
move(S('workbench'), S('passbox_done'), 1.2, 'cup', '8  완료품 적재', '배치 기록 종료 — SQLite 에 전 계량값·판정·감사 추적', 'RUNNING')
move(S('passbox_done'), safe, 1.0, None, '무인 연속 배치', '다음 약통이 매거진에 있으면 사람 없이 반복', 'DONE')
hold(title('비전 없이 판단하는 로봇', '파지 = 그리퍼 폭 추론 · 무게 = 관절 토크 · 접촉 = 힘 조건', '일탈 6종 자동 복구 · QA 원격 승인 · 배치 기록(SQLite) · 매뉴얼 3·4·5·6 절 전부 사용'), 3.5)

OUT.parent.mkdir(exist_ok=True)
p = subprocess.Popen(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{W}x{H}', '-r', str(FPS), '-i', '-',
                      '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '22', str(OUT)], stdin=subprocess.PIPE)
for im in frames: p.stdin.write(im.tobytes())
p.stdin.close(); p.wait()
print(OUT, f'{len(frames)/FPS:.1f}s', len(frames), 'frames')
