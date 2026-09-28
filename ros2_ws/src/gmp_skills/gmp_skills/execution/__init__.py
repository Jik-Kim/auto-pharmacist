"""스킬 실행 객체의 조립. 별도 ROS 노드나 추가 워커를 생성하지 않는다."""
from .context import ExecutionContext, Job
from .runtime import SkillRuntime
from .safety import SafetyController
from .motion import MotionSkills
from .scooping import ScoopingSkills
from .weighing import WeighingSkills


class SkillExecution:
    def __init__(self, ctx):
        """공유 상태를 한 번 만들고 필요한 실행 객체끼리 명시적으로 연결한다."""
        self.runtime = SkillRuntime(ctx)
        self.motion = MotionSkills(ctx, self.runtime)
        self.safety = SafetyController(ctx, self.runtime)
        self.scooping = ScoopingSkills(ctx, self.motion, self.safety, self.runtime)
        self.weighing = WeighingSkills(ctx, self.motion, self.safety, self.runtime)
        self.runtime.configure(safety=self.safety, motion=self.motion,
                               scooping=self.scooping, weighing=self.weighing)

__all__ = ["ExecutionContext", "Job", "SkillExecution"]
