#!/usr/bin/env python3
"""스쿱 1회 투입량의 평균·σ 를 재고 고정 스쿱이 성립하는지 판정한다 (#272).

측정은 로봇 계량을 쓰지 않는다 — A 가 스쿱 스킬을 수동 실행하고 **저울로 직접 계량**한다.
로봇 계량 잡음(재파지 σ 7 g)이 스쿱 산포에 섞이면 둘을 못 가른다.

기록은 저울 값 **세 개**만 적는다. 나머지는 이 스크립트가 뺄셈으로 만든다:

    퍼올림_g = 채취후_g − 빈스쿱_g      스쿱이 퍼올린 양
    잔량_g   = 붓기후스쿱_g − 빈스쿱_g  붓고도 스쿱에 붙어 남은 양
    투입량_g = 채취후_g − 붓기후스쿱_g  **용기에 실제로 들어간 양**

⚠️ 판정 대상은 **투입량**이다. `dosing.scoop_nominal_g` 가 `decide()` 에서 쓰이는 방식이
   「한 스쿱이 용기 내용물을 얼마나 늘리는가」(min_add·need 계산)라서 그렇다.
   퍼올림량으로 판정하면 잔량만큼 낙관적으로 나온다.

사용법:
    python3 scoop_sigma.py --template > records/scoop_sigma_0924.csv   # 빈 기록지
    python3 scoop_sigma.py records/scoop_sigma_0924.csv                # 분석·판정
    python3 scoop_sigma.py records/scoop_sigma_0924.csv --nominal 79 --tol 10
"""
from __future__ import annotations

import argparse
import csv
import math
import random
import sys
from pathlib import Path

COLUMNS = ['회차', '빈스쿱_g', '채취후_g', '붓기후스쿱_g', '원료면', '비고']
POUR_COLUMNS = ['회차', '투입량_g', '원료면', '비고']
GROSS_COLUMNS = ['회차', '총무게_g', '원료면', '비고']
CUP_DIRECTIVE = '용기_g'
SURFACES = ('가득', '중간', '바닥')

TEMPLATE = (
    '# ' + ' · '.join(COLUMNS) + '\n'
    '# 저울 값 세 개만 적는다. 퍼올림·잔량·투입량은 scoop_sigma.py 가 계산한다.\n'
    '#   빈스쿱_g     퍼기 전 스쿱 전체 무게\n'
    '#   채취후_g     퍼올린 직후 스쿱 전체 무게\n'
    '#   붓기후스쿱_g 용기에 붓고 난 뒤 스쿱 전체 무게 (잔량 포함)\n'
    '#   원료면       가득 | 중간 | 바닥 — 평균이 흐를 때 원인이 원료면인지 스쿱인지 가른다\n'
    '# 같은 원료·같은 깊이(depth_fraction 1.0)로 연속 15회. 중간에 원료를 보충하면 비고에 적는다.\n'
    + ','.join(COLUMNS) + '\n'
    + '\n'.join(f'{i},,,,,' for i in range(1, 16)) + '\n'
)


POUR_TEMPLATE = (
    '# ' + ' · '.join(POUR_COLUMNS) + '\n'
    '# 붓기 방식 — 저울 위 용기에 부어 **회차마다 저울 1번**만 읽는다.\n'
    '#   투입량_g  붓기 직전 저울을 0 으로 맞추고(tare), 붓고 나서 읽은 값\n'
    '#             = 용기에 실제로 들어간 양. 이것이 판정 대상이다\n'
    '#   원료면    가득 | 중간 | 바닥\n'
    '# tare 를 깜빡했으면 비고에 적는다 — 누적값이 섞이면 산포가 통째로 틀린다.\n'
    '# 용기가 차면 비우고 다시 tare 한다. 비운 회차도 비고에 적는다.\n'
    + ','.join(POUR_COLUMNS) + '\n'
    + '\n'.join(f'{i},,,' for i in range(1, 16)) + '\n'
)


