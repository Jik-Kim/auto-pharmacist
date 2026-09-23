"""measure_g1.py 의 빈 그리퍼 기준값(영점) — 9/23 에 고친 결함이 되돌아오지 않게 묶는다.

고치기 전 상태: 표본 10개 하드코딩(`--samples` 무시) · CSV 미기록 · `--no-workpiece` 미적용 ·
영점을 측정 자세가 아닌 AT 에서 측정. 앞의 셋은 여기서 잡고, 자세는 호출부 주석이 지킨다.

`measure_g1.py` 는 패키지가 아니고 최상위에서 DSR_ROBOT2 를 import 하므로, 스텁을 심고
파일 경로로 직접 읽어 들인다.
"""
import csv
import importlib.util
import io
import sys
import types
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / 'calibration' / 'measure_g1.py'


@pytest.fixture(scope='module')
def mod():
    for name in ('DSR_ROBOT2', 'DR_init'):
        sys.modules.setdefault(name, types.ModuleType(name))
    spec = importlib.util.spec_from_file_location('measure_g1_under_test', _SRC)
    m = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(m)
    except SystemExit:      # 인자 없이 읽으면 argparse 가 종료할 수 있다 — 정의만 필요하다
        pass
    return m


class _Arm:
    """tool_force 는 매번 조금 다른 값을, get_workpiece_weight 는 호출 횟수를 센다."""

    def __init__(self):
        self.R = types.SimpleNamespace(calls=0, get_workpiece_weight=self._wp)
        self.n = 0

    def _wp(self):
        self.R.calls += 1
        return 0.001

    def tool_force(self):
        self.n += 1
        return [0.0, 0.0, -1.3 - 0.01 * self.n, 0.0, 0.0, 0.0]


def test_baseline_records_every_requested_sample(mod):
    """표본 수가 호출자(= --samples)를 따라야 한다. 10 하드코딩이면 저주파 주기를 못 덮는다."""
    buf = io.StringIO()
    mod.baseline(_Arm(), 25, 0.0, no_workpiece=True, writer=csv.writer(buf), t0=0.0,
                 row=['cup_baseline', 'empty', 'cond', '0', 1])
    rows = list(csv.reader(io.StringIO(buf.getvalue())))
    assert len(rows) == 25
    assert [r[5] for r in rows[:3]] == ['1', '2', '3']
    assert len(rows[0]) == len(mod.COLUMNS)      # 본문과 같은 열 구성이어야 함께 분석된다


def test_baseline_honours_no_workpiece(mod):
    """--no-workpiece 는 폐기된 get_workpiece_weight() 를 부르지 않는다는 뜻이다."""
    off = _Arm()
    mod.baseline(off, 3, 0.0, no_workpiece=True)
    assert off.R.calls == 0

    on = _Arm()
    mod.baseline(on, 3, 0.0)
    assert on.R.calls == 3


def test_baseline_returns_mean_without_writer(mod):
    """writer 를 안 줘도 동작해야 한다 — 기록은 선택이고 측정은 필수다."""
    assert mod.baseline(_Arm(), 5, 0.0, no_workpiece=True) > 0
