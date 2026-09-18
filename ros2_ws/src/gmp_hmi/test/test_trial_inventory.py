"""재고 고갈, 원자적 예약, 폐기, 보충 및 HMI 서버 차단 회귀 검사."""
import copy
import json
import time
from types import SimpleNamespace

import pytest

from gmp_hmi.core.trial_inventory import TrialInventory, validate_trial_snapshot
from test_v3_backend import backend, node, app_db, login, post, state, Client


def ledger():
    return TrialInventory(['A','B','C'], [500.0]*3, [500.0]*3)


def test_reject_shortage_atomically_and_refill_then_reserve():
    inv=ledger()
    for batch in ['one','two']:
        inv.reserve({'A':200.0,'B':150.0,'C':100.0},batch)
        for mid,amount in [('A',200.0),('B',150.0),('C',100.0)]:inv.consume(mid,amount)
        inv.release()
    before=inv.snapshot(True)
    with pytest.raises(ValueError,match='원료 부족'):
        inv.reserve({'B':150.0,'A':200.0},'three')
    assert inv.snapshot(True)==before
    assert inv.refill('A')==400.0
    assert inv.items['B']['remaining_g']==200.0
    assert inv.items['C']['remaining_g']==300.0
    inv.reserve({'A':200.0},'three')
    assert inv.items['A']['reserved_g']==200.0


def test_zero_after_last_dispense_is_valid_and_discard_never_refunds():
    inv=TrialInventory(['A','B'],[200.0,500.0],[200.0,500.0])
    inv.reserve({'A':200.0,'B':150.0},'one')
    inv.consume('A',200.0)
    assert inv.items['A']['remaining_g']==0.0
    inv.release()
    assert inv.items['A']['remaining_g']==0.0
    assert inv.items['B']['remaining_g']==500.0
    assert inv.items['B']['reserved_g']==0.0


def test_refill_preserves_current_reservation_and_does_not_overfill():
    inv=ledger();inv.reserve({'A':200.0,'B':150.0},'one');inv.consume('A',200.0)
    inv.refill('A')
    assert inv.batch_id=='one'
    assert inv.items['B']['reserved_g']==150.0
    assert inv.snapshot(True)['items'][1]['available_g']==350.0
    inv.refill('A');assert inv.items['A']['remaining_g']==500.0


@pytest.mark.parametrize('value',[-1.0,float('nan'),float('inf'),True])
def test_invalid_remote_snapshot_rejected(value):
    data=ledger().snapshot(True);data['items'][0]['remaining_g']=value
    with pytest.raises(ValueError):validate_trial_snapshot(data)


def enable(node, data=None):
    node.test_inventory_enabled=True
    node.cli_test_refill={mid: Client() for mid in ('A','B','C')}
    node.pub_test_height=SimpleNamespace(publish=node.published.append)
    node._on_test_inventory(SimpleNamespace(data=json.dumps(data or ledger().snapshot(True))))
    m=state('',0);m.step='IDLE';node._on_state(m)


def test_backend_insufficient_inventory_never_calls_ros(node,backend):
    inv=ledger();inv.reserve({'A':450.0},'prior');inv.consume('A',450.0);inv.release()
    enable(node,inv.snapshot(True))
    with pytest.raises(ValueError,match='원료 부족'):node.submit('demo_batch','operator')
    assert node.cli_order.calls==[]
    assert node.published[-1].code=='HMI_ORDER_REJECTED'


def test_backend_stale_and_malformed_inventory_fail_closed(node,backend):
    enable(node);node.test_inventory_received=time.monotonic()-100
    with pytest.raises(backend.CommandUnavailable):node.submit('demo_batch','operator')
    assert node.snapshot()['inventory']['fresh'] is False
    node._on_test_inventory(SimpleNamespace(data='{broken'))
    with pytest.raises(backend.CommandUnavailable):node.submit('demo_batch','operator')
    assert node.cli_order.calls==[]


def test_old_inventory_revision_does_not_revert_consumption(node):
    inv=ledger();before=inv.snapshot(True);inv.reserve({'A':200.0},'one');inv.consume('A',200.0)
    enable(node,inv.snapshot(False));node._on_test_inventory(SimpleNamespace(data=json.dumps(before)))
    assert node.snapshot()['inventory']['items'][0]['remaining_g']==300.0


def test_production_inventory_explicitly_unconnected_and_refill_rejected(node,backend):
    assert not node.test_inventory_enabled
    inv=node.snapshot()['inventory']
    assert inv['enforced'] is False and inv['refill_supported'] is False
    with pytest.raises(backend.CommandUnavailable,match='실제 C'):node.refill_test('admin','A',True)


def test_http_shortage_rejects_even_if_button_bypassed(app_db,node):
    app,_=app_db;client=app.test_client();token=login(client)
    inv=ledger();inv.reserve({'A':500.0},'prior');inv.consume('A',500.0);inv.release()
    enable(node,inv.snapshot(True))
    result=post(client,token,'/order',{'recipe':'demo_batch'})
    assert result.status_code==400 and result.json['ok'] is False
    assert node.cli_order.calls==[]


def test_http_refill_permission_and_csrf(app_db,node):
    app,_=app_db
    node.admin_store.create_user('viewer','long-enough-password','viewer')
    client=app.test_client();token=login(client,'viewer','long-enough-password')
    assert post(client,token,'/test/refill',{}).status_code==403
    assert client.post('/test/refill',json={}).status_code==403


