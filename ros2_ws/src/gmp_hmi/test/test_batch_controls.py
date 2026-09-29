"""ROS 스텁 기반 취소 경합·인증·재접속 검증. 실제 C 액션 서버 검증은 별도."""
from concurrent.futures import Future
from types import SimpleNamespace as NS
import pytest
from test_v3_backend import backend, node, app_db, Message, state, login, post, PASSWORD
from gmp_hmi.core.measurement_context import target_band

RECIPE = dict(name='recipe-01', product='레시피 1', items=[
    dict(material_id='A', target_g=80, tol_pct=5), dict(material_id='B', target_g=40, tol_pct=10)])

class PendingGoal:
    accepted = True
    def __init__(self):
        self.result = Future()
        self.cancel = Future()
        self.goal_id = NS(uuid=list(range(16)))
        self.cancel_count = 0
    def get_result_async(self):
        return self.result
    def cancel_goal_async(self):
        self.cancel_count += 1
        return self.cancel


def own_goal(node, batch='B1'):
    goal = PendingGoal()
    accepted = Future(); accepted.set_result(goal)
    node.act_batch.send_goal_async = lambda *a, **k: accepted
    assert node._send_batch_goal(Message(), batch, detail=RECIPE, actor='admin').accepted
    node._on_state(state(batch))
    return goal


def test_late_acceptance_kept_and_double_order_blocked(node):
    future = Future()
    node._on_state(state('', 0))
    node.act_batch.send_goal_async = lambda *a, **k: future
    assert node._send_batch_goal(Message(), 'B1', timeout_s=0, detail=RECIPE, actor='admin') is None
    with pytest.raises(RuntimeError):
        node.submit('demo_batch', 'admin')
    goal = PendingGoal(); future.set_result(goal)
    node._on_state(state())
    assert node.snapshot()['batch_control']['can_cancel']
    assert node.snapshot()['active_recipe']['product'] == '레시피 1'
    assert any(e.code == 'HMI_ORDER_CONTEXT' for e in node.published)


def test_cancel_ack_is_not_completion_and_duplicates_blocked(node):
    goal = own_goal(node)
    with pytest.raises(ValueError):
        node.cancel_batch('B1', False, 'admin')
    with pytest.raises(RuntimeError):
        node.cancel_batch('OTHER', True, 'admin')
    assert node.cancel_batch('B1', True, 'admin', timeout_s=0) is None
    with pytest.raises(RuntimeError):
        node.cancel_batch('B1', True, 'admin')
    goal.cancel.set_result(NS(return_code=0, goals_canceling=[NS(goal_id=goal.goal_id)]))
    assert node.snapshot()['batch_control']['cancel_state'] == 'accepted'
    assert node.snapshot()['state']['mode'] == 'RUNNING'
    assert node._batch_handle is goal and goal.cancel_count == 1
    goal.result.set_result(NS(result=NS(success=False, items_done=0, deviations=0, result='ABORTED', message='cancelled')))
    assert not node.snapshot()['batch_control']['can_cancel']
    assert node.snapshot()['run_batch']['result'] == 'ABORTED'


def test_late_cancel_ack_does_not_reopen_completed_goal(node):
    goal = own_goal(node)
    node.cancel_batch('B1', True, 'admin', timeout_s=0)
    goal.result.set_result(NS(result=NS(success=True, items_done=2, deviations=0, result='DONE', message='done')))
    goal.cancel.set_result(NS(return_code=0, goals_canceling=[NS(goal_id=goal.goal_id)]))
    assert node._batch_cancel_state == '' and node._batch_handle is None


def test_wrong_goal_ack_rejected(node):
    goal = own_goal(node)
    goal.cancel.set_result(NS(return_code=0, goals_canceling=[NS(goal_id=NS(uuid=[99]*16))]))
    assert not node.cancel_batch('B1', True, 'admin').accepted
    assert node._batch_cancel_state == ''


