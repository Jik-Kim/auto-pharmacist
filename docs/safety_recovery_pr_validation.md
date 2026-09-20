# 안전복구 연동 PR 검증 범위

현재 HMI 작업본(확정 UI 배치·RunBatch·단일 복구 요청 버튼)을 최신 main에 통합한다.
main의 v1.3 반환 표시명은 유지한다. PR의 핵심 변경은 복구 시작 STOP이 HMI의 요청을
삭제해 성공 결과를 잃던 결함과 C가 과거 성공 이벤트로 차단을 풀 수 있던 경로다.

계약 v1.6은 제안이며 A/C/D 리뷰 후 확정한다. A 직접 호출, 자동 재개, 자동 주문은 추가하지 않는다.
안전복구 성공은 기존 ERROR 배치를 재개하지 않는다. 현장 정리·명시적 새 주문이 필요하다.

## 자동 검사

```bash
cd ~/auto-pharmacist
source /opt/ros/jazzy/setup.bash
cd ros2_ws
colcon build --symlink-install --packages-up-to gmp_hmi gmp_skills
source install/setup.bash
python3 -m pytest -q src/gmp_hmi/test
python3 -m pytest -q src/gmp_skills/test/test_safety_recovery.py src/gmp_skills/test/test_skill_shutdown.py
python3 -m pytest -q src/gmp_process/test/test_safety_events.py
node src/gmp_hmi/tools/test_safety_popup.cjs
```

위 Python 단위 검사와 DOM 스텁 검사는 실물 또는 DDS 검증이 아니다.
ROS 환경에서는 C의 가짜 A 연동 시험도 별도로 실행한다:

```bash
python3 -m pytest -q src/gmp_process/test/test_process_node.py -k safety
ROS_DOMAIN_ID=88 RMW_IMPLEMENTATION=rmw_fastrtps_cpp python3 src/gmp_hmi/tools/verify_safety_recovery_ros.py
```

실제 A/C/D 검증에서는 정상 복구, 현장 조치 필요, 복구 중 새 알람, 지연·중복 결과,
ERROR 배치 유지, 새 주문 차단/해제, 감사 기록을 확인한다.
ROS/DSR가 없는 개발 환경에서는 이 두 명령 및 하드웨어 동작을 실행했다고 보고하지 않는다.

## 적용 조건

A/C/D를 같은 PR 버전으로 배포한다. 구버전 이벤트를 성공으로 추측해서 해제하지 않는다.
UI에서 이벤트를 관측하지 못한 상태나 서버 재시작 후 상태 복원은 별도 운영 확인이 필요하다.
이벤트 상관관계는 인증된 ROS 발행자임을 보장하는 보안 기능은 아니다.
