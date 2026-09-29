# V4 인터페이스 대조 결과

기준: main `8c84d9e` 및 PR #20 리뷰. 공유 인터페이스와 C 공정 코드는 변경하지 않는다.

| 항목 | HMI 적용 |
|---|---|
| 운영 레시피 | 기본 경로는 `gmp_bringup/params/recipes`, C의 `recipe.load()` 사용 |
| 시험 레시피 | `config/test_recipes/v4`의 3종. 운영 레시피 사본(#306 원료별 1회량)이며 실물 정밀도 검증 결과가 아님 |
| 주문 | `RunBatch.Goal(recipe)`로 전달하고 Goal 수락 여부를 사용. Feedback의 `state`·`last_result`와 최종 Result의 `success`·`items_done`·`deviations`·`result`·`message`를 계약 그대로 수신. 운영 주문에 시험 재고 조건을 적용하지 않음 |
| QA | `QaDecision.Request(deviation_id, decision, operator_id)`, batch_id 전송하지 않음 |
| 인터락 | Request의 ENTER/EXIT 사용. granted 응답 후 PAUSED 또는 QA 대기 DEVIATION에서 허가 표시. 배치 변경·통신 만료·EXIT 시 해제 |
| 종료 | C의 DONE/DONE 및 DONE/DISCARDED를 종료로 기록. QA 폐기 판정만으로 종료하지 않음. 폐기 반송 동안 C 는 RUNNING 이고 DONE/DISCARDED 는 넛지 뒤 한 번만 낸다(#304, main `4e2c963`) — record_node 는 물리 종료 뒤에 배치를 닫는다. 시험 공정도 같다 |
| 세트 끝 | `NUDGE_WAIT`(mode PAUSED, note `NUDGE_WAIT — …`). NUDGE 뒤 DONE, 그때 RunBatch Result. HMI 는 `pause_reason=SET_COMPLETE` 로 표시. **계약 v1.9(9/28, C `feat/nudge-wait-order-queue` — 머지 전):** 세트 끝 구간(step FINISH·DISCARDED·NUDGE_WAIT, mode RUNNING·PAUSED)의 RunBatch 주문은 1건 예약 — HMI 는 예약 goal 을 진행 배치와 따로 추적(`batch_control.queued`)하고, 상태의 batch_id 가 예약 주문으로 바뀌면 진행 칸으로 옮긴다. 예약 취소는 별도 버튼. 이벤트 `ORDER_QUEUED`·`ORDER_DROPPED` 는 그 세트 배치 기록에 남는다 |
| 강제 개입 | `Deviation.FORCED=4` 판정 매핑 유지 |
| 계량·스쿱 | WeightReading의 subject/samples 및 ScoopCycle 기록 유지 |

시험 레시피는 A69/B69/C69 g, A138/B69 g, A69/B69/C138 g이며 모든 원료의 허용오차는 ±15%로 운영 레시피와 같다. 원료별 스쿱 1회량 설정은 A/B/C 모두 69 g이다.
운영 레시피와 실제 계량 분해능은 공통 설정 및 팀의 G1 검증 결과를 따른다.

원료 위치는 공통 stations.yaml에서 관리하며 HMI 레시피에 좌표를 복사하지 않는다.
재고·높이·개별 보충 토픽과 서비스는 /hmi_test 전용이다. 운영 재고의 공식 계약은 별도 합의가 필요하다.

단위 테스트와 시험 공정의 통과는 실제 C 공정·로봇 검증을 대신하지 않는다.
