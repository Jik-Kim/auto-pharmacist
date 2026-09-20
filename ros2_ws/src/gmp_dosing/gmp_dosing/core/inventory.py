"""원료 잔량 추정 — 순수 함수. SOT D-20 [추가 5] (누적 투입량 + 접촉 높이, 예방 보충 권고).

두 신호를 함께 본다.
  장부(누적 투입량)  초기량에서 배출량을 빼는 값 — 항상 있지만 반환·계량 오차가 쌓이면 실제와 벌어진다.
  접촉 높이(z)      measure_posx 에서 스쿱이 원료 표면에 닿는 z. 원료가 줄수록 표면이 낮아져 z 가 작아진다.
                    장부 오차와 무관한 실측이라, 있으면 장부보다 우선한다.
원료통 단면적·밀도 실측이 아직 없어 높이→그램 변환은 G1 의 gain/offset 두 점 직선과 같은 방식으로,
원료통이 가득 찼을 때·비었을 때의 접촉 z 두 점을 선형보간한다. full_contact_z_mm 이 아직 없으면(실측 전)
장부만 쓴다 — empty_contact_z_mm 은 stations.yaml 의 measure_posx.z(스쿱이 바닥에 닿는 자세)를 그대로 쓸 수 있다.
"""
from dataclasses import dataclass


@dataclass
class MaterialInventoryConfig:
    material_id: str
    initial_g: float                        # 원료통 초기 충전량
    refill_threshold_g: float               # 이 값 이하로 추정되면 예방 보충 권고
    empty_contact_z_mm: float               # 원료 없을 때(바닥) 접촉 z — stations.yaml measure_posx.z
    full_contact_z_mm: float | None = None  # 충전 직후 접촉 z — 실측 전엔 None (높이 신호 미사용, 장부만)


@dataclass
class InventoryState:
    delivered_g: float = 0.0   # 누적 투입량(장부). 반환은 포함하지 않는다 (D-22: 반환은 투입량에 합산 안 함)


@dataclass
class InventoryEstimate:
    remaining_g: float
    source: str                     # LEDGER | HEIGHT
    needs_refill: bool
    drift_g: float | None = None    # 높이 신호가 있을 때 (장부 − 높이) — 크면 장부 재동기화가 필요하다는 뜻


def remaining_by_ledger(cfg: MaterialInventoryConfig, state: InventoryState) -> float:
    return max(0.0, cfg.initial_g - state.delivered_g)


def remaining_by_height(cfg: MaterialInventoryConfig, contact_z_mm: float) -> float | None:
    """full_contact_z_mm 이 없으면(단면적 실측 전) 판단 불가 → None.

    contact_z_mm 이 두 기준점 밖이면(측정 잡음 등) 0~initial_g 로 자른다.
    """
    if cfg.full_contact_z_mm is None:
        return None
    span = cfg.full_contact_z_mm - cfg.empty_contact_z_mm
    if span <= 0.0:
        raise ValueError('full_contact_z_mm 은 empty_contact_z_mm 보다 커야 한다 (원료가 있으면 표면이 더 높다)')
    fraction = (contact_z_mm - cfg.empty_contact_z_mm) / span
    fraction = max(0.0, min(1.0, fraction))
    return fraction * cfg.initial_g


def estimate(cfg: MaterialInventoryConfig, state: InventoryState,
             contact_z_mm: float | None = None) -> InventoryEstimate:
    """접촉 높이 실측이 있으면 그 값을 우선하고, 장부는 drift 확인용으로 남긴다."""
    ledger_g = remaining_by_ledger(cfg, state)
    height_g = remaining_by_height(cfg, contact_z_mm) if contact_z_mm is not None else None
    if height_g is not None:
        remaining_g, source, drift_g = height_g, 'HEIGHT', ledger_g - height_g
    else:
        remaining_g, source, drift_g = ledger_g, 'LEDGER', None
    return InventoryEstimate(remaining_g, source, remaining_g <= cfg.refill_threshold_g, drift_g)