# ── χ² 분위수 (stdlib 만 사용) ──────────────────────────────────────────
def _gammainc_lower_reg(s: float, x: float) -> float:
    """정규화 하부 불완전감마 P(s, x). 급수와 연분수를 갈라 쓴다."""
    if x <= 0.0:
        return 0.0
    if x < s + 1.0:
        term = 1.0 / s
        total = term
        n = 0
        while n < 1000:
            n += 1
            term *= x / (s + n)
            total += term
            if abs(term) < abs(total) * 1e-15:
                break
        return total * math.exp(-x + s * math.log(x) - math.lgamma(s))
    # 연분수 (Lentz)
    tiny = 1e-300
    b, c, d = x + 1.0 - s, 1.0 / tiny, 1.0 / (x + 1.0 - s)
    h = d
    for i in range(1, 1000):
        an = -i * (i - s)
        b += 2.0
        d = an * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + an / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-15:
            break
    return 1.0 - math.exp(-x + s * math.log(x) - math.lgamma(s)) * h


def chi2_ppf(p: float, k: int) -> float:
    """자유도 k 의 χ² 분위수. 이분법 — 표본이 수십 개라 속도는 문제가 안 된다."""
    lo, hi = 0.0, max(10.0 * k, 10.0)
    while _gammainc_lower_reg(k / 2.0, hi / 2.0) < p:
        hi *= 2.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if _gammainc_lower_reg(k / 2.0, mid / 2.0) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def sigma_bounds(sd: float, n: int, conf: float = 0.95) -> tuple[float, float]:
    """참 σ 의 **한쪽** 95 % 한계 (하한, 상한). 각각 따로 읽는다.

    ⚠️ 양쪽 신뢰구간이 아니다 — 둘을 묶으면 90 % 다. 판정이 「σ 가 3 g 보다 클 수도
    있는가」라는 **한쪽 질문**이라 한쪽 한계를 쓴다. practice/B/CURRENT.md #249 설계의
    배수표(N=10 1.65 · 15 1.46 · 30 1.28)가 이 기준이다.
    """
    if n < 2 or sd <= 0.0:
        return (0.0, float('inf'))
    k = n - 1
    a = 1.0 - conf
    return (sd * math.sqrt(k / chi2_ppf(1.0 - a, k)), sd * math.sqrt(k / chi2_ppf(a, k)))


# ── 읽기 ────────────────────────────────────────────────────────────────
class RowError(ValueError):
    pass


