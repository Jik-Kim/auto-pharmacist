"""일탈 kind 별 처리 규칙표. ROS 비의존.

policy(kind, count) → (action, requires_decision)
  RETRY      같은 스텝 재시도
  REFILL     인터락으로 보충 요청 (개입 아님 — 계약 6절)
  QA         QA 판정 대기 (개입 아님)
  FORCED     강제 개입 — MTBI 분모에 든다
"""
RULES = {
    'GRIP_FAIL':       (3, 'RETRY', 'FORCED'),
    'SLIP':            (2, 'RETRY', 'FORCED'),
    'SCOOP_EMPTY':     (3, 'RETRY', 'REFILL'),
    'MATERIAL_EMPTY':  (0, 'REFILL', 'REFILL'),
    'OVERFILL':        (0, 'QA', 'QA'),
    'TIMEOUT':         (0, 'QA', 'QA'),
    'WEIGH_INVALID':   (2, 'RETRY', 'QA'),
    # ⚠️ VERIFY_MISMATCH 는 9/22 폐지(사용자·조장 확정) — process_fsm 이 더는 내지 않는다.
    #    정책 항목은 남긴다: 계약 열거값이 살아 있고, 과거 배치 기록에 이 kind 가 들어 있어
    #    HMI·DB 가 조회할 때 정책표를 찾는다. 새로 발생하지는 않는다.
    'VERIFY_MISMATCH': (0, 'QA', 'QA'),   # [폐지] 용기 계량 vs 스쿱 누적 투입량 불일치 (D-22 ②)
    'BATCH_OUT_OF_SPEC': (0, 'QA', 'QA'), # 용기 순량 vs 레시피 총 목표량 불일치 — 제품 규격 판정 (D-22 ①). QA 가 폐기 판단
    'SAFETY_SWITCH':   (1, 'RETRY', 'FORCED'),
    'FORCE_LIMIT':     (1, 'RETRY', 'FORCED'),
    'WRONG_TOOL':      (0, 'QA', 'QA'),   # 스쿱·약통 폭 지문 불일치 — 교차오염 의심 (D-20 추가 1, v1.2)
}

# gmp_interfaces/msg/Deviation.msg 의 kind 상수와 1:1 대응해야 한다 — 여기 없는 kind 로 policy() 를 부르면 KeyError.
assert set(RULES) == {
    'OVERFILL', 'GRIP_FAIL', 'SLIP', 'SAFETY_SWITCH', 'SCOOP_EMPTY', 'MATERIAL_EMPTY', 'FORCE_LIMIT',
    'TIMEOUT', 'WEIGH_INVALID', 'VERIFY_MISMATCH', 'BATCH_OUT_OF_SPEC', 'WRONG_TOOL',
}


def policy(kind: str, count: int) -> tuple[str, bool]:
    """count 는 같은 배치·같은 스텝에서 이 kind 가 몇 번째인가 (1부터)."""
    limit, first, after = RULES[kind]
    action = first if count <= limit else after
    return action, action == 'QA'
