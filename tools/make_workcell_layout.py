#!/usr/bin/env python3
"""협동로봇 조제 셀 작업공간 배치도 — 볼트 구멍(체결점) 기준 치수 그림 (PIL).
실행: python3 tools/make_workcell_layout.py  →  docs/diagrams/workcell_layout.png

치수 출처 (9/17 실측, C):
  · 가장자리 볼트 구멍 4×4, 가로·세로 약 15 cm 등간격 (전체 약 44 cm)
  · Pass Box · 폐기함 열의 볼트 구멍은 가장 오른쪽 열에서 약 5 cm 왼쪽
숫자만 바꾸면 그림이 따라온다 — COLS / ROWS / ZONES.
"""
import pathlib
from PIL import Image, ImageDraw, ImageFont

OUT = pathlib.Path(__file__).resolve().parent.parent / 'docs/diagrams/workcell_layout.png'
FB, FR = '/usr/share/fonts/truetype/nanum/NanumSquareB.ttf', '/usr/share/fonts/truetype/nanum/NanumSquareR.ttf'
f = lambda p, s: ImageFont.truetype(p, s)

# ── 치수 (cm) — 좌하단 볼트 구멍이 원점, x 오른쪽, y 위쪽 ─────────────────────
PITCH = 15.0
COLS = [0.0, 15.0, 30.0, 39.0, 44.0]      # 39 = 패스박스·폐기함 열 (44 − 5)
ROWS = [0.0, 15.0, 30.0, 44.0]
BOARD = (-2.5, -2.5, 47.5, 46.5)          # 판 외곽 (x0, y0, x1, y1)
UNUSABLE_X = 42.0                         # 이 오른쪽은 사용 불가
# 구역: (x0, y0, x1, y1, 라벨, 채움, 테두리)  — 원료 구역은 윗 두 행 사이, 스쿱·계량은 아랫 두 행 사이
ZONES = [
    (2.0, 33.0, 30.0, 42.0, '원료 배치 구역', '#CFE6FA', '#3B82D6'),
    (-8.5, 2.0, 9.0, 12.0, '스쿱 보관\n(판 바깥, 별도 거치)', '#FBD9B0', '#E07B22'),   # 9/17: 원점 기준 왼쪽으로 10 cm 당김
    (10.5, 2.0, 30.5, 12.0, '작업 · 계량 공간', '#D6F5D6', '#2E9E4F'),                # 스쿱 보관이 빠진 만큼 왼쪽으로 10 cm 확장
    (36.0, 17.0, 42.0, 27.5, 'Pass\nBox', '#FBD6E8', '#D9489A'),
    (36.0, 2.0, 42.0, 12.0, '폐기함', '#F9C9C4', '#D63B2F'),
]
CUP = (28.0, 21.0, 4.0)                    # 빈 용기 (x, y, 반지름)

# ── 화면 변환 ──────────────────────────────────────────────────────
S = 30                                     # px / cm
LEFT_EDGE = -9.0                           # 그림 왼쪽 끝 (판 밖 스쿱 보관 포함) — 세로 치수선은 이 왼쪽에
MX, MY = 450, 230                          # 여백 (치수선 자리)
W = int((BOARD[2] - BOARD[0]) * S + MX * 2)
H = int((BOARD[3] - BOARD[1]) * S + MY * 2 + 60)
def P(x, y):                               # cm → px (y 뒤집기)
    return (MX + (x - BOARD[0]) * S, MY + (BOARD[3] - y) * S)

im = Image.new('RGB', (W, H), '#FFFFFF')
d = ImageDraw.Draw(im)
INK, DIM, GRID = '#1F3A6E', '#1E4FA3', '#9AA3B2'

# 판
d.rectangle([P(BOARD[0], BOARD[3]), P(BOARD[2], BOARD[1])], fill='#F6E4C6', outline='#333333', width=3)
d.rectangle([P(UNUSABLE_X, BOARD[3]), P(BOARD[2], BOARD[1])], fill='#D3D7DF', outline='#333333', width=3)
ux = (UNUSABLE_X + BOARD[2]) / 2
d.text(P(ux, 37.0), '사용 불가\n영역', fill='#555C6B', font=f(FB, 30), anchor='mm', align='center')

# 구역
for x0, y0, x1, y1, lb, fill, stroke in ZONES:
    d.rounded_rectangle([P(x0, y1), P(x1, y0)], 14, fill=fill, outline=stroke, width=4)
    d.text(P((x0 + x1) / 2, (y0 + y1) / 2), lb, fill=INK, font=f(FB, 30 if '\n' in lb else 34), anchor='mm', align='center')
cx, cy, r = CUP
d.ellipse([P(cx - r, cy + r), P(cx + r, cy - r)], fill='#D6F5D6', outline='#2E9E4F', width=4)
d.text(P(cx, cy), '빈 용기', fill=INK, font=f(FB, 30), anchor='mm')

