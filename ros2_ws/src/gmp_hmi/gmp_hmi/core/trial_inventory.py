"""/hmi_test 전용 재고 장부. 실제 C의 재고 계약이나 물리 계량 모델이 아니다.

단일 공정 콜백에서 주문 전체를 예약하고, 생성한 분주 결과만 소비한다.
가상 스쿱 잔류량은 원료통에 반환된 것으로 가정한다(손실 모델 없음).
"""
import math
import uuid


class TrialInventory:
    def __init__(self, materials, capacities, initial, height_low_pct=20.0):
        if type(height_low_pct) not in (int, float) or not math.isfinite(height_low_pct) or not 0 < height_low_pct < 100:
            raise ValueError('시험 높이 기준은 0~100 사이여야 합니다')
        self.height_low_pct = float(height_low_pct)
        if not materials or len(set(materials)) != len(materials):
            raise ValueError('시험 원료 ID가 비어 있거나 중복되었습니다')
        if not (len(materials) == len(capacities) == len(initial)):
            raise ValueError('시험 재고 파라미터의 길이가 다릅니다')
        self.items = {}
        for mid, capacity, remaining in zip(materials, capacities, initial):
            if not isinstance(mid, str) or not mid or not all(
                isinstance(v, (float, int)) and not isinstance(v, bool) and math.isfinite(v)
                for v in (capacity, remaining)
            ) or not 0 < capacity or not 0 <= remaining <= capacity:
                raise ValueError('시험 재고에는 유효한 원료 ID와 0 ≤ 초기량 ≤ 양수 용량이 필요합니다')
            self.items[mid] = dict(material_id=mid, capacity_g=float(capacity),
                                   remaining_g=float(remaining), reserved_g=0.0,
                                   height_pct=None, height_low_latched=False, height_status='측정 대기')
        self.instance_id = uuid.uuid4().hex
        self.revision = 0
        self.batch_id = ''

    def reserve(self, requirements, batch_id):
        if self.blocked_materials:
            raise ValueError('원료 높이 부족: ' + ', '.join(self.blocked_materials) + ' · 해당 원료 만충 보충 완료가 필요합니다')
        if self.batch_id:
            raise ValueError('이미 시험 재고를 예약한 배치가 있습니다')
        if not requirements or not batch_id:
            raise ValueError('예약할 원료와 배치 ID가 필요합니다')
        # 전부 검증한 뒤 한 번에 변경한다. 부분 예약은 남기지 않는다.
        for mid, amount in requirements.items():
            if not isinstance(amount, (int, float)) or isinstance(amount, bool) or not math.isfinite(amount) or amount <= 0:
                raise ValueError('예약량은 양수여야 합니다')
            item = self.items.get(mid)
            if item is None:
                raise ValueError(f'시험 재고 미등록 원료: {mid}')
            if item['remaining_g'] + 1e-8 < amount:
                raise ValueError(f'시험 원료 부족: {mid} 필요 {amount:g} g / 잔량 {item["remaining_g"]:g} g · 만충 보충 후 주문하세요')
        for mid, amount in requirements.items():
            self.items[mid]['reserved_g'] = float(amount)
        self.batch_id = batch_id
        self.revision += 1

    def consume(self, mid, amount):
        item = self.items[mid]
        if not self.batch_id or not math.isfinite(amount) or amount <= 0 or amount > item['reserved_g'] + 1e-8 or amount > item['remaining_g'] + 1e-8:
            raise ValueError('시험 분주량이 예약량 또는 잔량을 초과했습니다')
        item['remaining_g'] = max(0.0, item['remaining_g'] - amount)
        item['reserved_g'] = max(0.0, item['reserved_g'] - amount)
        self.revision += 1

    def release(self):
        for item in self.items.values():
            item['reserved_g'] = 0.0
        self.batch_id = ''
        self.revision += 1

    @property
    def blocked_materials(self):
        return [mid for mid, item in self.items.items() if item['height_low_latched']]

    def report_height(self, mid, height_pct):
        if not isinstance(mid, str) or mid not in self.items:
            raise ValueError('시험 재고 미등록 원료')
        if type(height_pct) not in (int, float) or not math.isfinite(height_pct) or not 0 <= height_pct <= 100:
            raise ValueError('원료 높이는 0~100 사이의 유한한 숫자여야 합니다')
        item = self.items[mid]
        item['height_pct'] = float(height_pct)
        # 높은 측정값이나 팝업 닫기로 부족 이력을 해제하지 않는다.
        item['height_low_latched'] = item['height_low_latched'] or height_pct < self.height_low_pct
        item['height_status'] = '시험 높이 수신'
        self.revision += 1

    def refill(self, mid):
        # 실행 가능 상태는 공정 노드가 검증한다. 현재 예약은 그대로 유지한다.
        if not isinstance(mid, str) or mid not in self.items:
            raise ValueError('시험 재고 미등록 원료')
        item = self.items[mid]
        added = item['capacity_g'] - item['remaining_g']
        item['remaining_g'] = item['capacity_g']
        item['height_low_latched'] = False
        # 만충 확인은 g 장부를 초기화할 뿐 로봇의 높이 실측을 만들어내지 않는다.
        item['height_pct'] = None
        item['height_status'] = '보충 확인 · 재측정 대기'
        self.revision += 1
        return added

    def snapshot(self, can_refill):
        return dict(schema='hmi_test.inventory.v2', instance_id=self.instance_id,
                    revision=self.revision, batch_id=self.batch_id, can_refill=bool(can_refill),
                    height_low_pct=self.height_low_pct, blocked_materials=self.blocked_materials,
                    items=[dict(item, available_g=max(0.0, item['remaining_g'] - item['reserved_g']),
                                percent=100.0 * item['remaining_g'] / item['capacity_g'])
                           for item in self.items.values()])


