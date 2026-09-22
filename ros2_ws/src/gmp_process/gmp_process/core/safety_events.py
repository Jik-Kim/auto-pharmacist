"""A 기동 세션·정지 세대로 지연된 복구 성공을 걸러낸다. 로봇 제어는 하지 않는다."""


class SafetyEvents:
    def __init__(self):
        self.session = None
        self.revision = -1
        self.retired = set()
        self.request_id = None
        self.invalidated = set()

    @staticmethod
    def token(data):
        session, revision = data.get('safety_session'), data.get('safety_revision')
        if not isinstance(session, str) or not session or type(revision) is not int or revision < 0:
            return None
        return session, revision

    def stop(self, data):
        if data.get('origin') != 'recovery_request':
            self.invalidated.add((self.session, self.revision))
        self.request_id = None  # 알 수 없는 정지도 기존 성공 적용을 무효화한다.
        token = self.token(data)
        if token is None:
            return
        session, revision = token
        if (token in self.invalidated or session in self.retired
                or (session == self.session and revision < self.revision)):
            return
        if self.session is not None and session != self.session:
            self.retired.add(self.session)
        self.session, self.revision = token
        if (data.get('origin') == 'recovery_request'
                and isinstance(data.get('request_id'), str) and data['request_id']):
            self.request_id = data['request_id']

    def accepts(self, data):
        return (self.request_id is not None
                and self.token(data) == (self.session, self.revision)
                and self.request_id == data.get('request_id')
                and data.get('success') is True and data.get('manual_required') is False
                and type(data.get('robot_state')) is int and data['robot_state'] == 1)
