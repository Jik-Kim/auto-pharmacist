"""이중 폐루프 도징 정책 — 순수 함수. 계약 2절 판정 규칙.

decide() 는 상태를 갖지 않는다. 이력은 호출자(process_fsm)가 넘긴다.
행동:
  DONE      허용 오차 안
  SCOOP     부족 — 다시 퍼서 붓는다. fraction 은 부족량/스쿱 1회량 (털어내기 비율)
  DEVIATION 초과(OVERFILL) 또는 시도 상한(TIMEOUT) 또는 계량 무효 반복(WEIGH_INVALID)
"""
from dataclasses import dataclass


@dataclass
class DosingConfig:
    max_attempts: int = 3
    scoop_nominal_g: float = 40.0     # 스쿱 1회 퍼올림 평균 (9/18 실측)
    min_fraction: float = 0.15        # 이보다 작은 fraction 은 털어내기로 못 맞춘다 → 그냥 1회 붓고 재판정
    max_invalid: int = 2


@dataclass
class Decision:
    action: str            # DONE | SCOOP | DEVIATION
    verdict: str           # OK | UNDER | OVER | INVALID
    kind: str = ''         # DEVIATION 일 때 OVERFILL | TIMEOUT | WEIGH_INVALID
    fraction: float = 1.0  # SCOOP 일 때 붓기 비율
    error_pct: float = 0.0


def verdict_of(target_g: float, actual_g: float, tol_pct: float) -> tuple[str, float]:
    err = (actual_g - target_g) / target_g * 100.0
    if abs(err) <= tol_pct:
        return 'OK', err
    return ('UNDER', err) if actual_g < target_g else ('OVER', err)


def pour_fraction(need_g: float, scooped_g: float, cfg: DosingConfig) -> float:
    """1차 폐루프 — 퍼낸 양이 부족량보다 많으면 부족량만큼만 붓는다 (D-22 WEIGH_SCOOP).

    초과는 되돌릴 수 없으므로 붓기 전에 막는 것이 유일한 수단이다.
    min_fraction 아래로는 털어내기로 못 맞추니 그 값에서 자른다.

    ⚠️ **운영에서 호출되지 않는다** (2026-09-21 전수 확인, 호출처는 test_dosing.py 뿐).
    설계가 바뀌었다 — process_fsm 의 WEIGH_SCOOP 은 부분 투입을 하지 않고, 퍼낸 양이
    `남은 목표량 + _scoop_allowance_g()` 를 넘으면 **전량을 원료통에 되돌린 뒤 다시 푼다**
    (`RETURN_MATERIAL` → `_rescoop_fraction()`). 붓기는 언제나 fraction 1.0 이다.
    "실제 투입량과 반환량이 섞이지 않게" 하려는 의도이고 FSM 주석에 그렇게 적혀 있다.

    resolvable() 과는 성격이 다르다 — 그쪽은 아직 안 붙인 **미구현 요구사항**(BRD 3.1.3)이고,
    이쪽은 **폐기된 설계의 잔재**다. 지울지 부분 투입을 되살릴지는 조장·C 판단이 필요해 남겨 둔다.
    문서에는 아직 1차 폐루프로 기술돼 있다 — docs/interfaces.md · docs/process_flow.md (조장 소관).
    """
    if scooped_g <= 0.0 or scooped_g <= need_g:
        return 1.0
    return max(cfg.min_fraction, min(1.0, need_g / scooped_g))


def decide(target_g: float, actual_g: float, tol_pct: float, attempts: int, valid: bool,
           invalid_count: int, cfg: DosingConfig) -> Decision:
    if not valid:
        if invalid_count + 1 >= cfg.max_invalid:
            return Decision('DEVIATION', 'INVALID', 'WEIGH_INVALID')
        return Decision('SCOOP', 'INVALID', fraction=0.0)   # fraction 0 = 붓지 말고 다시 재라
    v, err = verdict_of(target_g, actual_g, tol_pct)
    if v == 'OK':
        return Decision('DONE', v, error_pct=err)
    if v == 'OVER':
        return Decision('DEVIATION', v, 'OVERFILL', error_pct=err)
    if attempts >= cfg.max_attempts:
        return Decision('DEVIATION', v, 'TIMEOUT', error_pct=err)
    need = target_g - actual_g
    frac = max(cfg.min_fraction, min(1.0, need / cfg.scoop_nominal_g))
    return Decision('SCOOP', v, fraction=frac, error_pct=err)
