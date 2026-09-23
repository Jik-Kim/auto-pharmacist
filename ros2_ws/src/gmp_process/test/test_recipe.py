"""recipe.parse/load 테스트 — 레시피 yaml 의 스키마·검증 단일 출처 (docs/interfaces.md §4).

D 의 HMI 가 이 함수로 읽어 SubmitOrder 로 보낸다 (인라인 파싱 금지). 잘못된 레시피가
여기서 막히지 않으면 그대로 배치가 돈다.

투입 순서는 검사하지 않는다 — 계약 1절이 "순서 위반은 일탈이 아니라 버그" 라고 못박았다.
파서는 yaml 배열 순서를 그대로 보존하기만 한다.
"""
import glob
import os

import pytest

from gmp_process.core.recipe import Item, RecipeSpec, load, parse


def _items(*specs):
    return {'product': 'T-01', 'items': list(specs)}


A = {'material_id': 'A', 'target_g': 200.0, 'tol_pct': 5.0}


# ── 유한값 ──────────────────────────────────────────────────────
@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -float('inf')])
def test_NaN_inf_는_거부(bad):
    with pytest.raises(ValueError, match='유한'):
        parse(_items({'material_id': 'A', 'target_g': bad, 'tol_pct': 5.0}))
    with pytest.raises(ValueError, match='유한'):
        parse(_items({'material_id': 'A', 'target_g': 100.0, 'tol_pct': bad}))


# ── 정상 ──────────────────────────────────────────────────────────
def test_정상_파싱():
    spec = parse(_items(A, {'material_id': 'B', 'target_g': 100.0, 'tol_pct': 1.0}))
    assert isinstance(spec, RecipeSpec) and spec.product == 'T-01'
    assert [i.material_id for i in spec.items] == ['A', 'B']
    assert spec.items[0] == Item('A', 200.0, 5.0)
    assert spec.items[1].target_g == 100.0 and spec.items[1].tol_pct == 1.0


def test_배열_순서를_그대로_보존한다():
    """투입 순서 = 배열 순서 (계약 1절). 파서가 정렬하거나 재배치하면 안 된다."""
    order = ['C', 'A', 'B']
    spec = parse(_items(*({'material_id': m, 'target_g': 50.0, 'tol_pct': 5.0} for m in order)))
    assert [i.material_id for i in spec.items] == order


def test_product_없으면_빈_문자열():
    assert parse({'items': [A]}).product == ''


def test_숫자를_문자열로_줘도_float_로_변환된다():
    spec = parse(_items({'material_id': 'A', 'target_g': '200', 'tol_pct': '5'}))
    assert spec.items[0].target_g == 200.0 and isinstance(spec.items[0].target_g, float)


# ── 계약에 없는 키는 조용히 무시한다 ──────────────────────────────
def test_모르는_키는_무시한다():
    """v1.2 에서 grade·scoop_id 가 계약에서 빠졌다. 옛 레시피 yaml 이 들어와도 깨지지 않아야 한다.
    전용 스쿱은 이제 stations.yaml 의 scoop_N (material_id 짝) 이 정한다."""
    spec = parse(_items(dict(A, grade='ACTIVE', scoop_id='SCOOP_X')))
    assert spec.items[0] == Item('A', 200.0, 5.0)


# ── 검증 (이게 안 돌면 잘못된 배치가 그대로 실행된다) ────────────────
def test_items_없으면_거부():
    with pytest.raises(ValueError, match='items'):
        parse({'product': 'T-01'})


def test_items_가_비어_있어도_거부():
    with pytest.raises(ValueError, match='items'):
        parse(_items())


@pytest.mark.parametrize('missing', ['material_id', 'target_g', 'tol_pct'])
def test_필수_필드_누락_거부(missing):
    with pytest.raises(ValueError, match=missing):
        parse(_items({k: v for k, v in A.items() if k != missing}))