def validate_trial_snapshot(data):
    """시험 JSON도 불완전/비정상 값은 주문 판단에 사용하지 않는다."""
    if (not isinstance(data, dict) or data.get('schema') != 'hmi_test.inventory.v2'
            or not isinstance(data.get('instance_id'), str) or not data['instance_id']
            or type(data.get('revision')) is not int or data['revision'] < 0
            or not isinstance(data.get('batch_id'), str)
            or type(data.get('can_refill')) is not bool
            or not isinstance(data.get('items'), list) or not data['items']
            or not isinstance(data.get('blocked_materials'), list)
            or type(data.get('height_low_pct')) not in (int, float)
            or not math.isfinite(data['height_low_pct']) or not 0 < data['height_low_pct'] < 100):
        raise ValueError('시험 재고 메시지 형식이 올바르지 않습니다')
    seen = set()
    for item in data['items']:
        if not isinstance(item, dict):
            raise ValueError('시험 원료 형식 오류')
        mid = item.get('material_id')
        if not isinstance(mid, str) or not mid or mid in seen:
            raise ValueError('시험 원료 ID 누락 또는 중복')
        seen.add(mid)
        height = item.get('height_pct')
        if (type(item.get('height_low_latched')) is not bool
                or (height is not None and (type(height) not in (int, float)
                    or not math.isfinite(height) or not 0 <= height <= 100))
                or (height is not None and height < data['height_low_pct'] and not item['height_low_latched'])):
            raise ValueError('시험 높이 값 또는 부족 잠금이 올바르지 않습니다')
        values = [item.get(k) for k in ('capacity_g', 'remaining_g', 'reserved_g', 'available_g')]
        if not all(type(v) in (int, float) and math.isfinite(v) for v in values):
            raise ValueError('시험 재고 값은 유한한 숫자여야 합니다')
        capacity, remaining, reserved, available = values
        if not (capacity > 0 and 0 <= reserved <= remaining <= capacity and available >= 0
                and abs(available - (remaining - reserved)) < 1e-6):
            raise ValueError('시험 재고 수량 관계가 올바르지 않습니다')
    expected = [item['material_id'] for item in data['items'] if item['height_low_latched']]
    if data['blocked_materials'] != expected:
        raise ValueError('높이 부족 원료 목록이 일치하지 않습니다')
    return data
