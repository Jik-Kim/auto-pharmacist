"""stations.yaml 에서 **이름만** 뽑는다 — 원료 ID → 스쿱·원료통 스테이션 ID.

좌표(posx·approach_mm)는 보지 않는다. 그건 skill_node 의 `gmp_skills/core/stations.py` 몫이고,
process 는 `MoveToStation.station_id` 에 넣을 문자열만 있으면 된다 (docs/interfaces.md 8절 C 항).

전용 스쿱은 원료통 아래에 둔다 (D-24, 9/18) — FSM 은 `station='scoop' + material_id` 만 넘기고
여기서 `material_id` 가 같은 `scoop_N` 을 찾는다. 짝이 0개거나 2개 이상이면 기동 때 바로 터뜨린다.
"""
from dataclasses import dataclass, field

SCOOP_PREFIX = 'scoop'
MATERIAL_PREFIX = 'material'


@dataclass
class StationMap:
    scoops: dict = field(default_factory=dict)      # material_id → scoop 스테이션 ID
    materials: dict = field(default_factory=dict)   # material_id → 원료통 스테이션 ID
    widths: dict = field(default_factory=dict)      # material_id → 기대 스쿱 손잡이 폭 [mm]

    @classmethod
    def from_data(cls, data: dict) -> 'StationMap':
        m = cls()
        for sid, body in ((data or {}).get('stations') or {}).items():
            mid = (body or {}).get('material_id')
            if not mid:
                continue
            table = m.scoops if sid.startswith(SCOOP_PREFIX) else (
                m.materials if sid.startswith(MATERIAL_PREFIX) else None)
            if table is None:
                continue
            if mid in table:
                raise ValueError(f'stations.yaml: 원료 {mid!r} 의 {sid.split("_")[0]} 스테이션이 둘 이상 '
                                 f'({table[mid]}, {sid})')
            table[mid] = sid
            if sid.startswith(MATERIAL_PREFIX) and 'expected_scoop_width_mm' in (body or {}):
                m.widths[mid] = float(body['expected_scoop_width_mm'])
        return m

    @classmethod
    def from_yaml(cls, path: str) -> 'StationMap':
        import yaml
        with open(path, encoding='utf-8') as f:
            return cls.from_data(yaml.safe_load(f) or {})

    def scoop_of(self, material_id: str) -> str:
        if material_id not in self.scoops:
            raise KeyError(f'원료 {material_id!r} 전용 스쿱 스테이션이 stations.yaml 에 없다. '
                           f'있는 것: {sorted(self.scoops)}')
        return self.scoops[material_id]

    def material_of(self, material_id: str) -> str:
        if material_id not in self.materials:
            raise KeyError(f'원료 {material_id!r} 원료통 스테이션이 stations.yaml 에 없다. '
                           f'있는 것: {sorted(self.materials)}')
        return self.materials[material_id]

    def check(self, material_ids) -> None:
        """레시피를 받자마자 부른다 — 배치 중간에 KeyError 로 서는 것보다 주문 거부가 낫다."""
        missing_scoops = [m for m in material_ids if m not in self.scoops]
        if missing_scoops:
            raise KeyError(f'전용 스쿱이 없는 원료 {missing_scoops} — stations.yaml 의 scoop_N 을 확인하라')
        missing_materials = [m for m in material_ids if m not in self.materials]
        if missing_materials:
            raise KeyError(f'원료통 스테이션이 없는 원료 {missing_materials} — stations.yaml 의 material_N 을 확인하라')
