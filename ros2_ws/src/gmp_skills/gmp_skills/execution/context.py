"""실행 객체가 공유하는 상태·설정과 명시적 외부 의존성. ROS 노드를 저장하지 않는다."""
from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field
from typing import Callable

from gmp_skills.core.nudge import NudgeDetector
from gmp_skills.core.transfer import MotionAnchor

@dataclass
class Job:
    kind: str
    args: dict
    done: threading.Event = field(default_factory=threading.Event)
    result: object = None
    error: str = ''
    cancel: bool = False
    feedback: object = None      # callable(phase) — 액션이면 피드백 발행



@dataclass(kw_only=True)
class SkillConfig:
    """YAML에서 읽는 운영 설정. 초기화 후 실행 객체들이 함께 참조한다."""
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
    """위치·파지·안전·큐 상태의 단일 원본. 잠금 범위는 기존 정책을 따른다."""
    station_id: str = ''
    motion_anchor: MotionAnchor | None = None
    held_payload: str = 'unknown'
    held_material_id: str = ''
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
    """장치와 ROS 기능의 최소 포트. 모든 시각은 주입한 노드 시계를 사용한다."""
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
