"""레시피 yaml → 검증된 dict. ROS 비의존. Recipe 메시지 변환은 nodes 가 한다."""
from dataclasses import dataclass, field

GRADES = {'EXCIPIENT': 0, 'ACTIVE': 1}


@dataclass
class Item:
    material_id: str
    target_g: float
    tol_pct: float
    grade: int = 0
    scoop_id: str = ''


@dataclass
class RecipeSpec:
    product: str
    items: list[Item] = field(default_factory=list)


def parse(data: dict) -> RecipeSpec:
    if not data.get('items'):
        raise ValueError('레시피에 items 가 없다')
    items = []
    seen = set()
    for i, it in enumerate(data['items']):
        for k in ('material_id', 'target_g', 'tol_pct'):
            if k not in it:
                raise ValueError(f'items[{i}] 에 {k} 없음')
        if it['material_id'] in seen:
            raise ValueError(f'원료 중복 {it["material_id"]!r} — 한 배치에 같은 원료를 두 번 넣지 않는다')
        seen.add(it['material_id'])
        if float(it['target_g']) <= 0 or float(it['tol_pct']) <= 0:
            raise ValueError(f'items[{i}] target_g/tol_pct 는 양수')
        grade = it.get('grade', 'EXCIPIENT')
        items.append(Item(it['material_id'], float(it['target_g']), float(it['tol_pct']),
                          GRADES[grade] if isinstance(grade, str) else int(grade),
                          it.get('scoop_id') or it['material_id']))
    return RecipeSpec(str(data.get('product', '')), items)


def load(path: str) -> RecipeSpec:
    import yaml
    with open(path, encoding='utf-8') as f:
        return parse(yaml.safe_load(f) or {})
