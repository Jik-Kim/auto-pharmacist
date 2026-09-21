# HMI 추가 제어 · 2026-09-21

기준 main: 942d422. D 패키지만 변경하며 메인 4열 배치는 유지한다.

## 구현

- 주문 카드: 확인 후 `POST /batch/cancel` → 이 HMI가 수락받은 RunBatch Goal의 표준 cancel.
  operator/admin + CSRF + 신선한 동일 배치 상태 + 소유 Goal을 확인한다.
  취소 응답이 목표 UUID와 일치해야 접수로 표시하며, 실제 정지 완료로 표시하지 않는다.
  늦은 Goal 수락도 유지하고 무응답 주문의 중복 전송을 막는다. 결과 전에 반복 취소를 막는다.
  재기동으로 Goal handle을 잃으면 다른 배치까지 취소하는 우회 호출을 하지 않는다.
- 계량 카드: **용기 최종 총량** 목표와 허용범위. 합계 target, 허용폭 합계(target×tol/100).
  수락된 레시피만 사용하며 메뉴의 선택 변경은 기준을 바꾸지 않는다.
  용기 필터·동일 관측 배치일 때만 선/밴드를 그린다. 스쿱/미상/전체에는 표시하지 않는다.
  WeightReading 자체에는 batch_id가 없다. HTTP의 observed_batch_id는 최근 신선한 CellState 기반
  관측 문맥이며 공정이 제공한 ID가 아니다. 이전 배치보다 오래된 stamp는 문맥 미상으로 남긴다.
  중간 용기 계량의 합격 판정에 사용하지 않으며 C의 최종 판정이 권위자다.
- 단일 기록자 record_node: 상태 전이 관측 checkpoint와 수락 레시피를 추가 저장한다.
  레시피는 기존 HMI_* 감사 이벤트 형식의 HMI_ORDER_CONTEXT로 전달한다.
  `/restart-state`는 미완료 배치·마지막 단계/원료 순번·대상별 마지막 계량(tare 포함)을 읽는다.
  이것은 공정 내부 FSM·스킬 실행 상태를 복원하는 체크포인트가 아니다.
- 재기동 후 같은 배치의 저장된 수락 레시피를 조회해서 표시한다. 자동 주문/재개 없음.
- 상세: 미제공 장치·비활성 수동 제어 및 미수집 wrench 표 숨김.
  Modbus의 grip 비트는 파지 감지로, 다른 백엔드는 추정으로 표시한다.

## 아직 연결할 수 없는 기능 (구현 완료로 보고하지 않음)

| 요청 | 현재 Git 근거 | 다음 담당 작업 |
|---|---|---|
| 운영 RunBatch | C process_node는 SubmitOrder 서비스만 구현. HMI 시험 노드만 RunBatch 서버 있음 | C: Action 서버/Feedback/Result/cancel 및 종료 정책 구현 |
| 운영 원료 잔량·보충 | 운영 메시지·서비스 없음. test_inventory/test_refill_*는 /hmi_test 전용 | B/C: 원료별량·단위·revision·신선도·보충 확인 권한/응답 계약 확정 |
| 회수 확인 | HMI_COLLECTION_CONFIRMED는 감사 기록뿐. C 카운터 수신/초기화 없음 | C: 대상 용량·회수 명령·중복 요청·초기화 결과 계약 |
| 공정 재기동 이어하기 | C resume 서비스/저장 FSM 복원 계약 없음 | C: 재개 가능 상태·검증·응답·금지 조건 확정. 안전정지 ERROR 배치 제외 |
| 운영 레시피 | 운영 demo_batch.yaml 삭제. 시험 3종만 존재 | 팀: 실제 목표량/허용오차 확정 후 운영 YAML 등록 |

HMI의 `integration`과 미연결 문구는 이 상태를 명시한다. 없는 ROS 엔드포인트를 만들거나
시험 서비스를 운영 네임스페이스에 붙이지 않는다. 회수 버튼은 감사 기록 기능을 그대로 유지한다.
`todo.md`와 `issues.md`는 조장 관리이므로 수정하지 않는다.

## 검증

- HMI pytest 109개 통과(ROS 스텁 포함): 취소 권한/CSRF/actor, 늦은 수락, 취소 중복/UUID,
  완료 후 늦은 취소 응답, 재기동 Goal 부재, 목표 계산, 배치 전환, 저장/조회·ERROR 제외.
- 실제 Chromium + 모의 HTTP: 4열 유지, 취소 확인창과 전송값, 목표선 조건, 기록 조회,
  Modbus 문구, 390px 가로 넘침 없음, 미처리 JS 오류 없음.
- 기존 안전복구 팝업 DOM 검사 통과.
- 개발환경에 ROS가 없어 새 DDS 시험/실물 시험은 미수행.

기동된 **시험** 서버에서 추가 검증:

```bash
# 시험 서버 터미널: 사용자 기존 시험 비밀번호 사용, item_duration_s:=10.0 권장
ros2 launch gmp_hmi hmi_comm_test.launch.py item_duration_s:=10.0

# 다른 터미널: ROS 환경을 불러오고 ROS_DOMAIN_ID=88 설정
# 비밀번호는 사용자 입력, 계정을 생성하거나 변경하지 않음
export GMP_HMI_ADMIN_USER=admin
read -rsp '시험 웹 로그인 비밀번호: ' GMP_HMI_ADMIN_PASSWORD
printf '\n'
export GMP_HMI_ADMIN_PASSWORD
python3 ros2_ws/src/gmp_hmi/tools/verify_hmi_controls_ros.py
```

검증기는 /hmi_test·시험 재고를 확인한 뒤 시험 recipe-01 한 건을 주문하고 취소한다.
자동 전체 회귀 시험과 동시에 실행하지 않는다. C 운영 서버나 로봇 검증은 아니다.
