from gmp_process.core.safety_events import SafetyEvents


def started(revision=2, session='a1', **extra):
    return dict(safety_session=session, safety_revision=revision,
                origin='recovery_request', request_id='r1', operator_id='op', **extra)


def result(revision=2, session='a1', **extra):
    data = started(revision, session)
    data.update(success=True, manual_required=False, robot_state=1)
    data.update(extra)
    return data


def test_only_matching_completed_recovery_unlocks():
    gate = SafetyEvents()
    assert not gate.accepts(result())
    gate.stop(started())
    assert gate.accepts(result())
    for change in ({'request_id': 'other'}, {'operator_id': 'other'},
                   {'success': 'true'}, {'manual_required': True}, {'robot_state': 8}):
        assert not gate.accepts(result(**change))


def test_new_alarm_rejects_late_success_and_late_start():
    gate = SafetyEvents()
    gate.stop(started())
    gate.stop(dict(safety_session='a1', safety_revision=3, origin='robot_alarm'))
    gate.stop(started())
    assert not gate.accepts(result())
    gate.stop(started(4))
    assert gate.accepts(result(4))


def test_unknown_stop_invalidates_old_request():
    gate = SafetyEvents()
    gate.stop(started())
    gate.stop({})
    gate.stop(started())
    assert not gate.accepts(result())


def test_restart_rejects_retired_session():
    gate = SafetyEvents()
    gate.stop(started())
    gate.stop(started(1, 'a2'))
    assert gate.accepts(result(1, 'a2'))
    gate.stop(started(3))
    assert not gate.accepts(result(3))
