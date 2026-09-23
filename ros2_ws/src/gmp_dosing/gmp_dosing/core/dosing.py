"""이중 폐루프 도징 정책 — 순수 함수. 계약 2절 판정 규칙.

붓기는 언제나 전량이다 — 계약 v1.3(9/20 팀 승인)이 `Pour.fraction=1.0` 만 허용하고 그 외는 이동 전에
거부한다. 퍼낸 양이 많으면 부분 투입이 아니라 원료통에 되돌린 뒤 다시 푼다(`ReturnMaterial`).
한 스쿱보다 작은 양은 **퍼올릴 때** 깊이로 조절한다 — v1.5 가 `Pour.fraction` 대신 `Scoop.depth_fraction`
으로 제어점을 옮겼다. (부분 붓기용 pour_fraction() 은 2026-09-21 삭제 — v1.3 으로 폐기된 설계였다.)

decide() 는 상태를 갖지 않는다. 이력은 호출자(process_fsm)가 넘긴다.
행동:
  DONE      허용 오차 안
  SCOOP     부족 — 다시 퍼서 붓는다. fraction 은 **다음 Scoop 의 담그기 깊이 비율**
            (계약 v1.5 `Scoop.depth_fraction`). process_fsm 이 `_scoop(d.fraction)` 으로 넘긴다.
  DEVIATION 초과(OVERFILL) 또는 시도 상한(TIMEOUT) 또는 계량 무효 반복(WEIGH_INVALID)
"""
from dataclasses import dataclass


@dataclass
class DosingConfig:
    max_attempts: int = 3
    scoop_nominal_g: float = 85.0     # 스쿱 1회 퍼올림 평균 (9/23 조장 결정, 종전 9/18 실측 40.0)
    min_fraction: float = 0.10        # 담그기 깊이 비율의 하한 (계약 v1.5). 이보다 얕게는 제어가 안 된다
                                      # ⚠️ 교착 조건 min_fraction × scoop_nominal_g ≤ 2 × target × tol 을
                                      #    지켜야 한다. common.yaml 주석과 test_dosing 의 단언 참조
    max_invalid_retries: int = 2      # 계량 무효 시 **다시 재는** 횟수. 최초 측정은 여기 안 든다 —
                                      # 총 측정은 이 값 + 1 이다 (#213 결정 1). 종전 이름 max_invalid 는
                                      # 「무효 결과 총 횟수」였는데 읽는 사람마다 다르게 세었다.


@dataclass
class Decision:
    action: str            # DONE | SCOOP | DEVIATION
    verdict: str           # OK | UNDER | OVER | INVALID
    kind: str = ''         # DEVIATION 일 때 OVERFILL | TIMEOUT | WEIGH_INVALID
    fraction: float = 1.0  # SCOOP 일 때 다음 Scoop 의 담그기 깊이 비율 (v1.5 depth_fraction)
    error_pct: float = 0.0


def verdict_of(target_g: float, actual_g: float, tol_pct: float) -> tuple[str, float]:
    err = (actual_g - target_g) / target_g * 100.0
    if abs(err) <= tol_pct:
        return 'OK', err
    return ('UNDER', err) if actual_g < target_g else ('OVER', err)


def decide(target_g: float, actual_g: float, tol_pct: float, attempts: int, valid: bool,
           invalid_count: int, cfg: DosingConfig) -> Decision:
    """다음 행동을 고른다. 상태를 갖지 않으며 이력은 호출자가 넘긴다.

    ⚠️ `valid`·`invalid_count` 는 **운영에서 쓰이지 않는다** (2026-09-21 확인, C 교차검증).
    유일한 운영 호출처인 `process_fsm:258` 이 `valid` 를 리터럴 `True` 로 넘기기 때문이다 —
    무효 계량은 그 앞의 `_invalid_or()` 가 재계량시키거나 `WEIGH_INVALID` 로 일탈시켜
    여기까지 내려오지 않는다. `invalid_count` 도 `if not valid:` 안에서만 읽히므로 같이 죽어 있다.
    아래 무효 분기는 계약(상태 없음·이력은 호출자)을 지키려고 방어적으로 남겨둔 것이고,
    지금 도달하는 곳은 test_dosing.py 뿐이다.
    """
    if not valid:
        if invalid_count > cfg.max_invalid_retries:
            return Decision('DEVIATION', 'INVALID', 'WEIGH_INVALID')
        # ⚠️ fraction 은 **담그기 깊이**다 (v1.5). 옛 설계에서는 붓기 비율이라 0 이 "붓지 말고 다시 재라"
        #    였지만, 지금 이 값이 실제로 쓰이면 `_scoop(0.0)` → 깊이 0 이 되어 계약 v1.5 의
        #    min_fraction 하한(미만이면 이동 전 거부)을 위반한다. 이 분기를 살려 쓰려면 먼저 고칠 것.
        return Decision('SCOOP', 'INVALID', fraction=0.0)
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
