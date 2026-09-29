"""한 로봇 워커가 사용하는 설정, 현재 상태, 외부 기능을 모아 둔다.

Action/Service 콜백은 Job을 큐에 넣고 기다린다. 워커가 Job을 처리하며
SkillState를 갱신한다. 여러 실행 객체가 같은 상태를 보도록 이 문맥을 공유한다.
"""
from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field
from typing import Callable

from gmp_skills.core.nudge import NudgeDetector
from gmp_skills.core.transfer import MotionAnchor

@dataclass
class Job:
    """ROS 요청 하나를 워커에 전달하고 완료·실패·취소를 콜백에 알리는 봉투."""
    kind: str
    args: dict
    done: threading.Event = field(default_factory=threading.Event)
    result: object = None
    error: str = ''
    cancel: bool = False
    feedback: object = None      # Action 진행 단계(phase)를 ROS 피드백으로 보내는 함수



@dataclass(kw_only=True)
class SkillConfig:
    """common.yaml 등의 ROS 파라미터에서 읽은 값을 실행 객체에 전달한다."""
    mode: str | None = None
    height_measure_only: bool | None = None
    vel_scale: float | None = None
    motion_timeout_s: float | None = None
    scoop_extract_y_mm: float | None = None
    scoop_extract_lift_z_mm: float | None = None
    transfer_joint_vel: float | None = None
    transfer_joint_acc: float | None = None
    pose_xyz_tolerance: float | None = None
    pose_rotation_tolerance: float | None = None
    joint_tolerance: float | None = None
    shutdown_timeout_s: float | None = None
    state_poll_s: float | None = None
    recovery_timeout_s: float | None = None
    recovery_cache_size: int | None = None


@dataclass(kw_only=True)
class SkillState:
    """작업 사이에 이어지는 로봇 위치·파지·안전·큐 상태의 단일 원본.

    motion_anchor는 확인된 출발 TCP/관절 자세, held_payload는 파지물의 종류다.
    safety_latched가 참이면 새 일반 작업을 막는다. safety_revision은 새 알람이나
    차단 변경마다 증가하며, 복구 도중 상태가 바뀌었는지 검사하는 데 쓴다.
    """
    station_id: str = ''
    motion_anchor: MotionAnchor | None = None
    held_payload: str = 'unknown'
    held_material_id: str = ''
    resume_grip: dict | None = None  # SafePose 직전 이력. 센서 확인 없이 복원하지 않는다.
    resume_grip_ready: bool = False
    empty_scoop_force_baseline: dict | None = None
    empty_scoop_baseline_pending: bool = False
    return_rescoop_blocked: bool = False
    pending_scoop_extract: bool = False
    scoop_extract_uncertain: bool = False
    nudge_enabled: bool = False
    nudge: NudgeDetector | None = None
    nudge_fault_logged: bool = False
    q: queue.Queue[Job] = field(default_factory=queue.Queue)
    job_lock: threading.Lock = field(default_factory=threading.Lock)
    current: Job | None = None
    stopping: threading.Event = field(default_factory=threading.Event)
    worker_stopped: threading.Event = field(default_factory=threading.Event)
    cleanup_error: str = ''
    safety_latched: bool = False
    safety_reason: str = ''
    safety_revision: int = 0
    safety_session: str = ''
    last_robot_state: int = -1
    last_state_poll: float = float('-inf')
    configured: bool = False
    recovery_requests: dict = field(default_factory=dict)
    recovery_inflight: bool = False
    worker_thread: threading.Thread | None = None
    ready: bool = False
    startup_job: Job | None = None


@dataclass(kw_only=True)
class ExecutionContext:
    """실행 객체가 사용할 로봇·그리퍼·스테이션과 ROS 기능을 연결한다.

    실행 객체는 ROS 노드 자체를 받지 않고 필요한 함수만 받는다. now/clock은
    skill_node의 같은 ROS 시계를 사용해 계량값과 이벤트 시각을 맞춘다.
    """
    parameter: Callable
    clock: Callable
    now: Callable
    logger: Callable
    event: Callable
    ok: Callable
    config: SkillConfig = field(default_factory=SkillConfig)
    state: SkillState = field(default_factory=SkillState)
    arm: object = None
    gripper: object = None
    stations: object = None
