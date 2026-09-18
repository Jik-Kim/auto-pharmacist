import pytest
from gmp_skills.core.stations import StationTable


def _table():
    return StationTable({'approach_mm': 50.0, 'stations': {
        'safe': {'posx': [300, 0, 400, 0, 180, 0]},
        'workbench': {'posx': [350, 320, 200, 0, 180, 0], 'note': '계량'},
    }})


def test_above_raises_z_only():
    t = _table()
    assert t.get('workbench').above(t.approach_mm) == [350, 320, 250, 0, 180, 0]


def test_missing_required():
    with pytest.raises(ValueError):
        StationTable({'stations': {'safe': {'posx': [0] * 6}}})


def test_unknown_station():
    with pytest.raises(KeyError):
        _table().get('nope')


def test_safe_joint_pose_stays_in_yaml_extra_fields():
    table = StationTable({'stations': {
        'safe': {'posx': [300, 0, 450, 0, 180, 0], 'posj': [0, 0, 90, 0, 90, 0]},
        'workbench': {'posx': [0] * 6},
    }})
    assert table.get('safe').extra['posj'] == [0, 0, 90, 0, 90, 0]


def test_material_width_fingerprint_lookup():
    table = StationTable({'stations': {
        'safe': {'posx': [0] * 6},
        'workbench': {'posx': [0] * 6},
        'material_1': {'posx': [0] * 6, 'material_id': 'A', 'expected_scoop_width_mm': 15.5},
    }})
    assert table.for_material('A').extra['expected_scoop_width_mm'] == 15.5
    with pytest.raises(KeyError):
        table.for_material('B')
