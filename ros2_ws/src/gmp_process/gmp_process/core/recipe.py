"""레시피 yaml → 검증된 dict. ROS 비의존. Recipe 메시지 변환은 nodes 가 한다."""
import math
from dataclasses import dataclass, field


@dataclass
class Item:
    material_id: str
    target_g: float
    tol_pct: float


@dataclass
class RecipeSpec:
    product: str
    items: list[Item] = field(default_factory=list)


def parse(data: dict) -> RecipeSpec:
    # 분해능 게이트(BRD 3.1.3 "target×tol 가 min_resolvable_g 아래인 주문 거부")는 일부러 없다.
    # 원료별 합격 판정이 VERIFY ①(배치 총량 대조)로 옮겨간 SOT Q-11(9/19 조장 확정) 이전 설계의
    # 요구사항이고, 그대로 넣으면 데모 레시피(200/150/100 g ±5 % → target×tol = 10/7.5/5 g)가
    # 전부 거부된다. 9/22 로 `min_resolvable_g` 자체가 사라졌으므로(VERIFY ② 폐지) 이 게이트는
    # 임계조차 없다 — 판정은 VERIFY ① 하나뿐이다. BRD v1.0 3.1.3·FR-01·TR-03 참조.
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
        target, tol = float(it['target_g']), float(it['tol_pct'])
        if not (math.isfinite(target) and math.isfinite(tol)):
            raise ValueError(f'items[{i}] target_g/tol_pct 는 유한한 수 (NaN·inf 불가)')
        if target <= 0 or tol <= 0:
            raise ValueError(f'items[{i}] target_g/tol_pct 는 양수')
        items.append(Item(it['material_id'], float(it['target_g']), float(it['tol_pct'])))
    return RecipeSpec(str(data.get('product', '')), items)


def load(path: str) -> RecipeSpec:
    import yaml
    with open(path, encoding='utf-8') as f:
        return parse(yaml.safe_load(f) or {})