def load(path: str) -> list[dict]:
    """기록 CSV 를 읽는다. 두 형식을 모두 받는다 — 헤더로 가른다.

    · 붓기 방식  `투입량_g` — 저울 위 용기에 부어 회차당 1번 읽는다 (9/23 채택)
    · 3회 계량   `빈스쿱_g/채취후_g/붓기후스쿱_g` — 스쿱을 빼서 재는 방식
    어느 쪽이든 판정은 **투입량**으로 한다. 3회 계량일 때만 퍼올림·잔량이 따라온다.
    """
    cup = None
    lines = []
    with open(path, encoding='utf-8') as fh:
        for ln in fh:
            if ln.lstrip().startswith('#'):
                head, sepd, tail = ln.lstrip('# ').partition(':')
                if sepd and head.strip() == CUP_DIRECTIVE:
                    try:
                        cup = float(tail.split('#')[0].strip())
                    except ValueError as exc:
                        raise RowError(f'{path}: `# {CUP_DIRECTIVE}: <숫자>` 를 못 읽었다 — {exc}') from exc
                continue
            lines.append(ln)
    reader = csv.DictReader(lines)
    fields = set(reader.fieldnames or ())
    pour = '투입량_g' in fields
    gross = '총무게_g' in fields
    if not (pour or gross) and not {'빈스쿱_g', '채취후_g', '붓기후스쿱_g'} <= fields:
        raise RowError(f'{path}: 헤더를 못 알아보겠다 — `투입량_g`, `총무게_g`, 또는 '
                       f'`빈스쿱_g/채취후_g/붓기후스쿱_g` 가 필요하다. 지금: {sorted(fields)}')
    if gross and cup is None:
        raise RowError(f'{path}: `총무게_g` 로 적으려면 주석에 `# {CUP_DIRECTIVE}: 79.0` 처럼 '
                       f'빈 용기 무게가 있어야 한다 — 없으면 투입량을 못 낸다')
    need = ['총무게_g'] if gross else (['투입량_g'] if pour else ['빈스쿱_g', '채취후_g', '붓기후스쿱_g'])
    rows = []
    for i, raw in enumerate(reader, start=1):
        if raw.get('회차') in (None, ''):
            continue
        if any(not (raw.get(c) or '').strip() for c in need):
            continue                                   # 아직 안 적은 줄은 건너뛴다
        try:
            vals = [float(raw[c]) for c in need]
        except ValueError as exc:
            raise RowError(f'{path} {i} 번째 기록: 숫자가 아니다 — {exc}') from exc
        surface = (raw.get('원료면') or '').strip()
        note = (raw.get('비고') or '').strip()
        if gross:
            total, = vals
            delivered = total - cup
            if delivered <= 0:
                raise RowError(f'회차 {raw["회차"]}: 총무게 {total:g} g 가 빈 용기 {cup:g} g 이하다 — '
                               f'용기를 비우고 다시 쟀는지, tare 를 쳐버렸는지 확인')
            rows.append({'회차': raw['회차'], '투입량': delivered,
                         '원료면': surface, '비고': note})
            continue
        if pour:
            delivered, = vals
            if delivered <= 0:
                raise RowError(f'회차 {raw["회차"]}: 투입량 {delivered:g} g — tare 를 안 했거나 부호가 틀렸다')
            rows.append({'회차': raw['회차'], '투입량': delivered,
                         '원료면': surface, '비고': note})
            continue
        empty, after, poured = vals
        scooped, residue, delivered = after - empty, poured - empty, after - poured
        if scooped <= 0:
            raise RowError(f'회차 {raw["회차"]}: 채취후({after}) 가 빈스쿱({empty}) 이하다 — 열을 바꿔 적었나')
        if residue < -0.5:
            raise RowError(f'회차 {raw["회차"]}: 잔량이 음수({residue:.1f} g) — 붓기후({poured}) < 빈스쿱({empty})')
        if delivered < 0:
            raise RowError(f'회차 {raw["회차"]}: 투입량이 음수({delivered:.1f} g) — 붓기후({poured}) > 채취후({after})')
        rows.append({'회차': raw['회차'], '퍼올림': scooped, '잔량': residue,
                     '투입량': delivered, '원료면': surface, '비고': note})
    return rows


def stats(xs: list[float]) -> dict:
    n = len(xs)
    mean = sum(xs) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in xs) / (n - 1)) if n > 1 else 0.0
    lo, hi = sigma_bounds(sd, n)
    return {'n': n, 'mean': mean, 'sd': sd, 'sd_lo': lo, 'sd_hi': hi,
            'cv': (sd / mean * 100.0 if mean else 0.0), 'min': min(xs), 'max': max(xs)}


# ── 완주율 — 실제 decide() 로 돌린다 ────────────────────────────────────
def _fixed_cfg(nominal: float, max_attempts: int):
    """#274 가 머지됐으면 진짜 decide() 를, 아니면 None 을 준다 (호출자가 내장 규칙 사용)."""
    try:
        from gmp_dosing.core.dosing import DosingConfig
        return DosingConfig(max_attempts=max_attempts, scoop_nominal_g=nominal,
                            min_fraction=0.10, fixed_scoop=True)
    except (ImportError, TypeError):
        return None


def _run_material(target: float, tol: float, nominal: float, draw, cfg, max_attempts: int) -> bool:
    """한 원료를 고정 스쿱으로 채운다. 성공하면 True, QA 로 가면 False."""
    actual, attempts = 0.0, 0
    upper, lower = target * (1 + tol / 100.0), target * (1 - tol / 100.0)
    while True:
        if cfg is not None:
            from gmp_dosing.core.dosing import decide
            d = decide(target, actual, tol, attempts, True, 0, cfg)
            if d.action == 'DONE':
                return True
            if d.action == 'DEVIATION':
                return False
        else:                                           # #274 미머지 — 같은 규칙을 내장으로
            if lower <= actual <= upper:
                return True
            if actual > upper or actual + nominal > upper or attempts >= max_attempts:
                return False
        actual += draw()
        attempts += 1


