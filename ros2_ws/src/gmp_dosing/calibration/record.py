#!/usr/bin/env python3
"""기록지에 한 회차를 적는다. scoop_run.sh 가 부른다.

  record.py <csv> check              빈 시료통 무게가 적혀 있는지만 본다
  record.py <csv> next               다음 빈 회차 번호 (없으면 0)
  record.py <csv> put <무게> [원료면] [비고]
"""
import sys
from pathlib import Path

CUP = '용기_g'


def split(path):
    lines = Path(path).read_text(encoding='utf-8').splitlines()
    head = [l for l in lines if l.lstrip().startswith('#')]
    body = [l for l in lines if not l.lstrip().startswith('#')]
    return lines, head, body


def cup_of(head):
    for l in head:
        k, s, v = l.lstrip('# ').partition(':')
        if s and k.strip() == CUP:
            try:
                return float(v.split('#')[0].strip())
            except ValueError:
                return None
    return None


def main(argv):
    path, cmd = argv[1], argv[2]
    lines, head, body = split(path)
    cup = cup_of(head)
    if cmd == 'check':
        if cup is None:
            print(f'!! 기록지에 `# {CUP}: <숫자>` 가 비어 있다.', file=sys.stderr)
            print(f'   빈 시료통을 한 번 재서 {path} 의 그 줄에 적고 다시 실행하세요.', file=sys.stderr)
            return 1
        print(f'빈 시료통 {cup:g} g')
        return 0
    rows = [l for l in body if l.strip() and not l.startswith('회차,')]
    empty = [(i, l) for i, l in enumerate(rows) if l.split(',')[1].strip() == '']
    if cmd == 'next':
        print(rows[empty[0][0]].split(',')[0] if empty else 0)
        return 0
    if cmd == 'put':
        if not empty:
            print('!! 빈 회차가 없다 — 기록지가 다 찼다', file=sys.stderr)
            return 1
        try:
            w = float(argv[3])
        except ValueError:
            print(f'!! 숫자가 아니다: {argv[3]!r}', file=sys.stderr)
            return 1
        if cup is not None and w <= cup:
            print(f'!! {w:g} g 가 빈 시료통 {cup:g} g 이하다 — 비우고 안 넣었거나 tare 를 쳤다.',
                  file=sys.stderr)
            return 1
        idx, line = empty[0]
        f = (line.split(',') + ['', '', '', ''])[:4]
        f[1] = f'{w:g}'
        if len(argv) > 4 and argv[4]:
            f[2] = argv[4]
        if len(argv) > 5 and argv[5]:
            f[3] = (f[3] + ' ' + argv[5]).strip()
        rows[idx] = ','.join(f)
        out, ri = [], 0
        for l in lines:
            if l.lstrip().startswith('#') or not l.strip() or l.startswith('회차,'):
                out.append(l)
            else:
                out.append(rows[ri]); ri += 1
        Path(path).write_text('\n'.join(out) + '\n', encoding='utf-8')
        print(f'회차 {f[0]}: 총무게 {w:g} g → 투입량 {w - cup:.1f} g')
        return 0
    print('알 수 없는 명령', file=sys.stderr)
    return 2


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
