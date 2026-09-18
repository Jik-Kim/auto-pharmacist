# V4 재고·높이·개별 보충 시험

## 적용 범위

`gmp_hmi` 내부 `/hmi_test` 전용 기능이다. 공용 `gmp_interfaces`와 실제 C의 `/cell` 계약은 변경하지 않는다. 시험 노드는 로봇·힘 센서·실제 도징을 호출하지 않는다. 실제 `/cell` 주문은 시험 재고로 차단하지 않고 C의 주문 서비스에 전달한다. 재고·높이·보충 차단은 시험 모드에만 적용한다.

## 원료·레시피

| 파일 | A | B | C | 합계 |
|---|---:|---:|---:|---:|
| recipe-01.yaml | 40 g | 40 g | 40 g | 120 g |
| recipe-02.yaml | 80 g | 40 g | 항목 없음 | 120 g |
| recipe-03.yaml | 40 g | 40 g | 80 g | 160 g |

기존 `RecipeItem`의 `material_id`, `target_g`, `tol_pct`만 사용한다. 대문자 A/B/C, 배열 순서가 투입 순서다. 원료 위치 `material_1/2/3` 및 스쿱 위치 `scoop_1/2/3`는 `stations.yaml`이 관리한다. 레시피에 좌표를 복사하지 않는다.

허용 오차는 기존 5%다. 40 g의 ±2 g, 80 g의 ±4 g 성능은 실물 검증 전이다. SOT D-08의 분해능 확인 조건은 남는다. 평균 스쿠핑 40 g·최대 3회라는 기존 공정 설정은 변경하지 않았다. 시험 노드는 80 g을 40 g 두 시도 기록으로 생성하지만 동작·시간·무게 모두 가상이다.

## 재고·차단

- A/B/C 만충 기준 각 1,000 g. 기본 시험 시작값도 1,000 g이다. 실제 투입 1,050 g을 가정하면 표시 장부 밖 50 g은 여유분이며 HMI가 측정한 값이 아니다.
- 주문 시 선택 레시피 전체 필요량 예약, 분주 시 소비 차감, 종료 시 미사용 예약 해제. 폐기해도 소비한 원료는 복원하지 않는다.
- g 부족은 선택 레시피 원료만 검사한다. recipe-02는 C 0 g이어도 C 높이 부족 잠금이 없으면 가능하다.
- 높이는 g에서 계산하지 않는다. `<20%` 신호는 해당 원료 부족 잠금을 설정한다. 정확히 20%는 새 부족 신호가 아니다.
- 하나라도 높이 부족이면 새 주문·다음 공정 단계 진행·QA 판정·EXIT를 막는다. 높은 값 재수신이나 팝업 닫기로 잠금이 해제되지 않는다.
- 재고 미수신·지연·형식 오류는 HMI 주문·보충 차단. 공정도 직접 ROS 주문에서 부족을 재검사한다.
- 시험 재고는 메모리이며 재시작하면 초기화된다. 실제 재고 복구 정책 구현이 아니다.

## 보충 흐름

대기 또는 최종 DONE에서는 개별 보충 완료를 누른다. 진행 중이면 부족으로 멈췄더라도 ENTER 요청 후 진입 허가·PAUSED를 확인해야 한다. 해당 원료를 만충 보충했음을 확인 대화상자에서 명시적으로 확인한다. 시험 화면에서는 가상 장부만 바뀐다.

선택 원료만 1,000 g·100%로 갱신하고 그 원료의 잠금만 해제한다. 다른 원료와 배치 예약은 유지한다. g 막대 100%는 센서 높이값이 아니므로 높이는 `보충 확인 · 재측정 대기`로 표시한다. 진행 중이었다면 모든 부족을 해소한 후 **EXIT를 별도로 눌러 재개**한다.

## 시험 전용 연결

| 경로 | 타입/요청 | 의미 |
|---|---|---|
| `/hmi_test/test_inventory` | `std_msgs/msg/String`, JSON `hmi_test.inventory.v2` | 공정→HMI, transient-local depth 1 |
| `/hmi_test/test_height` | `std_msgs/msg/String` | JSON `{material_id:"A",height_pct:19.9}` 시험 신호 |
| `/hmi_test/test_refill_A` · `_B` · `_C` | `std_srvs/srv/Trigger` | 해당 원료 만충. 공정에서 허용 상태 재검사 |
| `POST /test/refill` | `{material_id:"A",confirmed_full:true}` | operator/admin·세션·CSRF 필요 |
| `POST /test/height` | `{material_id:"A",height_pct:19.9}` | operator/admin 시험 신호 발행 |

재고 JSON: `instance_id`, `revision`, `batch_id`, `can_refill`, `height_low_pct`, `blocked_materials`, `items`. 원료 항목: `capacity_g`, `remaining_g`, `reserved_g`, `available_g`, `height_pct`, `height_low_latched`, `height_status`. HMI는 freshness·서비스 준비 상태·표시 퍼센트를 추가한다.

보충 서비스 응답만으로 장부를 낙관적으로 바꾸지 않고 후속 공정 스냅샷을 기다린다. `HMI_TEST_REFILL`에는 로그인 계정·원료 ID·수락 결과, `TEST_REFILL_COMPLETE`에는 공정 완료를 남긴다. 높이 입력도 감사 기록에 남는다. DB 단일 기록자는 계속 `record_node`다.

## 실제 연동 TODO

TODO([C/A/D]): 재고 권위자, 높이 단위·유효성·시퀀스, 부족 잠금, 안전 진입 허가, 개별 보충 요청/수락, 보충 전 지연 신호를 구분하는 세대·시퀀스, 재시작 복구를 합의한다. 실제 C가 주문과 다음 스킬 진행을 모두 막아야 한다. 시험 JSON·Trigger를 공식 실제 인터페이스로 간주하지 않는다. 공용 계약 변경은 팀 검토 후 메시지와 문서에 함께 반영한다.