# 볼트 구멍
def hole(x, y):
    c = P(x, y)
    d.ellipse([c[0] - 22, c[1] - 22, c[0] + 22, c[1] + 22], fill='#DDDDDD', outline='#222222', width=3)
    d.ellipse([c[0] - 6, c[1] - 6, c[0] + 6, c[1] + 6], fill='#222222')
for x in COLS:
    for y in ROWS:
        hole(x, y)

# 치수선
def arrow(a, b, color=DIM, w=4, head=14):
    d.line([a, b], fill=color, width=w)
    for tip, base in ((a, b), (b, a)):
        dx, dy = base[0] - tip[0], base[1] - tip[1]
        n = (dx * dx + dy * dy) ** 0.5
        ux_, uy_ = dx / n, dy / n
        px, py = -uy_, ux_
        d.polygon([tip, (tip[0] + ux_ * head + px * head * 0.45, tip[1] + uy_ * head + py * head * 0.45),
                   (tip[0] + ux_ * head - px * head * 0.45, tip[1] + uy_ * head - py * head * 0.45)], fill=color)
def ext(x, y, dx, dy, length):            # 보조선 (점선)
    a, b = P(x, y), (P(x, y)[0] + dx * length, P(x, y)[1] + dy * length)
    n = int(length // 12)
    for i in range(0, n, 2):
        t0, t1 = i / n, min(1, (i + 1) / n)
        d.line([(a[0] + (b[0] - a[0]) * t0, a[1] + (b[1] - a[1]) * t0), (a[0] + (b[0] - a[0]) * t1, a[1] + (b[1] - a[1]) * t1)], fill=GRID, width=2)

top = P(0, ROWS[-1])[1] - 30                # 위 치수선 기준 y
for x in COLS:
    ext(x, ROWS[-1], 0, -1, 200)
# 위: 전체 44 cm
y1 = top - 130
arrow((P(COLS[0], 0)[0], y1), (P(COLS[-1], 0)[0], y1))
d.text(((P(COLS[0], 0)[0] + P(COLS[-1], 0)[0]) / 2, y1 - 30), f'{COLS[-1]:.0f} cm', fill=DIM, font=f(FB, 34), anchor='mm')
# 위: 15 / 15 / 9 / 5
y2 = top - 60
for a, b in zip(COLS, COLS[1:]):
    arrow((P(a, 0)[0], y2), (P(b, 0)[0], y2), w=3, head=11)
    d.text(((P(a, 0)[0] + P(b, 0)[0]) / 2, y2 - 24), f'{b - a:.0f}', fill=DIM, font=f(FB, 26), anchor='mm')
# 왼쪽: 전체 44 cm, 15 / 15 / 14
left = P(LEFT_EDGE, 0)[0] - 20
for y in ROWS:
    ext(0, y, -1, 0, P(0, 0)[0] - left + 150)
x1 = left - 130
arrow((x1, P(0, ROWS[0])[1]), (x1, P(0, ROWS[-1])[1]))
d.text((x1 - 60, (P(0, ROWS[0])[1] + P(0, ROWS[-1])[1]) / 2), f'{ROWS[-1]:.0f}\ncm', fill=DIM, font=f(FB, 34), anchor='mm', align='center')
x2 = left - 60
for a, b in zip(ROWS, ROWS[1:]):
    arrow((x2, P(0, a)[1]), (x2, P(0, b)[1]), w=3, head=11)
    d.text((x2 - 30, (P(0, a)[1] + P(0, b)[1]) / 2), f'{b - a:.0f}', fill=DIM, font=f(FB, 26), anchor='mm')

# 스쿱 보관 당김량 (원점 기준 왼쪽 10 cm)
ya = P(0, 13.2)[1]
arrow((P(0, 0)[0], ya), (P(-10.0, 0)[0], ya), w=3, head=11)
d.text((P(-5.0, 0)[0], ya + 24), '10 (당김)', fill=DIM, font=f(FB, 24), anchor='mm')

# 체결점 기준 표시
o = P(0, 0)
d.line([(o[0] - 60, o[1] + 90), (o[0] - 16, o[1] + 16)], fill=DIM, width=3)
d.text((o[0] - 70, o[1] + 100), '체결점 기준 (원점)', fill=DIM, font=f(FB, 26), anchor='la')
d.text((W / 2, 50), '협동로봇 조제 셀 작업공간 배치도', fill=INK, font=f(FB, 46), anchor='mm')
d.text((W / 2, H - 40), '볼트 구멍 = 체결점 · 단위 cm · 가장자리 4×4 구멍 15 cm 등간격 · Pass Box/폐기함 열은 오른쪽 끝에서 5 cm',
       fill='#5F6980', font=f(FR, 22), anchor='mm')

OUT.parent.mkdir(exist_ok=True)
im.save(OUT)
print(OUT, im.size)
