"""recipe.parse/load 테스트 — 레시피 yaml 의 스키마·검증 단일 출처 (docs/interfaces.md §4).

D 의 HMI 가 이 함수로 읽어 SubmitOrder 로 보낸다 (인라인 파싱 금지). 잘못된 레시피가
여기서 막히지 않으면 그대로 배치가 돈다.

투입 순서는 검사하지 않는다 — 계약 1절이 "순서 위반은 일탈이 아니라 버그" 라고 못박았다.
파서는 yaml 배열 순서를 그대로 보존하기만 한다.
"""
import pytest

from gmp_process.core.recipe import Item, RecipeSpec, load, parse


def _items(*specs):
    return {'product': 'T-01', 'items': list(specs)}


A = {'material_id': 'A', 'target_g': 200.0, 'tol_pct': 5.0}


# ── 정상 ──────────────────────────────────────────────────────────
def test_정상_파싱():
    spec = parse(_items(A, {'material_id': 'B', 'target_g': 100.0, 'tol_pct': 1.0, 'grade': 'ACTIVE'}))
    assert isinstance(spec, RecipeSpec) and spec.product == 'T-01'
    assert [i.material_id for i in spec.items] == ['A', 'B']
    assert spec.items[0] == Item('A', 200.0, 5.0, 0, 'A')
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


# ── 등급 ──────────────────────────────────────────────────────────
def test_grade_기본값은_EXCIPIENT():
    assert parse(_items(A)).items[0].grade == 0


@pytest.mark.parametrize('given,expected', [('EXCIPIENT', 0), ('ACTIVE', 1), (0, 0), (1, 1)])
def test_grade_는_문자열도_정수도_받는다(given, expected):
    spec = parse(_items(dict(A, grade=given)))
    assert spec.items[0].grade == expected


# ── 전용 스쿱 ─────────────────────────────────────────────────────
def test_scoop_id_생략하면_material_id_로_채운다():
    """전용 스쿱 = 교차오염 방지 (GMP). 비어 있으면 같은 이름의 스쿱을 쓴다."""
    assert parse(_items(A)).items[0].scoop_id == 'A'


def test_scoop_id_를_주면_그대로_쓴다():
    assert parse(_items(dict(A, scoop_id='SCOOP_X'))).items[0].scoop_id == 'SCOOP_X'


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


# ── 실제 시연 레시피 ──────────────────────────────────────────────
def test_demo_batch_yaml_이_파싱된다():
    """params/recipes/demo_batch.yaml — 시연에 실제로 쓰는 파일."""
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    p = os.path.normpath(os.path.join(here, '..', '..', 'gmp_bringup', 'params', 'recipes', 'demo_batch.yaml'))
    if not os.path.exists(p):
        pytest.skip('demo_batch.yaml 없음')
    spec = load(p)
    assert len(spec.items) == 3
    assert [i.material_id for i in spec.items] == ['A', 'B', 'C']
    assert all(i.scoop_id for i in spec.items)        # 전용 스쿱이 비어 있으면 안 된다
