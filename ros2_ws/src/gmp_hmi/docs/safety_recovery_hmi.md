# HMI 안전정지 복구

기준: docs/interfaces.md §8 v1.6 확정.

- HMI POST /recover → 상대 서비스 request_safety_recovery (운영 /cell/request_safety_recovery).
- A /cell/recover_safety 또는 DSR 제어 서비스를 직접 호출하지 않는다.
- operator/admin 세션과 CSRF를 확인한다. operator_id는 세션에서만 얻는다.
- 서버가 요청 ID를 발급한다. 관측 상태를 expected_state로 보내며 작업자는 단일 버튼으로 현장 조치 완료와 복구 요청을 명시한다. 미관측/미연결이면 차단한다.
- 실제/불명 STOP마다 세대를 바꾸고 이전 요청/진입 허가를 무효화한다. 현재 요청 ID가 일치하는 origin=recovery_request만 요청을 유지한다. 작업자 ID는 감사 기록이며 인수인계 여부를 제한하지 않는다. 새 STOP 뒤 이전 응답은 감사 기록만 남긴다.
- 응답 대기/미확인/수동 조치/실패/복구 확인을 구분한다. 응답 미확인은 자동 재전송하지 않는다.
- 응답 미확인 때 operator/admin은 동일 ID와 저장된 전체 요청으로 수동 재확인할 수 있다.
- success=true, manual_required=false, robot_state=1일 때만 이 HMI의 복구 확인 표시를 해제한다.
- 배치 상태는 C의 CellState만 사용한다. HMI는 ERROR를 IDLE로 바꾸거나 RunBatch/EXIT를 자동 요청하지 않는다.
- 복구 성공은 진입 허가가 아니다. 다음 작업 전 명시적 안전 자세/현장 재설정이 필요하다.
- 모든 요청/응답/타임아웃은 HMI_SAFETY_RECOVERY_* 이벤트로 발행한다. SQLite 기록자는 기존 record_node다.
- PAUSED만으로 안전 자세라고 표시하지 않는다. 인터락 진입 허가 응답과 NUDGE 정지를 구분한다.

## 선행 조건

업로드된 인터페이스에는 RecoverSafety가 없었다. 최신 main을 팀 절차대로 병합하고
gmp_interfaces부터 gmp_hmi까지 colcon build --symlink-install --packages-up-to gmp_hmi를 실행해야 한다.
미설치/서비스 미연결이면 복구 요청을 차단한다. 이 PR은 A/C/D 이벤트 처리를 함께 변경하므로 동일 버전으로 배포한다.

## 검증

1. pytest -q src/gmp_hmi/test: 순수 로직 + Flask + ROS 클라이언트 스텁. 실제 DDS 검증 아님.
2. node src/gmp_hmi/tools/test_safety_popup.cjs: DOM 스텁. 실제 브라우저 렌더링 검증 아님.
3. ROS_DOMAIN_ID=88 RMW_IMPLEMENTATION=rmw_fastrtps_cpp python3 src/gmp_hmi/tools/verify_safety_recovery_ros.py
   실제 DDS에서 별도 /hmi_safety_test 시험 C 응답기를 사용한다. 실제 C/A/물리 로봇 검증 아님.
4. 운영에서는 현장 안전 절차에 따라 C/A 연계 검증이 별도로 필요하다. 시험 성공으로 물리 안전을 인증하지 않는다.

## 알려진 계약 한계

event는 현재 영속 안전상태 조회 계약이 아니다. HMI 시작 이전에 발생한 STOP의 재수신은 보장되지 않는다.
따라서 미관측을 정상이라고 표시하지 않는다. 새 주문의 최종 차단자는 C다.
상단 안전 복구 창은 열 수 있지만 STOP 미관측 상태에서는 요청 버튼이 비활성화된다.
다른 HMI의 복구 이벤트는 기록하되 상관관계가 없는 성공으로 현재 로컬 정지를 해제하지 않는다.
필요하면 현장 상태 재확인 후 이 HMI에서 새 요청을 보낸다. 상태/이벤트 전달 보장 개선은 A/C 계약 협의 대상이다.

실행 중 서버 종료의 기존 rclpy 중복 shutdown 문제는 이 안전 복구 패치와 별도다.
