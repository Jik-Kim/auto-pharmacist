# D HMI·기록 현행 상태 (갱신: 2026-09-23, 문서 관리 팀장 초안 — D 가 보완)

**담당**: aszx4880-star

> 세션 시작 때 이 파일을 읽는다. 값을 쓰기 전에 근거 링크의 원본을 직접 연다.

## 지금 유효한 값
| 항목 | 값 | 근거 |
|---|---|---|
| `DispenseResult.verdict` 이름표 | `VERDICTS = {0 OK, 1 UNDER, 2 OVER, 3 INVALID}`; `hmi.js verdict()` INVALID → bad | PR #240 (계약 v1.8, #241 과 짝) |
| 배치 결과 새 값 | `RunBatch.result = 'DONE_UNMEASURED'` (미측정 QA 승인 배치) | PR #225 |
| KPI 완료율 | DONE_UNMEASURED **제외** | 9/23 조장 결정, #228 |
| `hmi.js steps` 맵 | FSM 상태와 1:1 (CLEANUP 포함) | PR #232 |
| 재고 차감 `session_inventory.observe` | OK·OVER 만 차감(UNDER·INVALID 제외) — 거동 무변경, 「재고를 모른다」 표현은 별건 | #108 논의 |

## 열린 과제 (이슈 번호)
- #228 (급) `db.py:106 reconcile_discard` 가 `result='DONE'` 만 봐서 DONE_UNMEASURED 배치는 QA 폐기해도 DISCARDED 로 안 바뀜 + KPI(:314)·보고서 필터·배지.
- #242 진행 스트립 `renderProgress` 가 INVALID 원료를 「완료」(초록)로 표시, '재계량' 가지 도달 불가.
- `tools/test_frontend_render.cjs`(playwright 없음)·`tools/test_safety_popup.cjs`(appendChild TypeError) 는 main 에서도 실패 — 살릴지 지울지.

## 알려진 함정
- HMI 는 판정 정수를 직접 받지 않는다 — `hmi_web_node:339` 가 `VERDICTS` 로 바꾼 문자열을 받는다. 새 열거값은 `db.py:15` 가 본체.
- 배치 결과·판정을 정확 일치(`='DONE'`)로 거르는 곳이 새 값을 조용히 흘린다. 새 값 추가 시 grep.

## 철회 이력 (최근 것 위)
- 2026-09-23 ~~#213 5번 2단계(계약 확장: UNDETERMINED·unmeasured_cycles·BatchResult)~~ → 접음. weights.valid·scoop_cycles.outcome/valid·RunBatch.result 로 충분(D·C·문서 관리 합의).
