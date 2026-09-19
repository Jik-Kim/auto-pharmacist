# V4 인터페이스 대조 결과

기준: main `8c84d9e` 및 PR #20 리뷰. 공유 인터페이스와 C 공정 코드는 변경하지 않는다.

| 항목 | HMI 적용 |
|---|---|
| 운영 레시피 | 기본 경로는 `gmp_bringup/params/recipes`, C의 `recipe.load()` 사용 |
| 시험 레시피 | `config/test_recipes/v4`의 3종. 40 g 단위는 시험용이며 실물 정밀도 검증 결과가 아님 |
| 주문 | `SubmitOrder`에 전달하고 C의 수락 여부 사용. 운영 주문에 시험 재고 조건을 적용하지 않음 |
| QA | `QaDecision.Request(deviation_id, decision, operator_id)`, batch_id 전송하지 않음 |
| 인터락 | Request의 ENTER/EXIT 사용. granted 응답 후 PAUSED 또는 QA 대기 DEVIATION에서 허가 표시. 배치 변경·통신 만료·EXIT 시 해제 |
| 종료 | C의 DONE/DONE 및 DONE/DISCARDED를 종료로 기록. QA 폐기 판정만으로 종료하지 않음 |
| 강제 개입 | `Deviation.FORCED=4` 판정 매핑 유지 |
| 계량·스쿱 | WeightReading의 subject/samples 및 ScoopCycle 기록 유지 |

시험 레시피는 A40/B40/C40, A80/B40, A40/B40/C80 g이며 허용 오차는 5%다.
운영 레시피와 실제 계량 분해능은 공통 설정 및 팀의 G1 검증 결과를 따른다.

원료 위치는 공통 stations.yaml에서 관리하며 HMI 레시피에 좌표를 복사하지 않는다.
재고·높이·개별 보충 토픽과 서비스는 /hmi_test 전용이다. 운영 재고의 공식 계약은 별도 합의가 필요하다.

단위 테스트와 시험 공정의 통과는 실제 C 공정·로봇 검증을 대신하지 않는다.