def test_test_inventory_enable_forbidden_outside_namespace(backend,monkeypatch):
    Node=backend.HmiRosNode.__mro__[1]
    original=Node.declare_parameter
    def declare(self,key,value):
        return original(self,key,True if key=='test_inventory_enabled' else value)
    monkeypatch.setattr(Node,'declare_parameter',declare)
    monkeypatch.setattr(Node,'get_namespace',lambda self:'/cell')
    with pytest.raises(RuntimeError,match='/hmi_test'):backend.HmiRosNode()


def test_height_below_20_latches_independently_and_only_selected_refill_clears():
    inv=TrialInventory(['A','B','C'],[1000.0]*3,[1000.0]*3)
    inv.report_height('A',20.0)
    assert inv.blocked_materials==[]
    inv.report_height('A',19.99)
    inv.report_height('C',0)
    inv.report_height('A',100)
    assert inv.blocked_materials==['A','C']
    assert inv.items['A']['remaining_g']==1000.0
    with pytest.raises(ValueError,match='높이 부족'):inv.reserve({'B':40.0},'one')
    inv.refill('B')
    assert inv.blocked_materials==['A','C']
    inv.refill('A')
    assert inv.blocked_materials==['C'] and inv.items['A']['height_pct'] is None
    inv.refill('C');inv.reserve({'B':40.0},'one')
    assert inv.items['B']['reserved_g']==40.0


@pytest.mark.parametrize('height',[-1,101,True,None,'20',float('nan'),float('inf')])
def test_invalid_height_report_never_modifies_ledger(height):
    inv=ledger();before=copy.deepcopy(inv.snapshot(True))
    with pytest.raises(ValueError):inv.report_height('A',height)
    assert inv.snapshot(True)==before


def test_snapshot_must_preserve_height_latch_and_block_list():
    data=ledger().snapshot(True)
    data['items'][0]['height_pct']=10
    with pytest.raises(ValueError):validate_trial_snapshot(data)
    data['items'][0]['height_low_latched']=True
    with pytest.raises(ValueError):validate_trial_snapshot(data)
    data['blocked_materials']=['A']
    assert validate_trial_snapshot(data) is data


def test_production_submit_delegates_to_process_without_trial_inventory(node,backend):
    node._on_state(state('',0))
    assert node.submit('demo_batch','operator').accepted
    assert len(node.cli_order.calls)==1
    inv=node.snapshot()['inventory']
    assert inv['order_allowed'] is True and inv['enforced'] is False
    assert inv['refill_supported'] is False


def test_hmi_height_blocks_even_unused_material(node,tmp_path):
    (tmp_path/'only-b.yaml').write_text('product: B\nitems:\n  - {material_id: B, target_g: 40, tol_pct: 5}\n')
    inv=ledger();inv.report_height('C',10);enable(node,inv.snapshot(True))
    with pytest.raises(ValueError,match='높이 부족'):node.submit('only-b','operator')
    assert node.cli_order.calls==[]
    inv.refill('C');node._on_test_inventory(SimpleNamespace(data=json.dumps(inv.snapshot(True))))
    assert node.submit('only-b','operator').accepted


def test_zero_unused_material_does_not_block_recipe(node,tmp_path):
    (tmp_path/'only-b.yaml').write_text('product: B\nitems:\n  - {material_id: B, target_g: 40, tol_pct: 5}\n')
    inv=TrialInventory(['A','B','C'],[1000.0]*3,[0.0,1000.0,0.0])
    enable(node,inv.snapshot(True))
    assert node.submit('only-b','operator').accepted


def test_http_refill_validation_no_optimistic_update_and_actor(app_db,node):
    app,_=app_db;client=app.test_client();token=login(client)
    inv=ledger();inv.reserve({'A':100.0},'one');inv.consume('A',100.0);inv.release()
    enable(node,inv.snapshot(True))
    for body in ({},{'material_id':'ALL','confirmed_full':True},{'material_id':'A'},
                 {'material_id':'A','confirmed_full':'true'},{'material_id':'A','confirmed_full':1}):
        assert post(client,token,'/test/refill',body).status_code==400
    node.cli_test_refill['A'].response=SimpleNamespace(success=True,message='accepted')
    response=post(client,token,'/test/refill',{'material_id':'A','confirmed_full':True,'actor':'spoof'})
    assert response.json['ok']
    assert len(node.cli_test_refill['A'].calls)==1
    assert not node.cli_test_refill['B'].calls
    assert node.snapshot()['inventory']['items'][0]['remaining_g']==400
    event=node.published[-1]
    assert event.text.startswith('admin ') and 'material_id=A' in event.text and 'spoof' not in event.text
    inv.refill('A');node._on_test_inventory(SimpleNamespace(data=json.dumps(inv.snapshot(True))))
    assert node.snapshot()['inventory']['items'][0]['remaining_g']==500


def test_http_height_injection_is_test_only_authenticated_and_non_optimistic(app_db,node):
    app,_=app_db;client=app.test_client();token=login(client)
    body={'material_id':'A','height_pct':10,'actor':'spoof'}
    assert post(client,token,'/test/height',body).status_code==503
    enable(node)
    response=post(client,token,'/test/height',body)
    assert response.status_code==200 and response.json['applied'] is False
    assert node.snapshot()['inventory']['blocked_materials']==[]
    assert json.loads(node.published[-2].data)=={'material_id':'A','height_pct':10}
    assert node.published[-1].text.startswith('admin ') and 'spoof' not in node.published[-1].text
    assert post(client,token,'/test/height',{'material_id':'A','height_pct':True}).status_code==400
    node.admin_store.create_user('reader','long-enough-password','viewer')
    token=login(client,'reader','long-enough-password')
    assert post(client,token,'/test/height',body).status_code==403
