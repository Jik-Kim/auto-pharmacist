"""계약 v1.3 상태·결과 이름이 HMI 정적 자산에 연결됐는지 확인한다."""
import re
from pathlib import Path


HMI_JS = Path(__file__).resolve().parents[1] / 'static/hmi.js'
# 빌드 없이도 돌도록 소스 트리 상대 경로로 읽는다 (.resolve() 라 --symlink-install 에서도 같다).
PROCESS_FSM = Path(__file__).resolve().parents[2] / 'gmp_process/gmp_process/core/process_fsm.py'

# process_fsm 이 CellState.step 으로 내보내는 상태 이름을 뽑는 패턴들.
_STATE_PATTERNS = (
    r"self\.state(?:,\s*self\.mode)?\s*=\s*'([A-Z_]+)'",   # self.state = 'X' / self.state, self.mode = 'X', …
    r"self\.cur,\s*self\.state\s*=\s*[^,]+,\s*'([A-Z_]+)'",  # self.cur, self.state = …, 'X'
    r"st == '([A-Z_]+)'",                                   # 분기에서 비교하는 상태
    r"state:\s*str\s*=\s*'([A-Z_]+)'",                      # 초기 상태
    r"_final:\s*str\s*=\s*'([A-Z_]+)'",                     # NUDGE_WAIT 뒤 종료 상태 기본값
    r"_park\('([A-Z_]+)'\)",                                # _park(final) 로 넘기는 종료 상태
)


def _fsm_states():
    source = PROCESS_FSM.read_text(encoding='utf-8')
    return {m for p in _STATE_PATTERNS for m in re.findall(p, source)}


def _hmi_step_keys():
    source = HMI_JS.read_text(encoding='utf-8')
    body = re.search(r'const steps=\{(.*?)\};', source, re.S).group(1)
    return set(re.findall(r"([A-Z_]+):'", body))


def test_return_material_labels_are_exposed():
    source = HMI_JS.read_text(encoding='utf-8')

    assert "RETURN_MATERIAL:'초과 원료 반환'" in source
    assert "5:'원료 반환'" in source
    assert "6:'원료 반환 실패'" in source


def test_steps_map_matches_process_fsm_states():
    # steps 에 없는 상태는 화면에 영문 원시 문자열로 노출되고(NUDGE_WAIT·CLEANUP 사례),
    # FSM 에 없는 키는 존재하지 않는 계약을 있는 것처럼 보이게 한다 (옛 WEIGH·WAIT_QA 등).
    fsm, hmi = _fsm_states(), _hmi_step_keys()

    assert not fsm - hmi, f'HMI steps 맵에 없는 공정 상태: {sorted(fsm - hmi)}'
    assert not hmi - fsm, f'공정에 없는 HMI steps 키: {sorted(hmi - fsm)}'


def test_progress_strip_never_calls_invalid_item_complete():
    """INVALID 원료를 진행 스트립이 「완료」(초록)로 표시하면 안 된다 (#242).

    v1.8 의 INVALID 는 WEIGH_INVALID 일탈을 QA 가 APPROVED 해야만 발행된다. 그래서
    `devs.some(APPROVED)` 가 항상 참이라, 이 가지를 막지 않으면 결과 배지(bad)와
    진행 스트립(done)이 같은 원료를 두고 서로 다른 말을 한다.
    """
    source = HMI_JS.read_text(encoding='utf-8')
    complete_expr = re.search(r'complete=(.*?),current=', source, re.S).group(1)

    assert 'unmeasured' in complete_expr, f'complete 가 INVALID 를 거르지 않는다: {complete_expr}'
    assert "unmeasured=r?.verdict==='INVALID'" in source
    assert "'투입량 미확인'" in source
    # v1.8 의 INVALID 는 재계량 예정이 아니라 「모른 채 넘어감」이다.
    assert "'재계량'" not in source
