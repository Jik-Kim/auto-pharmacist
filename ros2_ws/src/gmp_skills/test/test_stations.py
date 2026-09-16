import pytest
from gmp_skills.core.stations import StationTable


def _table():
    return StationTable({'approach_mm': 50.0, 'stations': {
        'safe': {'posx': [300, 0, 400, 0, 180, 0]},
        'scale': {'posx': [350, 320, 200, 0, 180, 0], 'note': '계량'},
    }})


def test_above_raises_z_only():
    t = _table()
    assert t.get('scale').above(t.approach_mm) == [350, 320, 250, 0, 180, 0]


def test_missing_required():
    with pytest.raises(ValueError):
        StationTable({'stations': {'safe': {'posx': [0] * 6}}})


def test_unknown_station():
    with pytest.raises(KeyError):
        _table().get('nope')
