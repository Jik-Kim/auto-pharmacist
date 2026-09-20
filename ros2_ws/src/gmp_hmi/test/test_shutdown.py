"""실제 main 종료 경로 실행. ROS/DDS 대신 종료 경합을 재현하는 스텁 사용."""
import ast
from pathlib import Path
import sys
import threading
from types import ModuleType, SimpleNamespace

import pytest


@pytest.mark.parametrize('web', [False, True])
@pytest.mark.parametrize('already_stopped', [False, True])
def test_shutdown_order(monkeypatch, web, already_stopped):
    calls = []
    stopped = threading.Event()
    class ExternalShutdownException(Exception):
        pass
    module = ModuleType('rclpy.executors')
    module.ExternalShutdownException = ExternalShutdownException
    monkeypatch.setitem(sys.modules, 'rclpy.executors', module)
    context = SimpleNamespace(ok=lambda: not stopped.is_set())
    db = SimpleNamespace(close=lambda: calls.append('db'))
    node = SimpleNamespace(
        context=context, db=db,
        destroy_node=lambda: calls.append('node'),
        get_parameter=lambda _: SimpleNamespace(value=5002),
        get_logger=lambda: SimpleNamespace(info=lambda _: None),
        admin_store=SimpleNamespace(setup_required=lambda: False))
    def spin(*_):
        if web:
            stopped.wait(2)
        elif already_stopped:
            stopped.set()
        if stopped.is_set():
            raise ExternalShutdownException()
        raise KeyboardInterrupt()
    def shutdown():
        calls.append('executor')
        stopped.set()
    def run(**_):
        if already_stopped:
            stopped.set()
        # Flask 정상 종료와 SIGINT 후 종료의 공통 finally 경로를 실행한다.
    env = dict(
        rclpy=SimpleNamespace(init=lambda **_: None, spin=spin,
                              try_shutdown=lambda: calls.append('context')),
        RecordNode=lambda: node, HmiRosNode=lambda: node,
        ReadOnlyCellDB=lambda _: db, threading=threading,
        MultiThreadedExecutor=lambda **_: SimpleNamespace(
            add_node=lambda _: None, spin=spin, shutdown=shutdown),
        build_app=lambda *_: SimpleNamespace(run=run))
    name = 'hmi_web_node.py' if web else 'record_node.py'
    path = Path(__file__).parents[1] / 'gmp_hmi/nodes' / name
    tree = ast.parse(path.read_text())
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
    exec(compile(ast.Module(body=[main], type_ignores=[]), str(path), 'exec'), env)
    env['main']()
    assert calls == (['executor'] if web else []) + ['db', 'node', 'context']
