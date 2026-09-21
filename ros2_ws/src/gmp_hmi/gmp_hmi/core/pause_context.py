"""CellState의 표시용 정지 사유. 제어·안전 허가 판단에는 사용하지 않는다.

pause_reason은 ROS 필드가 아니라 HMI의 파생 표시값이다.
PAUSED만으로 REFILL/안전 자세를 추정하지 않으며 원문 note를 보존한다.
"""
import re


def pause_reason(mode: str, step: str, note: str) -> str:
    if mode not in ('PAUSED', 'IDLE', 'DONE'):
        return ''
    # 세트 완료 대기는 접촉으로 인한 정지와 별개다.
    if step == 'NUDGE_WAIT':
        return 'SET_COMPLETE'
    text = (note or '').strip()
    if text.startswith(('인터락 ENTER', '구역 진입 요청 유지')) or re.match(r'^INTERLOCK\b', text):
        return 'INTERLOCK'
    for reason in ('NUDGE', 'REFILL', 'HEIGHT_LOW'):
        if re.match(r'^' + reason + r'\b', text):
            return reason
    return ''
