"""실제 C ProcessNode + FakeSkillNode DDS 검증. DOMAIN 88 전용, 로봇 호출 없음.

pytest가 각 사례마다 노드를 기동/종료하며 입력 없이 끝까지 실행한다.
"""
import os
import threading
import time
from pathlib import Path

import pytest

rclpy = pytest.importorskip('rclpy', reason='ROS 2가 없어 DDS 시험은 건너뜀')
pytest.importorskip('gmp_interfaces.action', reason='gmp_interfaces 빌드 필요')
from action_msgs.msg import GoalStatus
from rclpy.action import ActionClient
from rclpy.executors import MultiThreadedExecutor
from rclpy.parameter import Parameter
from gmp_interfaces.action import RunBatch
from gmp_interfaces.msg import CellState, Deviation, Recipe, RecipeItem
from gmp_interfaces.srv import QaDecision, SubmitOrder
from gmp_process.nodes.process_node import ProcessNode
from fake_skill_node import NOMINAL_SCOOP_G, FakeSkillNode
from test_process_node import Collector

pytestmark = pytest.mark.skipif(os.environ.get('ROS_DOMAIN_ID') != '88', reason='DOMAIN 88 전용 시험')
STATIONS = Path(__file__).resolve().parents[2]/'gmp_bringup/params/stations.yaml'


def wait_until(predicate, timeout=20):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        value=predicate()
        if value:return value
        time.sleep(.02)
    raise AssertionError('조건 확인 시간 초과')


def result(future, timeout=20):
    wait_until(future.done,timeout)
    return future.result()


@pytest.fixture
def rig():
    # 모든 시험 노드를 고유 네임스페이스로 제한한다. /cell 스킬 서버를 호출하지 않는다.
    namespace=f'/runbatch_test_{os.getpid()}'
    rclpy.init(args=['--ros-args','-r',f'__ns:={namespace}'])
    proc=ProcessNode(parameter_overrides=[
        Parameter('stations_file',value=str(STATIONS)),Parameter('scale.samples',value=4),
        Parameter('scale.settle_s',value=0.0),Parameter('server_wait_s',value=5.0),
        Parameter('skill_timeout_s',value=8.0),
        # 선언 기본값이 운영값(D-35)으로 바뀌어도 가짜 스킬 노드의 공칭 40 g 과 짝을 유지한다 (9/25)
        Parameter('dosing.scoop_nominal_g',value=NOMINAL_SCOOP_G),
        Parameter('dosing.min_fraction',value=0.15),Parameter('scale.zero_drift_limit_n',value=0.5)])
    fake=FakeSkillNode(); probe=Collector()
    executor=MultiThreadedExecutor(num_threads=8)
    for n in (proc,fake,probe):executor.add_node(n)
    thread=threading.Thread(target=executor.spin,daemon=True);thread.start()
    fake.attend(proc)
    client=ActionClient(probe,RunBatch,'run_batch')
    assert client.wait_for_server(timeout_sec=10)
    feedback=[]
    def send(batch='ROS-B1', target=40):
        recipe=Recipe(batch_id=batch,product='ROS 시험',items=[RecipeItem(material_id='A',target_g=float(target),tol_pct=5.0)])
        return result(client.send_goal_async(RunBatch.Goal(recipe=recipe),feedback_callback=lambda m:feedback.append(m.feedback)))
    try:yield proc,fake,probe,send,feedback
    finally:
        fake.stop_attending()
        proc.shutdown(timeout=10)
        executor.shutdown(timeout_sec=10)
        thread.join(timeout=3)
        client.destroy()
        proc.batch_server.destroy()
        for n in (proc,fake,probe):n.destroy_node()
        rclpy.try_shutdown()


def test_normal_feedback_result(rig):
    proc,fake,probe,send,feedback=rig
    goal=send();assert goal.accepted
    reply=result(goal.get_result_async(),60)
    assert reply.status==GoalStatus.STATUS_SUCCEEDED
    assert reply.result.success and reply.result.result=='DONE' and reply.result.items_done==1
    wait_until(lambda: any(f.last_result.material_id=='A' for f in feedback))
    assert all(f.state.batch_id=='ROS-B1' for f in feedback)
    wait_until(lambda: any(e.code=='BATCH_END' for e in probe.events))


def test_bad_recipe_and_busy_service_rejected(rig):
    proc,fake,probe,send,feedback=rig
    assert not send('INVALID',0).accepted
    fake.delay['move']=.6
    goal=send();assert goal.accepted
    assert not send('SECOND').accepted
    client=probe.create_client(SubmitOrder,'submit_order')
    assert client.wait_for_service(timeout_sec=5)
    recipe=Recipe(product='동시 주문',items=[RecipeItem(material_id='A',target_g=40.0,tol_pct=5.0)])
    assert not result(client.call_async(SubmitOrder.Request(recipe=recipe))).accepted
    assert result(goal.cancel_goal_async()).goals_canceling
    assert result(goal.get_result_async()).result.result=='ABORTED'