def completion_rates(delivered: list[float], recipes: dict, nominal: float,
                     max_attempts: int, trials: int, seed: int) -> dict:
    """측정값을 부트스트랩으로 다시 뽑아 배치 완주율을 낸다. 정규분포를 가정하지 않는다."""
    rng = random.Random(seed)
    cfg = _fixed_cfg(nominal, max_attempts)
    out = {'engine': 'decide()' if cfg is not None else '내장 규칙 (#274 미머지)'}
    for name, items in recipes.items():
        ok = 0
        for _ in range(trials):
            if all(_run_material(t, tol, nominal, lambda: rng.choice(delivered), cfg, max_attempts)
                   for t, tol in items):
                ok += 1
        out[name] = ok / trials * 100.0
    return out


# ── 판정 ────────────────────────────────────────────────────────────────
def verdict(s: dict, nominal: float) -> list[str]:
    lines = []
    off = s['mean'] - nominal
    if abs(off) <= 5.0:
        lines.append(f"평균 ✅ {s['mean']:.1f} g — 목표 {nominal:g} ± 5 g 안 (편차 {off:+.1f})")
    else:
        lines.append(f"평균 ❌ {s['mean']:.1f} g — {nominal:g} ± 5 g 밖 (편차 {off:+.1f}). "
                     f"scoop_nominal_g 를 실측값으로 갱신하고 레시피를 재검토한다")
    if s['sd'] <= 3.0:
        lines.append(f"σ ✅ {s['sd']:.2f} g ≤ 3 g — 진행")
    elif s['sd'] <= 5.0:
        lines.append(f"σ ⚠️ {s['sd']:.2f} g (3~5 g) — 조건부. 시연 리허설로 판단한다")
    else:
        lines.append(f"σ ❌ {s['sd']:.2f} g > 5 g — 고정 스쿱 부적합. 깊이 제어나 다른 설계로 돌아간다")
    if s['sd'] <= 3.0 < s['sd_hi']:
        lines.append(f"   ⚠️ 다만 σ 95 % 상한이 {s['sd_hi']:.2f} g 다 — n={s['n']} 이라 "
                     f"점추정이 우연히 작게 나왔을 수 있다. 경계면 회차를 늘린다")
    return lines


