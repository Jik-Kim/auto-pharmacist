"""원료 → 스테이션 이름 해석. FSM 은 'scoop' 이라고만 말하고 여기서 scoop_N 이 된다 (D-24)."""
import os

import pytest

from gmp_process.core.station_map import StationMap

YAML = os.path.join(os.path.dirname(__file__), '..', '..', 'gmp_bringup', 'params', 'stations.yaml')


def test_real_stations_yaml_resolves_every_scoop():
    m = StationMap.from_yaml(os.path.abspath(YAML))
    assert m.scoop_of('A') == 'scoop_1'
    assert m.scoop_of('C') == 'scoop_3'
    assert m.material_of('B') == 'material_2'
    assert m.widths['A'] == 15.5


def test_unknown_material_says_what_exists():
    m = StationMap.from_data({'stations': {'scoop_1': {'material_id': 'A'}}})
    with pytest.raises(KeyError) as e:
        m.scoop_of('Z')
    assert 'A' in str(e.value)          # 무엇이 있는지 알려줘야 티칭 실수를 찾는다


def test_duplicate_material_is_a_teaching_mistake():
    with pytest.raises(ValueError):
        StationMap.from_data({'stations': {'scoop_1': {'material_id': 'A'},
                                           'scoop_9': {'material_id': 'A'}}})


def test_check_rejects_before_the_batch_starts():
    m = StationMap.from_data({'stations': {'scoop_1': {'material_id': 'A'}}})
    with pytest.raises(KeyError, match='원료통'):
        m.check(['A'])
    m.materials['A'] = 'material_1'
    m.check(['A'])
    with pytest.raises(KeyError):
        m.check(['A', 'B'])


def test_non_station_entries_are_ignored():
    """workbench·passbox 처럼 material_id 가 없는 것은 지도에 들어가지 않는다."""
    m = StationMap.from_data({'stations': {'workbench': {'posx': [0] * 6},
                                           'material_1': {'material_id': 'A'},
                                           'scoop_1': {'material_id': 'A'}}})
    assert m.scoops == {'A': 'scoop_1'} and m.materials == {'A': 'material_1'}
