"""HMI 목표는 수락 레시피의 용기 최종 총량 참고선이다. 스쿱 판정을 대체하지 않는다."""
import math


def validate_recipe_context(recipe):
    if not isinstance(recipe, dict) or not isinstance(recipe.get('items'), list) or not recipe['items']:
        raise ValueError('수락 레시피 문맥이 없습니다')
    items, seen = [], set()
    for item in recipe['items']:
        mid = item.get('material_id')
        target, tol = item.get('target_g'), item.get('tol_pct')
        if not isinstance(mid, str) or not mid or mid in seen:
            raise ValueError('원료 ID 오류')
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
               for v in (target, tol)) or target <= 0 or not 0 < tol <= 100:
            raise ValueError('목표량/허용오차 오류')
        seen.add(mid)
        items.append(dict(material_id=mid, target_g=target, tol_pct=tol))
    return dict(name=str(recipe.get('name', '')), product=str(recipe.get('product', '')), items=items,
                total_g=sum(i['target_g'] for i in items))


def target_band(recipe, batch_id):
    if not recipe or not batch_id:
        return None
    try:
        recipe = validate_recipe_context(recipe)
    except (ValueError, TypeError, KeyError, AttributeError):
        return None
    target = recipe['total_g']
    tolerance = sum(i['target_g'] * i['tol_pct'] / 100 for i in recipe['items'])
    return dict(batch_id=batch_id, subject='container', target_g=target,
                lower_g=target-tolerance, upper_g=target+tolerance,
                label='용기 최종 총량 기준 · 중간 계량의 합격 판정 아님')
