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
from dataclasses import dataclass, field


@dataclass
class DosingConfig:
    max_attempts: int = 3
    scoop_nominal_g: float = 85.0     # 스쿱 1회 퍼올림 평균 (9/23 조장 결정, 종전 9/18 실측 40.0)
    min_fraction: float = 0.10        # 담그기 깊이 비율의 하한 (계약 v1.5). 이보다 얕게는 제어가 안 된다
                                      # ⚠️ 교착 조건 min_fraction × scoop_nominal_g ≤ 2 × target × tol 을
                                      #    지켜야 한다. common.yaml 주석과 test_dosing 의 단언 참조
    fixed_scoop: bool = field(default=False, kw_only=True)
    """깊이 제어가 없어 **스쿱이 언제나 가득 퍼진다**면 True (9/23 시연 설정).

    이때 보충 1회의 최소량이 min_fraction x nominal 이 아니라 **nominal 전체**가 되므로,
    미달을 보충하려 해도 대부분 허용 상한을 넘긴다 — decide() 가 그것을 보고 바로 일탈시킨다.
    위치 인자로 잘못 넘기는 것을 막으려고 키워드 전용이다.
    """
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
    detail: str = ''       # 같은 kind 가 여러 사실을 덮을 때 **무엇이 일어났는지**를 사람 말로 싣는다.
                           # 내부 dataclass 라 계약(Deviation.kind 열거)은 안 바뀐다 — 호출자는
                           # `_deviate(d.kind, step, detail=d.detail)` 한 줄로 받는다.
                           # 이게 없으면 TIMEOUT 하나가 「시도 소진」과 「보충 불가」 두 경우를 덮어,
                           # 구분하려면 호출자가 술어를 다시 계산해야 한다 — 조건이 두 곳이 된다.


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
    # ── 보충해도 못 맞추는가 — **시도 소진보다 먼저 본다** ──────────────
    # 시도가 남아 있어도 한 번 더 퍼서 허용 상한을 넘긴다면 더 돌릴 이유가 없다.
    # min_add 는 **이 설정에서 가능한 가장 작은 보충량**이다:
    #     고정 스쿱  → nominal 전체 (깊이를 못 줄인다)
    #     깊이 제어  → min_fraction x nominal
    # ⚠️ 깊이 제어 모드에서 이 분기는 **교착 조건과 정확히 동치**다 —
    #    최악의 actual(허용 하한 직전)에서 발동 조건이 min_add > 2 x target x tol 로 떨어진다.
    #    즉 common.yaml 의 교착 조건이 지켜지는 한 **절대 발동하지 않고**, 누가 그 조건을 깨면
    #    무한 스쿱↔반환 대신 QA 로 보낸다. 안전망이지 평상시 경로가 아니다.
    min_add = cfg.scoop_nominal_g if cfg.fixed_scoop else cfg.min_fraction * cfg.scoop_nominal_g
    upper = target_g * (1.0 + tol_pct / 100.0)
    if actual_g + min_add > upper:
        return Decision('DEVIATION', v, 'TIMEOUT', error_pct=err,
                        detail=f'보충 불가 — 최소 채취 {min_add:.1f} g 을 더하면 '
                               f'허용 상한 {upper:.1f} g 초과')
    if attempts >= cfg.max_attempts:
        return Decision('DEVIATION', v, 'TIMEOUT', error_pct=err,
                        detail=f'보정 {attempts}회 후에도 미달')
    need = target_g - actual_g
    # 고정 스쿱에서는 깊이를 못 고른다 — 언제나 한 스쿱 전량이다. 여기서 need/nominal 로
    # 부분 깊이를 내면 A 의 고정 경로(depth_fraction != 1.0 거부)가 보충 스쿱을 튕겨
    # 배치가 ERROR 로 죽는다. 넘겨도 되는지는 위 min_add 분기가 이미 판정했다.
    frac = 1.0 if cfg.fixed_scoop else max(cfg.min_fraction, min(1.0, need / cfg.scoop_nominal_g))
    return Decision('SCOOP', v, fraction=frac, error_pct=err)