def test_원료_중복_거부():
    """한 배치에 같은 원료를 두 번 넣으면 투입량 누적이 꼬인다."""
    with pytest.raises(ValueError, match='중복'):
        parse(_items(A, dict(A, target_g=50.0)))


@pytest.mark.parametrize('field,value', [
    ('target_g', 0), ('target_g', -10), ('tol_pct', 0), ('tol_pct', -1),
])
def test_target_g_tol_pct_는_양수여야_한다(field, value):
    with pytest.raises(ValueError, match='양수'):
        parse(_items(dict(A, **{field: value})))


# ── load (파일 경로) ──────────────────────────────────────────────
def test_load_가_yaml_파일을_읽는다(tmp_path):
    p = tmp_path / 'r.yaml'
    p.write_text('product: DEMO\nitems:\n  - {material_id: A, target_g: 200, tol_pct: 5}\n', encoding='utf-8')
    spec = load(str(p))
    assert spec.product == 'DEMO' and spec.items[0].material_id == 'A'


def test_load_도_같은_검증을_거친다(tmp_path):
    p = tmp_path / 'bad.yaml'
    p.write_text('product: DEMO\nitems:\n  - {material_id: A, target_g: -1, tol_pct: 5}\n', encoding='utf-8')
    with pytest.raises(ValueError, match='양수'):
        load(str(p))


def test_빈_파일은_items_없음으로_거부(tmp_path):
    p = tmp_path / 'empty.yaml'
    p.write_text('', encoding='utf-8')
    with pytest.raises(ValueError, match='items'):
        load(str(p))


# ── 운영 레시피 ────────────────────────────────────────────────
# `gmp_bringup/params/recipes/` 의 실물 yaml 을 **있는 그대로** 읽는다 (#161·#217).
# 종전에는 `demo_batch.yaml` 한 개를 이름으로 찾고 없으면 `pytest.skip` 했는데,
# 그 파일이 9/16 에 지워진 뒤로 **영구 skip** 이라 커버리지가 0 이었다.
# 파일이 사라지면 **건너뛰지 말고 실패해야 한다** — 그게 이 시험의 요점이다.

RECIPE_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', '..', 'gmp_bringup', 'params', 'recipes'))


def _recipe_files():
    return sorted(glob.glob(os.path.join(RECIPE_DIR, '*.yaml')))


def test_운영_레시피가_최소_한_개는_등록돼_있다():
    """레시피가 없으면 셀이 아무 주문도 못 받는다. 없어졌는데 조용히 넘어가지 않는다."""
    assert _recipe_files(), f'{RECIPE_DIR} 에 레시피 yaml 이 없다'


@pytest.mark.parametrize('path', _recipe_files(), ids=lambda p: os.path.basename(p))
def test_운영_레시피가_파서를_통과한다(path):
    """D 의 HMI 가 이 함수로 읽어 SubmitOrder 로 보낸다 — 여기서 막히지 않으면 배치가 그대로 돈다.

    항목 수·원료 조합은 고정하지 않는다. recipe-02 는 C 를 쓰지 않아 항목이 2개다
    (0 g 항목 금지라 아예 빼는 것이 맞다). 레시피마다 다른 것을 단언하면 레시피가
    바뀔 때마다 시험이 깨지고, 그러면 시험을 고치느라 정작 값을 안 본다.
    """
    spec = load(path)
    assert spec.product.strip(), '제품명이 비어 있다'
    assert spec.items, '항목이 없다'
    ids = [i.material_id for i in spec.items]
    assert len(ids) == len(set(ids)), f'원료가 중복됐다: {ids}'
    assert set(ids) <= {'A', 'B', 'C'}, f'모르는 원료: {sorted(set(ids) - {"A", "B", "C"})}'
    for i in spec.items:
        assert i.target_g > 0 and i.tol_pct > 0, f'{i.material_id}: {i.target_g} g ±{i.tol_pct} %'
