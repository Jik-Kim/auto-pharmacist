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


def test_station_specific_heights_do_not_change_other_stations():
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / 'gmp_bringup/params/stations.yaml'
    t = StationTable.from_yaml(path)
    for name in ('passbox_empty', 'passbox_done', 'reject_bin'):
        st = t.get(name)
        assert st.posx[2] == 100
        assert st.above(t.approach_mm) == st.posx[:2] + [150] + st.posx[3:]
        assert st.exit() == st.posx[:2] + [250] + st.posx[3:]
    wb = t.get('workbench')
    assert wb.above(t.approach_mm) == [423, 93, 200, 90, -90, -90]
    assert wb.exit() == [423, 93, 300, 90, -90, -90]
    assert wb.posx == [423, 93, 100, 90, -90, -90]
    assert 'pick_posx' not in wb.extra
    assert t.get('material_1').above(t.approach_mm)[2] == 260
    assert t.get('scoop_1').above(t.approach_mm)[2] == 110
    assert 'posj' not in t.get('nudge_wait').extra
    assert t.get('nudge_wait').extra['taught_at_posj'][0] == 14.57


@pytest.mark.parametrize('height', [-1, float('nan'), float('inf'), True, '50'])
def test_bad_relative_height_is_rejected(height):
    st = _table().get('workbench')
    with pytest.raises(ValueError):
        st.offset_z(height)


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
        'scoop_1': {'posx': [0] * 6, 'material_id': 'A'},
    }})
    assert table.for_material('A').extra['expected_scoop_width_mm'] == 15.5
    with pytest.raises(KeyError):
        table.for_material('B')