def test_cancel_waits_active_skill_and_stops_followups(rig):
    proc,fake,probe,send,feedback=rig
    fake.delay['move']=.8  # fake 서버의 기본 cancel 거부도 종료 결과까지 기다린다
    goal=send();assert goal.accepted
    wait_until(lambda: any(c.startswith('move:') for c in fake.calls))
    assert result(goal.cancel_goal_async()).goals_canceling
    reply=result(goal.get_result_async())
    assert reply.status==GoalStatus.STATUS_CANCELED and reply.result.result=='ABORTED'
    assert proc.fsm.mode=='ERROR'
    calls=list(fake.calls);time.sleep(.2)
    assert fake.calls==calls


def test_cancel_set_boundary_nudge_wait(rig):
    proc,fake,probe,send,feedback=rig
    fake.attendant=False
    goal=send();assert goal.accepted
    wait_until(lambda: proc._nudge_waiting,60)
    assert result(goal.cancel_goal_async()).goals_canceling
    reply=result(goal.get_result_async())
    assert reply.result.result=='ABORTED' and not reply.result.success
    wait_until(lambda: any(s.mode==CellState.ERROR for s in probe.states))


def _fresh_cell(fake):
    """가짜 셀을 다음 배치 상태로 — 첫 배치의 용기 내용물이 남으면 계량이 어긋난다 (test_process_node 와 같다)."""
    with fake.lock:
        fake.in_cup, fake.held = 0.0, None
        fake.content.clear()


def test_order_during_nudge_wait_is_queued_and_starts_on_nudge(rig):
    """9/28 A안: 세트 끝 대기 중 주문은 거부하지 않고 예약한다. 넛지 하나로 배치 1 이 끝나고 예약이 시작된다."""
    proc,fake,probe,send,feedback=rig
    fake.attendant=False
    first=send('ROS-Q1');assert first.accepted
    wait_until(lambda: proc._nudge_waiting,60)
    second=send('ROS-Q2');assert second.accepted,'세트 끝 주문은 예약된다'
    wait_until(lambda: any(e.code=='ORDER_QUEUED' for e in probe.events))
    assert [e.batch_id for e in probe.events if e.code=='ORDER_QUEUED']==['ROS-Q2'],'예약 사건은 예약 주문의 기록이다'
    time.sleep(.3)
    assert proc.fsm.state=='NUDGE_WAIT' and proc.batch_id=='ROS-Q1','넛지 전에는 시작하지 않는다'
    assert 'ROS-Q2' in proc.note
    _fresh_cell(fake)
    fake.attendant=True                      # 배치 1 대기를 풀고, 배치 2 의 세트 끝도 건드려 준다
    assert result(first.get_result_async(),60).result.result=='DONE'
    reply=result(second.get_result_async(),60)
    assert reply.status==GoalStatus.STATUS_SUCCEEDED and reply.result.result=='DONE'
    assert any(e.code=='SET_NEXT' and e.batch_id=='ROS-Q1' and 'ROS-Q2' in e.text for e in probe.events)


def test_cancel_at_nudge_wait_drops_the_queued_order(rig):
    """넛지 없이 끝난 세트(취소)는 회수 확인이 없다 — 예약 주문은 시작하지 않고 ABORTED 로 돌려준다."""
    proc,fake,probe,send,feedback=rig
    fake.attendant=False
    first=send('ROS-C1');assert first.accepted
    wait_until(lambda: proc._nudge_waiting,60)
    second=send('ROS-C2');assert second.accepted
    assert result(first.cancel_goal_async()).goals_canceling
    assert result(first.get_result_async()).result.result=='ABORTED'
    reply=result(second.get_result_async())
    assert reply.status==GoalStatus.STATUS_ABORTED and reply.result.result=='ABORTED'
    assert '넛지 없이' in reply.result.message
    assert proc.batch_id=='ROS-C1' and not any(c.startswith('move:') and 'ROS-C2' in c for c in fake.calls)
    wait_until(lambda: any(e.code=='ORDER_DROPPED' for e in probe.events))
    assert [e.batch_id for e in probe.events if e.code=='ORDER_DROPPED']==['ROS-C2'],'시작 못 한 예약의 근거는 그 주문 기록에'


def test_qa_discard_returns_discarded(rig):
    proc,fake,probe,send,feedback=rig
    fake.scoop_gain=10.0
    goal=send();assert goal.accepted
    dev=wait_until(proc._pending_dev,60)
    cli=probe.create_client(QaDecision,'qa_decision');assert cli.wait_for_service(timeout_sec=5)
    ack=result(cli.call_async(QaDecision.Request(deviation_id=dev.deviation_id,decision=Deviation.DISCARDED,operator_id='ros-test')))
    assert ack.accepted
    reply=result(goal.get_result_async(),60)
    assert reply.result.result=='DISCARDED' and not reply.result.success


def test_safety_stop_errors_without_resume(rig):
    proc,fake,probe,send,feedback=rig
    fake.attendant=False
    goal=send();assert goal.accepted
    wait_until(lambda: proc._nudge_waiting,60)
    fake.safety_stop()
    wait_until(lambda: proc._safety_stop)
    reply=result(goal.get_result_async())
    assert reply.status==GoalStatus.STATUS_ABORTED and reply.result.result=='ERROR'
    assert not send('BLOCKED').accepted
    calls=list(fake.calls)
    fake.safety_recovery(True,False)
    wait_until(lambda: not proc._safety_stop)
    time.sleep(.2)
    assert proc.fsm.mode=='ERROR' and fake.calls==calls
