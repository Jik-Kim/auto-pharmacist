"""기존 회귀 fixture의 필드·메서드를 새 실행 객체에 연결하는 테스트 전용 도구.

운영 코드에는 호환용 프록시를 두지 않는다. 과거 fixture의 상태/모의 호출을
그대로 사용해 분리 전후의 동작·실패 경로를 같은 assertions로 비교한다.
"""

OWNERS = {'_cancel_requested': 'runtime',
 '_drain_jobs_locked': 'runtime',
 'shutdown': 'runtime',
 '_submit': 'runtime',
 '_worker': 'runtime',
 'check_startup': 'runtime',
 '_do_startup': 'runtime',
 '_restore_extracted_scoop': 'runtime',
 '_observe_force': 'safety',
 '_poll_nudge': 'safety',
 '_wait_with_nudge': 'safety',
 '_latch_safety': 'safety',
 '_poll_safety': 'safety',
 '_do_recover': 'safety',
 '_do_safe': 'safety',
 '_require_scoop_extracted': 'motion',
 '_do_move': 'motion',
 '_pose_matches': 'motion',
 '_record_arrival': 'motion',
 '_move_checked': 'motion',
 '_motion_scale': 'motion',
 '_leave_taught_station': 'motion',
 '_move_taught_station': 'motion',
 '_require_solution': 'motion',
 '_leave_solution_station': 'motion',
 '_move_solution_station': 'motion',
 '_require_transfer_payload': 'motion',
 '_run_transfer': 'motion',
 '_do_grip': 'motion',
 '_require_held_scoop': 'motion',
 '_pose_from_extra': 'motion',
 '_do_scoop': 'scooping',
 '_do_fixed_scoop': 'scooping',
 '_measure_surface_world': 'scooping',
 '_wait_compliance_settle': 'scooping',
 '_do_check_depth': 'scooping',
 '_do_pour': 'scooping',
 '_do_taught_pour': 'scooping',
 '_do_return_material': 'scooping',
 '_scale_period_s': 'weighing',
 '_do_measure': 'weighing',
 '_measure_weight_reading': 'weighing',
 '_do_weigh': 'weighing',
 '_do_weigh_held': 'weighing'}
PORTS = {'get_parameter': 'parameter', 'get_clock': 'clock', 'get_logger': 'logger', '_now_s': 'now', 'event': 'event'}

class AttributeView:
    def __init__(self, node, prefix=''):
        object.__setattr__(self, 'node', node)
        object.__setattr__(self, 'prefix', prefix)

    def __getattr__(self, key):
        return getattr(self.node, self.prefix + key)

    def __setattr__(self, key, value):
        setattr(self.node, self.prefix + key, value)


class ContextView(AttributeView):
    def __getattr__(self, key):
        if key == 'config':
            return AttributeView(self.node)
        if key == 'state':
            return AttributeView(self.node, '_')
        for old, new in PORTS.items():
            if key == new:
                return lambda *a, **kw: getattr(self.node, old)(*a, **kw)
        return super().__getattr__(key)


def install_legacy_fixture(module):
    """기존 노드 fixture만 연결하며 운영 SkillNode는 변경하지 않는다."""
    from gmp_skills.execution import SkillExecution
    real_node = module.SkillNode

    def ensure(node):
        if 'execution' in vars(node):
            return
        node.ctx = ContextView(node)
        node.ctx.ok = lambda: module.rclpy.ok()
        node.execution = SkillExecution(node.ctx)
        for name, owner in OWNERS.items():
            component = getattr(node.execution, owner)
            original = getattr(component, name)
            def call(*args, _name=name, _original=original, **kwargs):
                override = vars(node).get(_name)
                return (override or _original)(*args, **kwargs)
            setattr(component, name, call)
        class Handlers(dict):
            def __getitem__(self, kind):
                override = vars(node).get('_do_' + kind)
                if override is not None:
                    return override
                name = '_do_' + kind
                return getattr(getattr(node.execution, OWNERS[name]), name)
        node.execution.runtime.handlers = Handlers()

    class LegacyFixture(real_node):
        pass

    for name, owner in OWNERS.items():
        def call(node, *args, _name=name, _owner=owner, **kwargs):
            ensure(node)
            component = getattr(node.execution, _owner)
            method = getattr(type(component), _name)
            if _name == '_pose_from_extra':
                return method(*args, **kwargs)
            return method(component, *args, **kwargs)
        setattr(LegacyFixture, name, call)
    for name, method in vars(real_node).items():
        if callable(method) and name not in OWNERS and name != '__init__':
            def call(node, *args, _method=method, **kwargs):
                ensure(node)
                return _method(node, *args, **kwargs)
            setattr(LegacyFixture, name, call)
    module.RealSkillNode = real_node
    module.SkillNode = LegacyFixture
    return module
