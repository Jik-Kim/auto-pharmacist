# D 이슈 대조 · 2026-09-21

기준: main 858f59e (RunBatch PR #163 병합). UI 배치 변경 없음.
할 일 정본은 GitHub Issues이며 이 문서는 해당 시점의 코드 근거다.

| 이슈 | 구현 근거 | 남은 확인 |
|---|---|---|
| #133 배치 중단 | hmi_web_node.py cancel_batch, test_batch_controls.py, C RunBatch 서버 | 실제 C + HMI HTTP 취소·감사 기록 통합. 실물 정지는 A 별도 |
| #134 목표선·허용범위 | measurement_context.py target_band, hmi.js visibleTargetBand/draw, test_batch_controls.py | 실제 C 데이터 문맥으로 브라우저 표시 확인. '미구현' 제목은 현재 코드와 다름 |
| #135 재기동 | record_node 상태 관측·수락 레시피 저장, /restart-state 조회 | 이것은 스킬 실행/FSM 복원 체크포인트가 아님. C 재개 계약 필요 |
| #136 NUDGE/REFILL | 기존 note 원문 표시·이벤트 조회 + 이번 표시용 사유 매핑 | 실제 C→HMI→브라우저·타임라인 검증 필요. 이 변경만으로 닫지 않음 |
| #137 잔량·보충 | 시험 재고 UI·세션 추정 구현 | B/C 운영 재고 계약·실물 높이 보정 |
| #161 레시피 | 시험 YAML 3종은 존재, 운영 경로에는 없음 | 운영 목표·허용폭·시도 상한 합의 후 운영 경로 등록 |
| #128 통합 | 기존 시험 공정 검증 + C RunBatch DDS | HMI·C·A 통합 실물 검증 |
| #139 영상/PPT | 코드 외 산출물 | 촬영·조장 협업 |

## #136 이번 변경

CellState에는 pause_reason 필드가 없다. HMI가 mode/step/note로 표시용
pause_reason을 만들어 HTTP 스냅샷에 넣는다. ROS 메시지와 제어 조건은 변경하지 않는다.

- NUDGE_WAIT는 SET_COMPLETE로 분리하여 접촉 정지로 잘못 표시하지 않는다.
- NUDGE/REFILL/HEIGHT_LOW는 note 첫 토큰이 명확할 때만 분류한다.
- 인터락 ENTER (REFILL)는 원료 부족으로 단정하지 않고 INTERLOCK으로 분류한다.
- PAUSED만 수신하거나 note가 비어 있으면 원인을 추정하지 않는다.
- 원문 note를 항상 보존하며 RUNNING/ERROR에서 과거 사유를 재사용하지 않는다.
- 현재 C의 REFILL 경로는 빈 note를 보낼 수 있으므로 이때 보충 사유는 미확인이다.
  정확한 구조화 사유 발행은 C와 별도 합의가 필요하며 과거 deviation으로 추정하지 않는다.
- 이 표시값은 진입 허가·안전 복구·주문 수락 조건으로 사용하지 않는다.

검증: HMI 수신 매핑 회귀 9사례 추가. 테스트는 ROS 스텁 기반이며 DDS/실물 검증이 아니다.
