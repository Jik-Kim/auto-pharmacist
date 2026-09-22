"""stations.yaml 파싱과 접근점 계산. ROS 비의존 — 단위 테스트 대상.

yaml 형식 (gmp_bringup/params/stations.yaml):
  frame: base | user            # D-15 판 좌표계를 쓰면 user
  approach_mm: 60.0             # 작업점 위 접근 높이 (z+)
  stations:
    workbench: {posx: [x, y, z, a, b, c], note: "용기 파지 AT — 용기 계량은 ABOVE"}
"""
from dataclasses import dataclass, field
import math
from gmp_skills.core.transfer import parse_routes, vector6


@dataclass
class Station:
    station_id: str
    posx: list            # [x, y, z, a, b, c] mm·deg
    note: str = ''
    extra: dict = field(default_factory=dict)

    def offset_z(self, height_mm: float) -> list:
        """기준 자세에서 BASE Z 상대 높이를 적용한다. TOOL 방향과 무관하다."""
        if (isinstance(height_mm, bool) or not isinstance(height_mm, (int, float))
                or not math.isfinite(height_mm) or height_mm < 0):
            raise ValueError('BASE Z 높이는 유한한 0 이상 숫자여야 한다')
        p = list(vector6(self.posx, 'posx'))
        p[2] += height_mm
        return p

    def above(self, approach_mm: float) -> list:
        return self.offset_z(self.extra.get('approach_mm', approach_mm))

    def exit(self) -> list:
        return self.offset_z(self.extra.get('exit_mm'))


class StationTable:
    REQUIRED = ('safe', 'workbench')

    def __init__(self, data: dict):
        self.frame = data.get('frame', 'base')
        self.approach_mm = float(data.get('approach_mm', 60.0))
        self.stations = {}
        for sid, body in (data.get('stations') or {}).items():
            if any(k in body for k in ('pick_posx', 'pick_approach_mm', 'pick_exit_mm')):
                raise ValueError('파지 좌표는 posx와 approach_mm/exit_mm로 통합해야 한다')
            posx = body.get('posx')
            if posx is None or len(posx) != 6:
                raise ValueError(f'stations.yaml: {sid} 의 posx 는 6개여야 한다')
            if 'posj' in body and len(body['posj']) != 6:
                raise ValueError(f'stations.yaml: {sid} 의 posj 는 6개여야 한다')
            if 'solution_space' in body:
                sol = body['solution_space']
                if type(sol) is not int or not 0 <= sol <= 7 or 'posj' in body:
                    raise ValueError(f'{sid}: solution_space는 0~7 정수이며 posj와 함께 쓸 수 없다')
                height = body.get('approach_mm', self.approach_mm)
                exit_height = body.get('exit_mm')
                if (type(height) not in (int, float) or not math.isfinite(height) or height <= 0
                        or type(exit_height) not in (int, float)
                        or not math.isfinite(exit_height) or exit_height < height):
                    raise ValueError(f'{sid}: solution_space 접근에는 양수 접근 높이와 exit_mm가 필요하다')
                if self.frame != 'base':
                    raise ValueError('solution_space 이동은 BASE 좌표만 지원한다')
            self.stations[sid] = Station(sid, [float(v) for v in posx], body.get('note', ''),
                                         {k: v for k, v in body.items() if k not in ('posx', 'note')})
            for key in ('approach_mm', 'exit_mm'):
                if key in body:
                    self.stations[sid].offset_z(body[key])
        missing = [s for s in self.REQUIRED if s not in self.stations]
        if missing:
            raise ValueError(f'stations.yaml: 필수 스테이션 없음 {missing}')
        self.transfers = parse_routes(data.get('transfers', []), self.stations, self.approach_mm)
        if any('solution_space' in self.stations[r.destination].extra
               for r in self.transfers.values()):
            raise ValueError('solution_space 목적지에 이전 관절 이송 경로를 중복 등록할 수 없다')

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
