"""HMI 관측/요청 상관관계. 로봇 안전 판단·배치 재개는 하지 않는다."""
import copy
import secrets


class SafetyRecovery:
    def __init__(self):
        self.generation = 0
        self.active = False
        self.robot_state = -1
        self.reason = ''
        self.phase = 'unobserved'
        self.message = '안전 이벤트 미관측 · 정상 상태를 보장하지 않습니다'
        self.request = None

    def stop(self, data):
        request = self.request
        if (request and data.get('origin') == 'recovery_request'
                and data.get('request_id') == request['request_id']
                and data.get('operator_id') == request['operator_id']
                and request['generation'] == self.generation):
            # 복구 시작은 해제 근거가 아니다. 응답이 먼저 왔거나 중복 수신돼도 결과를 덮지 않는다.
            return
        self.generation += 1
        self.active = True
        self.robot_state = data.get('robot_state', -1)
        if type(self.robot_state) is not int:
            self.robot_state = -1
        self.reason = str(data.get('reason', '안전정지 이벤트 수신 · 상세 미확인'))
        self.phase = 'stopped'
        self.message = '원인 제거 및 현장 상태 확인이 필요합니다. 배치는 자동 재개되지 않습니다.'
        self.request = None

    def begin(self, actor, expected_state, confirmed, batch_id, generation):
        if confirmed is not True:
            raise ValueError('현장 원인 제거·필요한 자세 교정/펜던트 조치를 확인하세요')
        if type(expected_state) is not int or expected_state not in (1, 3, 5, 8, 9, 10):
            raise ValueError('복구 가능한 실제 컨트롤러 상태를 현장에서 확인하세요')
        if type(generation) is not int or generation != self.generation:
            raise ValueError('새 안전정지 신호가 있습니다. 상태를 다시 확인하세요')
        if self.phase in ('pending', 'uncertain'):
            raise ValueError('이전 요청 결과 미확인: 새 요청을 보내지 말고 상태를 확인하세요')
        self.request = dict(request_id=secrets.token_hex(16), operator_id=actor,
                            expected_state=expected_state, operator_confirmed=True,
                            generation=self.generation, batch_id=batch_id)
        self.active = True
        self.phase = 'pending'
        self.message = 'C에 복구 요청 중 · 자동 재전송/배치 재개 없음'
        return copy.deepcopy(self.request)

    def finish(self, request, result):
        if (not self.request or self.request['request_id'] != request['request_id']
                or request['generation'] != self.generation):
            return False
        if result is None:
            self.phase = 'uncertain'
            self.message = '복구 응답 미확인 · 자동 재요청하지 않습니다. 연결/서버 로그를 확인하세요.'
            return True
        valid = (type(result.get('success')) is bool
                 and type(result.get('manual_required')) is bool
                 and type(result.get('robot_state')) is int)
        if not valid:
            return self.finish(request, None)
        self.robot_state = result['robot_state']
        self.active = not (result['success'] and not result['manual_required'] and self.robot_state == 1)
        self.phase = ('recovered' if not self.active else
                      'manual_required' if result['manual_required'] else 'failed')
        self.message = str(result.get('message', ''))
        return True

    def retry(self, actor, request_id):
        if (self.phase != 'uncertain' or not self.request
                or self.request['request_id'] != request_id
                or self.request['operator_id'] != actor
                or self.request['generation'] != self.generation):
            raise ValueError('이 작업자의 결과 미확인 요청만 같은 ID와 내용으로 재확인할 수 있습니다')
        self.phase = 'pending'
        self.message = '같은 요청 ID·원본 내용으로 C에 결과 재확인 중'
        return copy.deepcopy(self.request)

    def snapshot(self):
        return dict(generation=self.generation, active=self.active, robot_state=self.robot_state,
                    reason=self.reason, phase=self.phase, message=self.message,
                    request_id=self.request['request_id'] if self.request else '',
                    source='observed_events_and_this_hmi_requests')
