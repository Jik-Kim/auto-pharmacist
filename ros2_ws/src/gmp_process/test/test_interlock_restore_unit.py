"""EXIT 결과는 파지 복구 완료 뒤에만 재개 허가를 뜻한다."""
from types import SimpleNamespace as NS

import pytest
from test_run_batch_unit import module, node, Message


def enter(node):
    return node._srv_interlock(Message(request=0, reason='REFILL'), Message())


def leave(node):
    return node._srv_interlock(Message(request=1, reason='REFILL'), Message())


def test_idle_exit_restores_before_unpausing_and_clears_signal(node):
    assert enter(node).granted
    def restore(key, request):
        assert key == 'restore_grip' and node._pause
        assert not node._interlock_exit.is_set()
        return Message(success=True, payload='empty')
    node._call_srv = restore
    reply = leave(node)
    assert reply.granted and '복구 완료' in reply.message
    assert not node._pause and not node._interlock_exit.is_set()


@pytest.mark.parametrize('fault', ['failed', 'timeout', 'new_alarm', 'cancel'])
def test_failed_restore_keeps_pause_and_never_signals_resume(node, module, fault):
    assert enter(node).granted
    def restore(*_):
        if fault == 'timeout': raise module.SkillError('timeout')
        if fault == 'new_alarm':
            # 복구 이벤트가 뒤따라 latch가 풀려도 세대가 바뀌면 거부한다.
            node._interlock_revision += 1
        if fault == 'cancel': node._batch_cancel.set()
        return Message(success=fault != 'failed', payload='empty', message='센서 불일치')
    node._call_srv = restore
    assert not leave(node).granted
    assert node._pause and not node._interlock_exit.is_set()


def test_exit_during_enter_is_rejected(node):
    replies = []
    def safe(*_):
        replies.append(leave(node))
        return Message(success=True)
    node._call_srv = safe
    assert enter(node).granted
    assert not replies[0].granted
    assert node._pause and not node._interlock_exit.is_set()


def test_batch_exit_signals_after_restore_and_preserves_pause_for_loop(node):
    node.fsm = NS(mode='RUNNING', state='PICK_CONTAINER', idx=0)
    assert enter(node).granted
    assert leave(node).granted
    assert node._pause and node._interlock_exit.is_set()
    assert not leave(node).granted  # 루프가 신호를 소비하기 전 중복 요청은 재실행하지 않는다.


def test_enter_failure_cannot_be_bypassed_with_exit(node):
    node._call_srv = lambda *_: Message(success=False, message='안전 자세 실패')
    assert not enter(node).granted
    assert not leave(node).granted
    assert node._pause and not node._interlock_exit.is_set()


def test_compound_carry_cannot_restart_from_source(node, module):
    node._pause = node._interlock_ready = True
    node._active_request_kind = "carry"
    result = node._srv_interlock(Message(request=module.InterlockRequest.Request.EXIT, reason=""), Message())
    assert not result.granted
    assert node._pause and not node._interlock_exit.is_set()


def test_order_start_checks_safe_then_empty_gripper(node):
    calls = []
    def call(key, request):
        calls.append((key, request))
        return Message(success=True, message='')
    node._call_srv = call
    assert node._dispatch({'kind': 'safe', 'reason': 'BATCH_START'})['success']
    assert [key for key, _ in calls] == ['safe', 'restore_grip']
    assert calls[1][1].expected_payload == 'empty'
    assert node.station == 'safe'


@pytest.mark.parametrize('failed', ['safe', 'restore_grip'])
def test_order_start_failure_prevents_next_stage(node, module, failed):
    calls = []
    def call(key, request):
        calls.append(key)
        return Message(success=key != failed, message='거부')
    node._call_srv = call
    with pytest.raises(module.SkillError):
        node._dispatch({'kind': 'safe', 'reason': 'BATCH_START'})
    assert calls == (['safe'] if failed == 'safe' else ['safe', 'restore_grip'])
