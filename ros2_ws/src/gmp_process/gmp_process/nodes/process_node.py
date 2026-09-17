"""공정 노드 — ProcessFSM 의 요청을 스킬 Action/Service 로 실행하고 결과를 돌려준다.

입력  submit_order · qa_decision · interlock (Service 서버), run_batch (Action 서버)
      스킬: move_to_station·scoop·pour·weigh_container (Action 클라이언트), grip·measure_force·safe_pose (Service 클라이언트)
출력  state (2 Hz + 변화, TRANSIENT_LOCAL) · weight · dispense_result · deviation (TRANSIENT_LOCAL) · event

구조: rclpy 콜백은 값만 저장한다. 배치 실행 루프는 별도 스레드(run_loop)에서 돌며 스킬을 **한 번에 하나** 부른다.
QA 판정·인터락은 콜백이 Event 를 세우고 루프가 기다린다.

TODO([C]) 9/18: 스킬 클라이언트 6종 연결, 발행 5종, 가상에서 레시피 1건 완주.
"""
import threading

import rclpy
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile

from gmp_interfaces.msg import CellEvent, CellState, Deviation, DispenseResult, WeightReading
from gmp_interfaces.srv import InterlockRequest, QaDecision, SubmitOrder

from gmp_dosing.core.dosing import DosingConfig
from gmp_dosing.core.scale import ScaleConfig, WeightModel
from gmp_process.core.process_fsm import ProcessFSM
from gmp_process.core.recipe import Item, RecipeSpec

LATCHED = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)


class ProcessNode(Node):
    def __init__(self):
        super().__init__('process_node')
        self.declare_parameters('', [
            ('scale.method', 'workpiece'), ('scale.gain', 1.0), ('scale.offset_g', 0.0),
            ('scale.min_resolvable_g', 30.0), ('scale.max_std_g', 10.0),
            ('dosing.max_attempts', 3), ('dosing.scoop_nominal_g', 40.0), ('dosing.min_fraction', 0.15),
            ('gripper.scoop_width_mm', 18.0), ('gripper.cup_width_mm', 60.0), ('gripper.force_n', 20.0),
        ])
        p = lambda k: self.get_parameter(k).value  # noqa: E731
        self.scale = WeightModel(ScaleConfig(p('scale.method'), p('scale.gain'), p('scale.offset_g'),
                                             p('scale.min_resolvable_g'), p('scale.max_std_g')))
        self.dosing_cfg = DosingConfig(p('dosing.max_attempts'), p('dosing.scoop_nominal_g'), p('dosing.min_fraction'))

        self.pub_state = self.create_publisher(CellState, 'state', LATCHED)
        self.pub_weight = self.create_publisher(WeightReading, 'weight', 20)
        self.pub_result = self.create_publisher(DispenseResult, 'dispense_result', 50)
        self.pub_dev = self.create_publisher(Deviation, 'deviation', QoSProfile(depth=10, durability=DurabilityPolicy.TRANSIENT_LOCAL))
        self.pub_event = self.create_publisher(CellEvent, 'event', 100)
        self.create_service(SubmitOrder, 'submit_order', self._srv_submit)
        self.create_service(QaDecision, 'qa_decision', self._srv_qa)
        self.create_service(InterlockRequest, 'interlock', self._srv_interlock)
        self.create_timer(0.5, self._pub_state)

        self.fsm: ProcessFSM | None = None
        self.batch_id = ''
        self._qa = threading.Event(); self._qa_decision = None
        self._interlock_exit = threading.Event()
        self._thread = None
        # TODO([C]): 스킬 Action/Service 클라이언트 6종 (from gmp_interfaces.action/srv)

    # ── 서비스 ───────────────────────────────────────────────────────
    def _srv_submit(self, req, res):
        if self.fsm and self.fsm.mode in ('RUNNING', 'PAUSED', 'DEVIATION'):
            res.accepted, res.message = False, f'실행 중 ({self.fsm.state})'
            return res
        items = [Item(i.material_id, i.target_g, i.tol_pct, i.grade, i.scoop_id or i.material_id) for i in req.recipe.items]
        self.batch_id = req.recipe.batch_id or self.get_clock().now().to_msg().sec.__str__()
        self.fsm = ProcessFSM(RecipeSpec(req.recipe.product, items), self.dosing_cfg, self.scale)
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        res.accepted, res.batch_id, res.message = True, self.batch_id, 'accepted'
        return res

    def _srv_qa(self, req, res):
        if not (self.fsm and self.fsm.mode == 'DEVIATION'):
            res.accepted, res.message = False, '판정 대기 중인 일탈 없음'
            return res
        self._qa_decision = 'APPROVED' if req.decision == Deviation.APPROVED else 'DISCARDED'
        self._qa.set()
        res.accepted, res.message = True, self._qa_decision
        return res

    def _srv_interlock(self, req, res):
        # TODO([C]): ENTER 는 safe_pose 성공 후 granted. 지금은 EXIT 만 처리
        if req.request == InterlockRequest.EXIT:
            self._interlock_exit.set()
            res.granted, res.message = True, 'resume'
        else:
            res.granted, res.message = False, 'TODO: safe_pose 연동'
        return res

    # ── 실행 루프 (별도 스레드) ────────────────────────────────────────
    def _run_loop(self):
        fsm = self.fsm
        req = fsm.start()
        while req and rclpy.ok():
            result = self._execute(req)
            req = fsm.on_result(req, result)
            self._pub_state()

    def _execute(self, req: dict) -> dict:
        """요청 kind 별 스킬 호출. TODO([C]) 9/18 — 지금은 골격.

        carry (D-18): MoveToStation(src, ABOVE) → (src, AT, slot) → Grip(close, gripper.cup_width_mm) → (src, ABOVE)
                      → MoveToStation(dst, ABOVE) → (dst, AT, slot) → Grip(open) → (dst, ABOVE).
                      결과 {'grip_inferred': Grip 결과}. 슬롯 오프셋은 stations.yaml slot_pitch_mm × slot.
        """
        k = req['kind']
        if k == 'wait_qa':
            self._qa.clear(); self._qa.wait()
            return {'decision': self._qa_decision}
        if k == 'wait_interlock':
            self._interlock_exit.clear(); self._interlock_exit.wait()
            return {}
        raise NotImplementedError(f'TODO([C]): 스킬 호출 {k}')

    def _pub_state(self):
        m = CellState()
        m.header.stamp = self.get_clock().now().to_msg()
        if self.fsm:
            m.mode = getattr(CellState, self.fsm.mode, CellState.IDLE)
            m.step, m.item_index, m.batch_id = self.fsm.state, self.fsm.idx, self.batch_id
        self.pub_state.publish(m)


def main(args=None):
    rclpy.init(args=args)
    node = ProcessNode()
    ex = MultiThreadedExecutor()
    ex.add_node(node)
    try:
        ex.spin()
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node(); rclpy.shutdown()


if __name__ == '__main__':
    main()
