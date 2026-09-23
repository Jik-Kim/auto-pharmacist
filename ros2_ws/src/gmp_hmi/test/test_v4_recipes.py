"""확정된 세 레시피가 C의 실제 로더와 원료 ID 계약에 맞는지 검사한다."""
from pathlib import Path

from gmp_process.core.recipe import load


def test_fixed_recipe_catalog_uses_shared_loader_and_material_ids():
    directory = Path(__file__).resolve().parents[1] / 'config' / 'test_recipes' / 'v4'
    # SOT D-33 (9/23 조장) — 고정 스쿱 85 g 에 맞춘 목표량, 허용오차 ±10 %.
    expected = {
        'recipe-01': [('A', 85.0), ('B', 85.0), ('C', 85.0)],
        'recipe-02': [('A', 170.0), ('B', 85.0)],
        'recipe-03': [('A', 85.0), ('B', 85.0), ('C', 170.0)],
    }
    assert {p.stem for p in directory.glob('*.yaml')} == set(expected)
    for name, items in expected.items():
        recipe = load(str(directory / (name + '.yaml')))
        assert [(item.material_id, item.target_g) for item in recipe.items] == items
        assert all(item.tol_pct == 10.0 for item in recipe.items)
        assert recipe.product == f'레시피 {int(name[-2:])}'


def test_test_recipes_match_production_recipes():
    """시험 사본이 운영 레시피와 같은지. 9/23 #271 이 운영만 85/170 g ±10 % 로 바꾸고
    사본은 40/80 g ±5 % 로 남았는데, 위 시험은 사본끼리만 비교해서 못 잡았다."""
    here = Path(__file__).resolve().parents[1] / 'config' / 'test_recipes' / 'v4'
    production = Path(__file__).resolve().parents[2] / 'gmp_bringup' / 'params' / 'recipes'
    assert {p.stem for p in here.glob('*.yaml')} == {p.stem for p in production.glob('*.yaml')}
    for path in sorted(production.glob('*.yaml')):
        ours, theirs = load(str(here / path.name)), load(str(path))
        assert ours.product == theirs.product, path.name
        assert [(i.material_id, i.target_g, i.tol_pct) for i in ours.items] == \
            [(i.material_id, i.target_g, i.tol_pct) for i in theirs.items], path.name
