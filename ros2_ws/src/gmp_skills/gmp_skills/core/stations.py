"""stations.yaml 파싱과 접근점 계산. ROS 비의존 — 단위 테스트 대상.

yaml 형식 (gmp_bringup/params/stations.yaml):
  frame: base | user            # D-15 판 좌표계를 쓰면 user
  approach_mm: 60.0             # 작업점 위 접근 높이 (z+)
  stations:
    workbench: {posx: [x, y, z, a, b, c], note: "작업·계량 구역 — 계량 자세. 영점도 여기서"}
"""
from dataclasses import dataclass, field
from gmp_skills.core.transfer import parse_routes


@dataclass
class Station:
    station_id: str
    posx: list            # [x, y, z, a, b, c] mm·deg
    note: str = ''
    extra: dict = field(default_factory=dict)

    def above(self, approach_mm: float) -> list:
        """작업점 위 접근점. z 만 올린다 — 툴 z 가 아래를 보는 자세를 전제한다 (계량·스쿱 모두)."""
        p = list(self.posx)
        p[2] = p[2] + approach_mm
        return p


class StationTable:
    REQUIRED = ('safe', 'workbench')

    def __init__(self, data: dict):
        self.frame = data.get('frame', 'base')
        self.approach_mm = float(data.get('approach_mm', 60.0))
        self.stations = {}
        for sid, body in (data.get('stations') or {}).items():
            posx = body.get('posx')
            if posx is None or len(posx) != 6:
                raise ValueError(f'stations.yaml: {sid} 의 posx 는 6개여야 한다')
            if 'posj' in body and len(body['posj']) != 6:
                raise ValueError(f'stations.yaml: {sid} 의 posj 는 6개여야 한다')
            self.stations[sid] = Station(sid, [float(v) for v in posx], body.get('note', ''),
                                         {k: v for k, v in body.items() if k not in ('posx', 'note')})
        missing = [s for s in self.REQUIRED if s not in self.stations]
        if missing:
            raise ValueError(f'stations.yaml: 필수 스테이션 없음 {missing}')
        self.transfers = parse_routes(data.get('transfers', []), self.stations)

    def get(self, station_id: str) -> Station:
        if station_id not in self.stations:
            raise KeyError(f'모르는 스테이션 {station_id!r}. 있는 것: {sorted(self.stations)}')
        return self.stations[station_id]

    def for_material(self, material_id: str) -> Station:
        # material_N과 scoop_N은 같은 material_id를 공유한다. 원료통을 찾는 이 메서드가
        # 스쿱 거치대까지 함께 세면 항상 2개가 되어 Scoop이 시작도 못 한다.
        matches = [s for s in self.stations.values()
                   if s.station_id.startswith('material_')
                   and s.extra.get('material_id') == material_id]
        if len(matches) != 1:
            raise KeyError(f'material_id {material_id!r} 원료 스테이션은 1개여야 한다: {len(matches)}개')
        return matches[0]

    @classmethod
    def from_yaml(cls, path: str) -> 'StationTable':
        import yaml
        with open(path, encoding='utf-8') as f:
            return cls(yaml.safe_load(f) or {})
