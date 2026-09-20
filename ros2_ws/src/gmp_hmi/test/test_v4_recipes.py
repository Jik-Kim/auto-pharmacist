"""확정된 세 레시피가 C의 실제 로더와 원료 ID 계약에 맞는지 검사한다."""
from pathlib import Path

from gmp_process.core.recipe import load


def test_fixed_recipe_catalog_uses_shared_loader_and_material_ids():
    directory = Path(__file__).resolve().parents[1] / 'config' / 'test_recipes' / 'v4'
    expected = {
        'recipe-01': [('A', 40.0), ('B', 40.0), ('C', 40.0)],
        'recipe-02': [('A', 80.0), ('B', 40.0)],
        'recipe-03': [('A', 40.0), ('B', 40.0), ('C', 80.0)],
    }
    assert {p.stem for p in directory.glob('*.yaml')} == set(expected)
    for name, items in expected.items():
        recipe = load(str(directory / (name + '.yaml')))
        assert [(item.material_id, item.target_g) for item in recipe.items] == items
        assert all(item.tol_pct == 5.0 for item in recipe.items)
        assert recipe.product == f'레시피 {int(name[-2:])}'