def report(rows: list[dict], nominal: float, tol: float, max_attempts: int,
           trials: int, seed: int, out=sys.stdout) -> dict:
    delivered = [r['투입량'] for r in rows]
    s = stats(delivered)
    three = '퍼올림' in rows[0]
    p = stats([r['퍼올림'] for r in rows]) if three else None
    q = stats([r['잔량'] for r in rows]) if three else None

    w = out.write
    w(f"■ 스쿱 1회량 (#272) — 회차 {s['n']}  ({'3회 계량' if three else '붓기 방식'})\n\n")
    w(f"{'':10}{'평균':>9}{'σ':>8}{'CV':>8}{'최소':>8}{'최대':>8}\n")
    for label, d in (('투입량', s), ('퍼올림', p), ('잔량', q)):
        if d is not None:
            w(f"{label:10}{d['mean']:9.2f}{d['sd']:8.2f}{d['cv']:7.1f}%{d['min']:8.1f}{d['max']:8.1f}\n")
    w(f"\nσ(투입량) 한쪽 95 % 한계  하한 {s['sd_lo']:.2f} / 상한 {s['sd_hi']:.2f} g  (양쪽 구간 아님)\n")
    if q is not None:
        w(f"잔량 가정 확인       CURRENT.md 는 2 g 를 가정했다 → 실측 {q['mean']:.1f} g\n")
    else:
        w("잔량                 붓기 방식은 스쿱에 남는 양을 따로 보지 않는다 —\n"
          "                     투입량에 이미 빠져 있어 판정에는 영향이 없다\n")
    w("\n")

    w("■ 판정 — 투입량 기준 (scoop_nominal_g 가 decide() 에서 뜻하는 값)\n")
    for line in verdict(s, nominal):
        w('  ' + line + '\n')

    by_surface = {}
    for r in rows:
        by_surface.setdefault(r['원료면'] or '(미기재)', []).append(r['투입량'])
    if len(by_surface) > 1:
        w("\n■ 원료면 상태별 평균 — 평균이 흐르면 원인이 스쿱이 아니라 원료면이다\n")
        for key in list(SURFACES) + [k for k in by_surface if k not in SURFACES]:
            if key in by_surface:
                v = by_surface[key]
                w(f"  {key:8} n={len(v):2d}  평균 {sum(v) / len(v):6.2f} g\n")

    if s['n'] >= 3:
        third = max(1, s['n'] // 3)
        head, tail = delivered[:third], delivered[-third:]
        drift = sum(tail) / len(tail) - sum(head) / len(head)
        # 두 평균 차의 표준오차는 sd x sqrt(2/third) 다. σ 와 비교하면 우연을 흐름으로 읽는다
        # — n=15·third=5 면 우연한 차가 1.3 g 씩 나온다. 2 SE 를 넘을 때만 흐름으로 부른다.
        se = s['sd'] * math.sqrt(2.0 / third)
        w(f"\n  앞 {third}회 → 뒤 {third}회 추세  {drift:+.2f} g (2 SE = {2 * se:.2f} g)")
        w("  ← 흐름이다. 원료면을 먼저 의심한다\n" if abs(drift) > 2 * se
          else "  (우연 범위 — 흐름 없음)\n")

    # 레시피 구조(D-33·D-35)는 한 스쿱 목표 = nominal, 두 스쿱 목표 = 2 x nominal 이다.
    # 숫자를 박아 두면 nominal 이 바뀔 때(85 → 79, D-35) 완주율이 옛 목표로 계산된다.
    one, two = nominal, 2.0 * nominal
    recipes = {f'recipe-01 ({one:g}·{one:g}·{one:g})': [(one, tol)] * 3,
               f'recipe-02 ({two:g}·{one:g})': [(two, tol), (one, tol)],
               f'recipe-03 ({one:g}·{one:g}·{two:g})': [(one, tol), (one, tol), (two, tol)]}
    cr = completion_rates(delivered, recipes, nominal, max_attempts, trials, seed)
    w(f"\n■ 배치 완주율 — 측정값 부트스트랩 {trials}회, 판정 엔진 {cr['engine']}\n")
    for name in recipes:
        w(f"  {name:24} {cr[name]:5.1f} %\n")
    w("  (완주 = 모든 원료가 QA 없이 허용 구간에 들어옴)\n")

    notes = [r for r in rows if r['비고']]
    if notes:
        w("\n■ 비고가 적힌 회차\n")
        for r in notes:
            w(f"  회차 {r['회차']}: {r['비고']}\n")
    return {'delivered': s, 'scooped': p, 'residue': q, 'completion': cr}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='스쿱 1회 투입량 평균·σ 판정 (#272)')
    ap.add_argument('csv', nargs='?', help='기록 CSV')
    ap.add_argument('--template', nargs='?', const='pour', choices=['pour', 'three'],
                    help='빈 기록지를 표준출력으로. pour(기본)=붓기 방식 1회 계량, three=3회 계량')
    ap.add_argument('--nominal', type=float, default=79.0, help='기대 1회량 [g] (common.yaml dosing.scoop_nominal_g)')
    ap.add_argument('--tol', type=float, default=10.0, help='레시피 허용 오차 [%%]')
    ap.add_argument('--max-attempts', type=int, default=8, help='common.yaml dosing.max_attempts')
    ap.add_argument('--trials', type=int, default=20000, help='완주율 부트스트랩 횟수')
    ap.add_argument('--seed', type=int, default=272, help='부트스트랩 난수 씨앗 (재현용)')
    a = ap.parse_args(argv)

    if a.template:
        sys.stdout.write(POUR_TEMPLATE if a.template == 'pour' else TEMPLATE)
        return 0
    if not a.csv:
        ap.error('CSV 경로가 필요하다 (빈 기록지는 --template)')
    if not Path(a.csv).exists():
        sys.stderr.write(f'없는 파일: {a.csv}\n')
        return 2
    try:
        rows = load(a.csv)
    except RowError as exc:
        sys.stderr.write(f'기록 오류 — {exc}\n')
        return 2
    if len(rows) < 2:
        sys.stderr.write(f'채워진 회차가 {len(rows)} 개다 — 최소 2회, 권장 15회\n')
        return 2
    if len(rows) < 10:
        sys.stderr.write(f'⚠️ 회차 {len(rows)} — σ 추정 불확실도가 크다 (권장 15)\n\n')
    report(rows, a.nominal, a.tol, a.max_attempts, a.trials, a.seed)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
