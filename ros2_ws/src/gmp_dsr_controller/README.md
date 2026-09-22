# DSR 충돌 감도 조회 확장

기존 `dsr_controller2::RobotController`를 상속해 동일한 `Drfl` 연결에 읽기 전용
`system/get_collision_sensitivity` 서비스를 추가한다. 로봇 접속·제어권 획득·감도 변경은
추가하지 않는다. 사용자는 `skill_node`의 단일 DSR 워커이며 HMI/process는 직접 호출하지 않는다.

- 설치된 벤더의 exported target에서 `DRCF_VERSION`과 ABI 의존성을 이어받는다.
- 컨트롤러 이름은 `dsr_controller2`를 유지하고 플러그인 종류만 바꾼다.
- `get_safety_configuration()->_fCollisionSensitivity`를 값으로 복사한다.
  null·예외·NaN/Inf·0~100 밖의 값은 실패다. 실패 응답 값은 NaN이며 사용하지 않는다.
- 활성화 후 서비스를 만들고 비활성화/정리/오류/종료 시 서비스를 제거한다.
- 기본 콜백 그룹을 사용한다. SDK 호출 자체의 중단 시간을 보장하지 않으며,
  Python 클라이언트의 응답 제한은 서버 SDK 호출 취소를 의미하지 않는다.
- 전역 설정 조회이며 로컬 안전 구역의 재정의나 실제 충돌 검출 성능은 별도 검증이다.

공식 근거: [API v1.33 get_safety_configuration](https://v2-manual.scroll.site/ko/api/1.33/publish/cdrflex-get_safety_configuration),
[GL013301 조회 예제](https://doosanrobotics.github.io/doosan-robotics-api-manual/GL013301/mode/common/safety_configuration_param/get_safety_configuration_ex.html).
함수 표기는 매뉴얼별로 다르며, 빌드는 설치된 벤더 헤더의 `get_safety_configuration()`을 사용한다.

모의 테스트는 반환값 유효성 및 플러그인 로딩을 확인한다. 실제 컨트롤러의 감도 응답,
기동·재기동은 실물 검증 전까지 완료로 보지 않는다. 벤더 업데이트 때 상속 API·ABI와
런치 구조를 다시 확인한다.
