# D HMI·기록 현행 상태 (갱신: 2026-09-23 16:10, D 본인 — 팀장 초안 보완)

**담당**: aszx4880-star

> 세션 시작 때 이 파일을 읽는다. 값을 쓰기 전에 근거 링크의 원본을 직접 연다.

## 지금 유효한 값
| 항목 | 값 | 근거 |
|---|---|---|
| `DispenseResult.verdict` 이름표 | `VERDICTS = {0 OK, 1 UNDER, 2 OVER, 3 INVALID}` (`core/db.py:15`). DB 기록(`db.item`)과 실시간 스냅샷(`hmi_web_node.py:339`)이 **같은 표**를 쓴다 | PR #240 (계약 v1.8, #241 과 짝) |
| 판정 배지 `hmi.js verdict()` | good: OK·DONE·AUTO_RECOVERED / bad: ERROR·DISCARDED·**INVALID** / warn: OVER·UNDER·PENDING·**DONE_UNMEASURED** / 그 외 info | PR #240·#261 |
| 진행 스트립 INVALID 원료 | 「투입량 미확인」(hold) — 「완료」 아님 | PR #245 (#242 해소) |
| 배치 결과 `DONE_UNMEASURED` 기록 | 종료 시 `db.has_unmeasured` = `events.code='BATCH_UNMEASURED'` **또는** `items.verdict='INVALID'` 이면 DONE_UNMEASURED. DB 로 판정하므로 record_node 재시작에도 같다 | PR #261, SOT D-32 |
| 이벤트 순서 역전 흡수 | `BATCH_UNMEASURED`·INVALID 결과가 종료 **뒤** 오면 `reconcile_unmeasured` 가 DONE → DONE_UNMEASURED UPDATE(`finished_at` 유지). DISCARDED·ERROR 는 안 건드림 | PR #261, C 발행 PR #257 |
| QA 폐기 대사 | `reconcile_discard` 는 `result IN ('DONE','DONE_UNMEASURED')` | PR #261 |
| KPI | `batch_success_pct` = **DONE 만**(카드 이름 「계량 검증 완료율」), `unmeasured_done`·`unmeasured_done_pct` = 미측정 승인 완료, `run_complete_pct` = 둘의 합(완주율). 카드 수 5개 불변 | PR #261, SOT D-32 (5) |
| 결과 필터 | 전체·정상 종료·**미측정 승인 완료**·폐기·오류·진행 중 | `templates/index.html` `#reportResult`, PR #261 |
| `hmi.js steps` 맵 | FSM 상태와 1:1 (CLEANUP 포함) | PR #232 |
| 재고 차감 `session_inventory.observe` | OK·OVER 만 차감(UNDER·INVALID 제외) — 「재고를 모른다」 표현은 별건 | `core/session_inventory.py:39`, #108 논의 |
| 레이아웃 | `body` 가 `display:flex; height:100dvh; overflow:hidden` — **스크롤은 `main` 안에서만** 일어난다. 기록·통계 탭은 grid 4행 `1fr` 로 카드 3개 하단 정렬 | PR #229 (`hmi.css:1`·`:73`) |
| 시연 기기 | 로봇 PC 1대 + **휴대폰 브라우저 HMI 기본**, 같은 네트워크(`http://<로봇 PC Wi-Fi IP>:5000`) | PR #259, `docs/demo_run_procedure.md` T0·G6 |
| ROS 도메인 | 본운영 70, 격리 시험 88 | `docs/demo_run_procedure.md:38` |

## 열린 과제 (이슈 번호)
- **G6 휴대폰 리허설** — `body overflow:hidden`(#229) 이 모바일(≤ 760 px)에도 걸린다. 헤더 줄바꿈으로 `main` 이 좁아질 수 있음. 휴대폰에서 스크롤·승인/폐기·안전 복구 버튼을 실제로 눌러 볼 것 (PR #259 G6, **미검증**).
- **#261 실물/가상 통합 확인** — `record_node` 가 `BATCH_UNMEASURED` 를 받아 DB 에 `DONE_UNMEASURED` 를 남기는지, KPI 카드·필터 표시. 샌드박스엔 ROS·flask 가 없어 **core 단위 시험만 통과**(test_db +5).
- `docs/interfaces.md:188` KPI 이름이 아직 「배치 성공률」 — 계약 문서라 조장 몫(#261 본문에 적음).
- `gmp_hmi/tools/test_frontend_render.cjs`(playwright 없음)·`tools/test_safety_popup.cjs`(appendChild TypeError) 는 main 에서도 실패 — 살릴지 지울지.

## 알려진 함정
- HMI 는 판정 정수를 직접 받지 않는다 — `hmi_web_node:339` 가 `VERDICTS` 로 바꾼 문자열을 받는다. 새 열거값은 `db.py:15` 가 본체.
- 배치 결과·판정을 정확 일치(`='DONE'`)로 거르는 곳이 새 값을 조용히 흘린다. 새 값 추가 시 `grep -rn "'DONE'" ros2_ws/src/gmp_hmi`.
- `RunBatch.result` 는 **DB 에 안 닿는다** — record_node 는 배치 결과를 `CellState` 로 만든다. C 가 결과 문자열을 늘리면 이벤트가 같이 와야 기록된다(D-32).
- **배치가 즉시 ERROR/CANCELLED 면 먼저 도메인 충돌을 의심** — 9/22 공유 `ROS_DOMAIN_ID=70` 에서 다른 팀원 노드가 우리 배치를 취소했다(`BATCH_CANCEL_REQUEST` 감사 기록 없이 `BATCH_CANCELLED`). 격리 도메인에서 재현되면 우리 문제.
- `ros2 node list`·`service list` 가 옛 목록이면 `ros2 daemon stop && ros2 daemon start`.
- `static/*` 를 고친 뒤 화면이 그대로면 `colcon build --symlink-install --packages-select gmp_hmi` + HMI 재시작, `curl http://127.0.0.1:5000/static/hmi.css | grep <바꾼 문자열>` 로 서빙 확인.
- gmp_hmi 와 gmp_process 시험을 같은 pytest 실행에 넣지 않는다 — 노드 경합으로 process 시험이 깨진다(C CURRENT).

## 철회 이력 (최근 것 위)
- 2026-09-23 ~~KPI 완료율에서 DONE_UNMEASURED 「제외」만~~ → 두 지표로 분리(계량 검증 완료율 / 미측정 승인 완료, 완주율은 합). SOT D-32, PR #261.
- 2026-09-23 ~~DONE_UNMEASURED 는 VERIFY 미측정일 때만~~ → 원료 하나라도 미측정이어도. SOT D-32, PR #257·#261.
- 2026-09-23 ~~#242 진행 스트립 INVALID 「완료」 표시(열린 과제)~~ → PR #245 로 해소.
- 2026-09-23 ~~#213 5번 2단계(계약 확장: UNDETERMINED·unmeasured_cycles·BatchResult)~~ → 접음. weights.valid·scoop_cycles.outcome/valid·RunBatch.result 로 충분(D·C·문서 관리 합의).
- 2026-09-22 ~~배치 즉시 ERROR 원인 = 스쿠핑 `calibrated:false` 게이트~~ → 공유 도메인 70 에서 외부 취소. 이벤트 로그가 `BATCH_CANCELLED` 였고 격리 도메인(91) 재현에서 정상.
