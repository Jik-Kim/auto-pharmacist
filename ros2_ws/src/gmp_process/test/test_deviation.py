"""deviation.policy() 테스트 — kind 별 재시도 상한·전환 규칙 (docs/process_flow.md 「일탈 정책」 표와 1:1).

ROS 비의존 순수 함수라 gmp_interfaces 없이 돈다. MSG_KINDS 는 Deviation.msg 의 kind 상수를
그대로 옮긴 것 — 둘이 갈리면 실물에서 policy(새 kind) 가 KeyError 로 죽는다.
"""
import pytest

from gmp_process.core.deviation import RULES, policy

MSG_KINDS = {
    'OVERFILL', 'GRIP_FAIL', 'SLIP', 'SAFETY_SWITCH', 'SCOOP_EMPTY', 'MATERIAL_EMPTY', 'FORCE_LIMIT',
    'TIMEOUT', 'WEIGH_INVALID', 'VERIFY_MISMATCH', 'BATCH_OUT_OF_SPEC', 'WRONG_TOOL',
}


def test_catalog_covers_every_deviation_msg_kind():
    assert set(RULES) == MSG_KINDS


def test_unknown_kind_raises():
    with pytest.raises(KeyError):
        policy('NOT_A_KIND', 1)


@pytest.mark.parametrize('kind', ['OVERFILL', 'TIMEOUT', 'VERIFY_MISMATCH', 'BATCH_OUT_OF_SPEC', 'WRONG_TOOL'])
def test_zero_limit_kinds_always_go_to_qa(kind):
    for count in (1, 2, 5):
        action, needs_qa = policy(kind, count)
        assert action == 'QA'
        assert needs_qa is True


def test_material_empty_always_refills_without_qa():
    for count in (1, 3):
        action, needs_qa = policy('MATERIAL_EMPTY', count)
        assert action == 'REFILL'
        assert needs_qa is False


def test_grip_fail_retries_then_forces():
    assert policy('GRIP_FAIL', 1) == ('RETRY', False)
    assert policy('GRIP_FAIL', 3) == ('RETRY', False)
    assert policy('GRIP_FAIL', 4) == ('FORCED', False)


def test_scoop_empty_retries_then_refills():
    assert policy('SCOOP_EMPTY', 3) == ('RETRY', False)
    assert policy('SCOOP_EMPTY', 4) == ('REFILL', False)


def test_weigh_invalid_retries_then_qa():
    assert policy('WEIGH_INVALID', 2) == ('RETRY', False)
    assert policy('WEIGH_INVALID', 3) == ('QA', True)


@pytest.mark.parametrize('kind', ['SLIP', 'SAFETY_SWITCH', 'FORCE_LIMIT'])
def test_single_retry_kinds_force_on_second(kind):
    limit = RULES[kind][0]
    action, needs_qa = policy(kind, limit + 1)
    assert action == 'FORCED'
    assert needs_qa is False
