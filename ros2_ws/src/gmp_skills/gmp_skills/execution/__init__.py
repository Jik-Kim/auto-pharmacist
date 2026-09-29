"""한 ExecutionContext를 실행 담당 객체들이 공유하도록 연결한다.

여기서는 ROS 노드나 워커 스레드를 새로 만들지 않는다. Job 실행 스레드는
SkillRuntime 하나이며, 이동·안전·스쿠핑·계량 객체가 같은 상태를 사용한다.
"""
from .context import ExecutionContext, Job
from .runtime import SkillRuntime
from .safety import SafetyController
from .motion import MotionSkills
from .scooping import ScoopingSkills
from .weighing import WeighingSkills


class SkillExecution:
    def __init__(self, ctx):
        """실행 객체를 만들고 runtime.handlers에 작업 종류별 담당 메서드를 등록한다."""
        self.runtime = SkillRuntime(ctx)
        self.motion = MotionSkills(ctx, self.runtime)
        self.safety = SafetyController(ctx, self.runtime)
        self.scooping = ScoopingSkills(ctx, self.motion, self.safety, self.runtime)
        self.weighing = WeighingSkills(ctx, self.motion, self.safety, self.runtime)
        self.runtime.configure(safety=self.safety, motion=self.motion,
                               scooping=self.scooping, weighing=self.weighing)

__all__ = ["ExecutionContext", "Job", "SkillExecution"]
