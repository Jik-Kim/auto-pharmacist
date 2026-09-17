#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""docs/todo.md 의 체크박스를 세어 맨 위 진행 표를 다시 쓴다.

진행률을 손으로 적지 않는 이유 — 손으로 적은 퍼센트는 근거가 없어서
아무도 믿지 않게 되고, 결국 갱신이 멈춘다. 체크박스를 세면 숫자에 근거가
있고 "남은 게 무엇인지" 도 바로 답이 된다.

사용:  python3 tools/todo_stats.py           # 표 갱신
       python3 tools/todo_stats.py --check   # 갱신 없이 확인만 (지난 마감이 있으면 exit 1)
"""
import datetime
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TODO = os.path.normpath(os.path.join(HERE, '..', 'docs', 'todo.md'))
BEGIN, END = '<!-- STATS:BEGIN -->', '<!-- STATS:END -->'
YEAR = 2026

ITEM = re.compile(r'^- \[([ xX])\]\s*(.*)$')
DUE = re.compile(r'마감\s*(\d{1,2})/(\d{1,2})')   # 줄 끝 앵커 없음 — 뒤에 '(A 와)' 가 붙어도 읽는다
SECTION = re.compile(r'^## (.+)$')


def parse(lines):
    """섹션별 항목을 모은다. 반환: [(섹션명, [(done, 본문, 마감일 or None)])]"""
    out, cur = [], None
    for ln in lines:
        m = SECTION.match(ln)
        if m:
            cur = (m.group(1).strip(), [])
            out.append(cur)
            continue
        m = ITEM.match(ln)
        if m and cur is not None:
            done = m.group(1).lower() == 'x'
            text = m.group(2).strip()
            ds = DUE.findall(text)          # 여럿이면 마지막 것 (마감은 줄 뒤쪽에 온다)
            due = datetime.date(YEAR, int(ds[-1][0]), int(ds[-1][1])) if ds else None
            cur[1].append((done, text, due))
    return [s for s in out if s[1]]


def strip_md(t):
    """마감 표기와 강조·코드 표시를 걷어낸 본문. 표 안에서 읽히게 한다."""
    t = re.sub(r'\s*·?\s*마감\s*\d+/\d+\s*', ' ', t)
    t = t.replace('**', '').replace('`', '')
    t = re.sub(r'\s+', ' ', t).strip()
    return t if len(t) <= 72 else t[:71] + '…'


def bar(done, total, width=10):
    if total == 0:
        return ''
    filled = round(width * done / total)
    return '█' * filled + '░' * (width - filled)


def build(sections, today):
    tot = sum(len(i) for _, i in sections)
    fin = sum(1 for _, i in sections for d, _, _ in i if d)
    late = [(s, t, due) for s, i in sections for d, t, due in i
            if not d and due and due < today]
    soon = [(s, t, due) for s, i in sections for d, t, due in i
            if not d and due and due == today]

    o = [BEGIN, '']
    o.append('**전체 %d/%d 완료** (%s)  ·  기준 %s'
             % (fin, tot, bar(fin, tot, 20), today.strftime('%m/%d')))
    o.append('')
    o.append('| 파트 | 완료 | 진행 | 지난 마감 |')
    o.append('|---|---|---|---|')
    for name, items in sections:
        dn = sum(1 for d, _, _ in items if d)
        od = sum(1 for d, _, due in items if not d and due and due < today)
        o.append('| %s | %d/%d | `%s` | %s |'
                 % (name, dn, len(items), bar(dn, len(items)),
                    ('**%d**' % od) if od else '—'))
    o.append('')
    if late:
        o.append('**마감이 지난 항목 %d건**' % len(late))
        o.append('')
        for s, t, due in late:
            o.append('- `%d/%d` %s — %s'
                     % (due.month, due.day, s.split('[')[0].strip(), strip_md(t)))
        o.append('')
    if soon:
        o.append('**오늘 마감 %d건**' % len(soon))
        o.append('')
        for s, t, _ in soon:
            o.append('- %s — %s' % (s.split('[')[0].strip(), strip_md(t)))
        o.append('')
    o.append('> 이 표는 `python3 tools/todo_stats.py` 가 체크박스를 세어 다시 쓴다. 손으로 고치지 않는다.')
    o.append('')
    o.append(END)
    return '\n'.join(o), len(late)


def main():
    today = datetime.date.today()
    text = open(TODO, encoding='utf-8').read()
    lines = text.split('\n')
    sections = parse(lines)
    block, late = build(sections, today)

    if BEGIN in text and END in text:
        pre = text[:text.index(BEGIN)]
        post = text[text.index(END) + len(END):]
        new = pre + block + post
    else:  # 첫 실행 — 제목 바로 아래에 끼워 넣는다
        i = lines.index('') if '' in lines else 1
        new = '\n'.join(lines[:i + 1]) + '\n' + block + '\n' + '\n'.join(lines[i + 1:])

    if '--check' in sys.argv:
        print('전체 %d/%d, 지난 마감 %d건'
              % (sum(1 for _, i in sections for d, _, _ in i if d),
                 sum(len(i) for _, i in sections), late))
        sys.exit(1 if late else 0)

    open(TODO, 'w', encoding='utf-8').write(new)
    print('갱신: %s  (지난 마감 %d건)' % (TODO, late))


if __name__ == '__main__':
    main()
