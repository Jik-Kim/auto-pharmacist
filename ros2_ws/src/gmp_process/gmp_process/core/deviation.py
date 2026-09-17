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
    'VERIFY_MISMATCH': (0, 'QA', 'QA'),   # 스쿱 누적 투입량 vs 용기 계량 불일치 (D-22 2차 검증) — 계약 v1.2 kind
    'SAFETY_SWITCH':   (1, 'RETRY', 'FORCED'),
    'FORCE_LIMIT':     (1, 'RETRY', 'FORCED'),
}


def policy(kind: str, count: int) -> tuple[str, bool]:
    """count 는 같은 배치·같은 스텝에서 이 kind 가 몇 번째인가 (1부터)."""
    limit, first, after = RULES[kind]
    action = first if count <= limit else after
    return action, action == 'QA'