def test_cancel_http_auth_csrf_and_actor(app_db, node):
    app, db = app_db
    guest = app.test_client()
    assert guest.get('/restart-state').status_code == 401
    node.admin_store.create_user('viewer', PASSWORD, 'viewer')
    view = app.test_client(); vt = login(view, 'viewer')
    assert post(view, vt, '/batch/cancel', dict(batch_id='B1', confirmed=True)).status_code == 403
    client = app.test_client(); token = login(client)
    goal = own_goal(node)
    goal.cancel.set_result(NS(return_code=0, goals_canceling=[NS(goal_id=goal.goal_id)]))
    assert client.post('/batch/cancel', json=dict(batch_id='B1', confirmed=True)).status_code == 403
    response = post(client, token, '/batch/cancel', dict(batch_id='B1', confirmed=True, actor='spoof'))
    assert response.json['ok']
    assert all(e.text.startswith('admin ') for e in node.published if e.code.startswith('HMI_BATCH_CANCEL'))
    assert client.get('/restart-state').json['resume_supported'] is False


def test_goal_lost_on_restart_cannot_cancel_arbitrary_batch(node):
    node._on_state(state())
    assert not node.snapshot()['batch_control']['can_cancel']
    with pytest.raises(RuntimeError):
        node.cancel_batch('B1', True, 'admin')


def test_target_band_uses_total_weighted_tolerance():
    band = target_band(RECIPE, 'B1')
    assert (band['lower_g'], band['target_g'], band['upper_g']) == (112, 120, 128)
    assert target_band(None, 'B1') is None
    assert target_band(RECIPE, '') is None
    assert target_band(dict(items=[dict(material_id='A', target_g=float('nan'), tol_pct=5)]), 'B1') is None


def test_weight_context_clears_on_batch_change_and_rejects_late_sample(node):
    node._on_state(state('B1'))
    def weight(t):
        w = Message(net_g=120, gross_g=140, tare_g=20, std_g=1, valid=True, station='workbench', subject='container', samples=20)
        w.header.stamp.sec=t
        return w
    node._on_weight(weight(100))
    assert node.snapshot()['weights'][0]['observed_batch_id'] == 'B1'
    st=state('B2'); st.header.stamp.sec=110; node._on_state(st)
    assert not node.snapshot()['weights']
    node._on_weight(weight(100))
    assert node.snapshot()['weights'][0]['observed_batch_id'] is None


def test_restart_persists_readonly_and_never_resumes(app_db, node, backend):
    app, db = app_db
    db.start_batch('B1', 1, '레시피 1')
    db.save_recipe_context('B1', 2, RECIPE)
    db.checkpoint('B1', 3, '2', 'SCOOP', 1, 'material_2', 'pause')
    db.checkpoint('B1', 2, '1', 'OLD', 0, 'material_1', '')
    db.checkpoint('B1', 4, '2', 'SCOOP', 1, 'material_2', 'pause')
    db.weight('B1', 3, 'material_2', 30, 10, 20, 1, True, subject='scoop')
    reader = backend.ReadOnlyCellDB(db.path)
    assert len(reader._rows('SELECT * FROM state_checkpoints')) == 1
    r = reader.restart_records()[0]
    assert r['checkpoint']['step']=='SCOOP' and r['recipe']['total_g']==120
    assert r['last_measurements'][0]['tare_g']==10 and r['resume_supported'] is False
    node._on_state(state())
    client=app.test_client(); login(client)
    assert client.get('/status').json['target_band']['target_g']==120
    assert not node.act_batch.calls
    db.finish_batch('B1', 5, 'ERROR')
    assert reader.restart_records()==[]


def set_end(batch='B1', step='NUDGE_WAIT', mode=2):
    s = state(batch, mode)
    s.step = step
    s.note = 'NUDGE_WAIT — 세트 완료, 건드리면 다음 세트' if step == 'NUDGE_WAIT' else ''
    return s


