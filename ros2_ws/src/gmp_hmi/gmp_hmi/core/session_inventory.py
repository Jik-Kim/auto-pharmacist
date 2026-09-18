"""D 화면 전용 세션 잔량 추정. B의 잔량 모델이나 ROS 계약을 대체하지 않는다.

최종 OK/OVER 결과의 actual_g를 원료별 누적 사용량으로 본다. UNDER는 중간 결과이므로
무시하며, 같은 배치·원료의 반복/역순 결과는 가장 큰 actual_g까지만 반영한다.
폐기 배치라도 이미 사용한 양은 되돌리지 않는다. 실제 유출·실패 중 부분 사용·보충을
관측하지 못하며, 재기동하면 초기 파라미터부터 다시 계산하므로 운영 재고 원장이 아니다.
호출자는 ROS/HTTP 공유 잠금을 잡고 접근한다.
"""
import math


class SessionInventory:
    def __init__(self, material_ids, capacity_g, initial_g, low_pct=20.0):
        if not (len(material_ids) == len(capacity_g) == len(initial_g)):
            raise ValueError('잔량 원료 ID·용량·초기량 배열 길이가 달라요')
        if not material_ids or len(set(material_ids)) != len(material_ids):
            raise ValueError('잔량 원료 ID는 비어 있지 않고 중복이 없어야 해요')
        if any(not isinstance(key, str) or not key.strip() for key in material_ids):
            raise ValueError('잔량 원료 ID는 비어 있지 않은 문자열이어야 해요')
        self.low_pct = float(low_pct)
        if not math.isfinite(self.low_pct) or not 0 <= self.low_pct <= 100:
            raise ValueError('잔량 알림 기준은 0~100%여야 해요')
        self._items = {}
        self._greatest = {}
        for key, capacity, initial in zip(material_ids, capacity_g, initial_g):
            capacity, initial = float(capacity), float(initial)
            if not math.isfinite(capacity) or not math.isfinite(initial):
                raise ValueError('잔량 설정에 NaN/무한대를 쓸 수 없어요')
            if capacity < 0 or (initial < 0 and initial != -1):
                raise ValueError('용량은 0 이상, 초기량은 0 이상 또는 미설정(-1)이어야 해요')
            if initial >= 0 and (capacity <= 0 or initial > capacity):
                raise ValueError('초기량을 설정하려면 용량이 양수이고 초기량 이상이어야 해요')
            self._items[key] = {'capacity_g': capacity or None,
                                'initial_g': initial if initial >= 0 else None,
                                'consumed_g': 0.0}

    def observe(self, batch_id, material_id, actual_g, verdict):
        """유효한 최종 결과만 반영. 인자 오류/미등록 원료/UNDER는 False 반환."""
        if not batch_id or material_id not in self._items or verdict not in ('OK', 'OVER'):
            return False
        try:
            actual = float(actual_g)
        except (ValueError, TypeError):
            return False
        if not math.isfinite(actual) or actual < 0:
            return False
        key = (batch_id, material_id)
        previous = self._greatest.get(key, 0.0)
        if actual <= previous:
            return False
        self._greatest[key] = actual
        self._items[material_id]['consumed_g'] += actual - previous
        return True

    def snapshot(self):
        items = []
        configured = False
        for key, values in self._items.items():
            item = dict(values, material_id=key)
            known = item['capacity_g'] is not None and item['initial_g'] is not None
            configured |= known
            remaining = max(0.0, item['initial_g'] - item['consumed_g']) if known else None
            percent = 100.0 * remaining / item['capacity_g'] if known else None
            item.update(remaining_g=remaining, percent=percent,
                        low=(percent <= self.low_pct) if known else False)
            items.append(item)
        return {'mode': 'session_estimate' if configured else 'unconfigured', 'items': items,
                'note': '세션 추정값 · 최종 OK/OVER만 차감 · 유출·실패 중 사용·보충 미반영 · HMI 재시작 시 초기화'}
