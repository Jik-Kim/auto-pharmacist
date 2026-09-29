"""가상 계량 흔들림 재생 자료(#156) — manifest 와 CSV 가 서로 맞는지.

A 의 재생 구현은 manifest 의 창(행 범위)만 믿고 CSV 를 자른다. 파일을 바꾸거나 창을 손으로 고치다
어긋나면 다른 회차·다른 하중이 한 창에 섞여 흔들림이 부풀어도 조용히 지나간다 — 그래서 여기서 막는다.
"""
import csv
import statistics
from pathlib import Path

import pytest
import yaml

DIR = Path(__file__).resolve().parents[1] / 'calibration' / 'virtual_weigh'
MANIFEST = yaml.safe_load((DIR / 'manifest.yaml').read_text(encoding='utf-8'))
STATES = {'container_empty', 'container_loaded', 'scoop_empty', 'scoop_loaded'}


def _rows(path):
    with open(DIR / path, encoding='utf-8') as fh:
        return list(csv.DictReader(fh))


def _windows():
    for f in MANIFEST['files']:
        for w in f['windows']:
            yield f, w


def test_the_four_requested_states_are_declared():
    assert set(MANIFEST['states']) == STATES
    assert {s['status'] for s in MANIFEST['states'].values()} <= {'ready', 'pending'}


@pytest.mark.parametrize('f', MANIFEST['files'], ids=lambda f: f['path'])
def test_windows_tile_the_file_without_gaps_or_overlap(f):
    rows = _rows(f['path'])
    spans = sorted(tuple(w['rows']) for w in f['windows'])
    assert spans[0][0] == 1 and spans[-1][1] == len(rows)
    for (_, end), (start, _) in zip(spans, spans[1:]):
        assert start == end + 1


@pytest.mark.parametrize('f,w', list(_windows()), ids=lambda x: str(x.get('rows', x.get('path'))))
def test_each_window_is_one_trial_at_the_declared_load(f, w):
    rows = _rows(f['path'])
    a, b = w['rows']
    win = rows[a - 1:b]
    keys = {(r['실험명'], r['반복번호']) for r in win}
    assert len(keys) == 1, f'{f["path"]} {w["rows"]}: 회차 {len(keys)}개가 한 창에 섞였다'
    assert {float(r['실제총무게_g']) for r in win} == {float(w['load_g'])}
    assert len(win) == f['sampling']['samples']
    assert w['state'] in STATES


def test_ready_states_have_usable_windows_and_pending_states_have_none():
    used = {w['state'] for _, w in _windows() if w['use']}
    for name, s in MANIFEST['states'].items():
        assert (name in used) == (s['status'] == 'ready'), name


def test_usable_windows_look_like_noise_not_a_changing_load():
    # 9/23 용기 창 σ 는 0.055~0.094 N 이고 오염 창 하나만 0.600 N 이었다. 사이의 넉넉한 선.
    col = MANIFEST['value']['column']
    for f, w in _windows():
        z = [float(r[col]) for r in _rows(f['path'])[w['rows'][0] - 1:w['rows'][1]]]
        sd = statistics.stdev(z)
        assert (sd < 0.2) == w['use'], f'{f["path"]} {w["rows"]}: σ {sd:.3f} N, use={w["use"]}'


def test_window_centering_is_what_keeps_regrip_steps_out_of_the_noise():
    """파일 평균을 빼면 재파지 계단이 흔들림에 들어간다 — manifest 머리말의 수치를 붙잡아 둔다."""
    f = next(f for f in MANIFEST['files'] if f['path'] == 'g1_0923_regrip229.csv')
    col = MANIFEST['value']['column']
    rows = _rows(f['path'])
    wins = [[float(r[col]) for r in rows[w['rows'][0] - 1:w['rows'][1]]] for w in f['windows']]
    window_sd = statistics.pstdev([z - statistics.mean(v) for v in wins for z in v])
    file_sd = statistics.pstdev([z for v in wins for z in v])
    assert file_sd > 1.3 * window_sd