def queue_goal(node):
    goal = PendingGoal()
    accepted = Future(); accepted.set_result(goal)
    node.act_batch.send_goal_async = lambda *a, **k: accepted
    assert node.submit('demo_batch', 'admin').accepted
    return goal, node.snapshot()['batch_control']['queued']['batch_id']


def test_set_end_order_is_queued_apart_from_running_goal_then_promoted(node):
    """계약 v1.9 — 세트 끝 주문은 진행 배치 handle·목표선을 건드리지 않고 예약 칸에 둔다."""
    from gmp_hmi.nodes.hmi_web_node import order_queueable
    first = own_goal(node)
    node._on_state(set_end())
    second, queued_id = queue_goal(node)
    control = node.snapshot()['batch_control']
    assert control['queued'] == dict(batch_id=queued_id, accepted=True, cancel_state='', can_cancel=True)
    assert node._batch_handle is first and node._batch_id == 'B1' and control['can_cancel']
    assert node.snapshot()['active_recipe']['product'] == '레시피 1'   # 예약 주문이 목표선을 가로채지 않는다
    with pytest.raises(RuntimeError, match='이미 예약'):
        node.submit('demo_batch', 'admin')
    first.result.set_result(NS(result=NS(success=True, items_done=2, deviations=0, result='DONE', message='done')))
    assert node._batch_handle is None and node.snapshot()['batch_control']['queued']['batch_id'] == queued_id
    node._on_state(state(queued_id))            # 넛지 뒤 공정이 예약 주문을 시작했다
    control = node.snapshot()['batch_control']
    assert control['queued'] is None and node._batch_handle is second and control['can_cancel']
    assert node.snapshot()['active_recipe']['product'] == 'DEMO-01'
    # 끝난 배치의 DONE/DISCARDED 는 예약이 아니라 보통 주문이다.
    assert not order_queueable(dict(step='DISCARDED', mode='DONE'))
    assert order_queueable(dict(step='DISCARDED', mode='RUNNING')) and order_queueable(dict(step='FINISH', mode='RUNNING'))
    # 세트 끝이어도 인터락 ENTER·접촉 정지 중이면 C 가 거부한다 — NUDGE_WAIT 자체의 정지만 허용.
    assert order_queueable(dict(step='NUDGE_WAIT', mode='PAUSED', pause_reason='SET_COMPLETE'))
    assert not order_queueable(dict(step='NUDGE_WAIT', mode='PAUSED', pause_reason='INTERLOCK'))
    assert not order_queueable(dict(step='FINISH', mode='PAUSED', pause_reason='NUDGE'))


def test_queued_cancel_and_drop_leave_running_goal_alone(node):
    first = own_goal(node)
    node._on_state(set_end('B1', 'FINISH', 1))
    second, queued_id = queue_goal(node)
    assert node.cancel_batch(queued_id, True, 'admin', timeout_s=0) is None
    assert node.snapshot()['batch_control']['queued']['cancel_state'] == 'pending'
    assert node._batch_cancel_state == '' and second.cancel_count == 1 and first.cancel_count == 0
    second.cancel.set_result(NS(return_code=0, goals_canceling=[NS(goal_id=second.goal_id)]))
    assert node.snapshot()['batch_control']['queued']['cancel_state'] == 'accepted'
    second.result.set_result(NS(result=NS(success=False, items_done=0, deviations=0, result='ABORTED',
                                          message='예약 주문 취소 요청')))
    control = node.snapshot()['batch_control']
    assert control['queued'] is None and node._batch_handle is first and control['can_cancel']
    assert 'run_batch' not in node.snapshot() or node.snapshot()['run_batch'].get('result') != 'ABORTED'
    # 시작 못 한 예약의 사유를 잠깐 보여 준다 — 운영자가 왜 시작 안 됐는지 바로 안다.
    assert control['queued_dropped'] == dict(batch_id=queued_id, result='ABORTED', message='예약 주문 취소 요청')
