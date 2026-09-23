# D HMI·기록 현행 상태 (갱신: 2026-09-23 20:10, D 본인 — 규칙 5: 남의 파트 수치를 이름으로)

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
| `hmi.js steps` 맵 | FSM 상태와 1:1 (CLEANUP 포함). 시험 공정·데모가 내는 단계도 이 맵 안에서만 쓴다(`test_v3_frontend_contract.py` 대조) | PR #232, 통합 전 정리 PR |
| 계약 이름표 대조 | `db.py` `KINDS`·`DECISIONS`·`VERDICTS`·`LEVELS`, `hmi.js` `outcomeNames` 를 `gmp_interfaces/msg/*.msg` 와 시험으로 대조(`test_db.py`). 통신 시험 launch 의 시나리오 목록도 시험 노드와 대조 | 통합 전 정리 PR |
| 프런트 시험 2 종 | `tools/test_safety_popup.cjs`(DOM 스텁·안전 복구 팝업 가드)·`tools/test_frontend_render.cjs`(playwright) **둘 다 PASS**. 후자는 `NODE_PATH=/opt/node22/lib/node_modules HMI_TEST_CHROMIUM=/opt/pw-browsers/chromium` 필요 | 9/23 실행, PR #267 |
| 재고 차감 `session_inventory.observe` | OK·OVER 만 차감(UNDER·INVALID 제외) — 「재고를 모른다」 표현은 별건 | `core/session_inventory.py:39`, #108 논의 |
| 레이아웃 | `body` 가 `display:flex; height:100dvh; overflow:hidden` — **스크롤은 `main` 안에서만** 일어난다. 기록·통계 탭은 grid 4행 `1fr` 로 카드 3개 하단 정렬 | PR #229 (`hmi.css:1`·`:73`) |
| 시연 기기 | 로봇 PC 1대 + **휴대폰 브라우저 HMI 기본**, 같은 네트워크(`http://<로봇 PC Wi-Fi IP>:5000`) | PR #259, `docs/demo_run_procedure.md` T0·G6 |
| ROS 도메인 | 본운영 70, 격리 시험 88 | `docs/demo_run_procedure.md:38` |
| 통신 검증 기준선 | `tools/verify_ros_http.py` **20 항목 PASS** (실제 DDS, `/hmi_test`, 배치 9 건) — D-33 레시피 기준(값은 `gmp_bringup/params/recipes` 참조). 시작 재고 `test_initial_g:='[170.0,1000.0,1000.0]'`. launch 와 검증기의 `GMP_HMI_ADMIN_PASSWORD` 가 **같아야** 한다(다르면 로그인 401) | 9/23 19:50 실행, PR #276 |
| 시험 시나리오 | `normal`·`overfill`(과다 배율 `OVERFILL_RATIO` 1.15 — 레시피 `tol_pct` 를 확실히 넘어야 해서 1.10 에서 올림)·`batch_out_of_spec`·`wrong_tool`·`weigh_invalid`·`material_empty` | `gmp_hmi/nodes/hmi_test_process.py` |
| 시험 레시피 사본 | `config/test_recipes/v4` = 운영 `gmp_bringup/params/recipes` 와 **같다**(값은 운영 파일 참조). `test_v4_recipes.py` 가 운영과 대조해 어긋나면 실패한다. 통신 시험 launch 가 시험 노드에 `test_scoop_nominal_g` 85 g 을 넘긴다 | SOT D-33, PR #276 |

## 열린 과제 (이슈 번호)
- **G6 휴대폰 리허설** — `body overflow:hidden`(#229) 이 모바일(≤ 760 px)에도 걸린다. 헤더 줄바꿈으로 `main` 이 좁아질 수 있음. 휴대폰에서 스크롤·승인/폐기·안전 복구 버튼을 실제로 눌러 볼 것 (PR #259 G6, **미검증**).
- **#261 실제 `/cell` 확인** — `/hmi_test` DDS 경로는 **9/23 검증 완료**(아래 통신 검증 20 항목). 남은 것은 **실제 C 공정·로봇**에서 같은 경로가 도는지다. 가상 `/cell` 은 파지에서 막히므로(아래 함정) 실물이어야 한다.
- `docs/interfaces.md:188` KPI 이름이 아직 「배치 성공률」 — 계약 문서라 조장 몫(#261 본문에 적음).
- ~~`tools/test_safety_popup.cjs` 살릴지 지울지 D 판단~~ → **살린다**. 실패 원인은 팝업 로직이 아니라 스텁이었다(9/22 `f349fe2` 가 `hmi.js:151` 에 `appendChild` 추가 → 스텁에 없어 `TypeError`). 스텁 한 줄로 PASS, 가드 17 건 유효(고의로 `robot_state` 허용 목록을 넓히면 실패 확인). `tools/test_frontend_render.cjs` 는 **환경 문제였다** — playwright·Chromium 이 있는 샌드박스에서 `NODE_PATH=/opt/node22/lib/node_modules HMI_TEST_CHROMIUM=/opt/pw-browsers/chromium node tools/test_frontend_render.cjs` 로 9/23 PASS. 심볼릭 링크 불필요.

## 알려진 함정
- HMI 는 판정 정수를 직접 받지 않는다 — `hmi_web_node:339` 가 `VERDICTS` 로 바꾼 문자열을 받는다. 새 열거값은 `db.py:15` 가 본체.
- 배치 결과·판정을 정확 일치(`='DONE'`)로 거르는 곳이 새 값을 조용히 흘린다. 새 값 추가 시 `grep -rn "'DONE'" ros2_ws/src/gmp_hmi`.
- `RunBatch.result` 는 **DB 에 안 닿는다** — record_node 는 배치 결과를 `CellState` 로 만든다. C 가 결과 문자열을 늘리면 이벤트가 같이 와야 기록된다(D-32).
- **배치가 즉시 ERROR/CANCELLED 면 먼저 도메인 충돌을 의심** — 9/22 공유 `ROS_DOMAIN_ID=70` 에서 다른 팀원 노드가 우리 배치를 취소했다(`BATCH_CANCEL_REQUEST` 감사 기록 없이 `BATCH_CANCELLED`). 격리 도메인에서 재현되면 우리 문제.
- `ros2 node list`·`service list` 가 옛 목록이면 `ros2 daemon stop && ros2 daemon start`.
- `static/*` 를 고친 뒤 화면이 그대로면 `colcon build --symlink-install --packages-select gmp_hmi` + HMI 재시작, `curl http://127.0.0.1:5000/static/hmi.css | grep <바꾼 문자열>` 로 서빙 확인.
- **ROS 없는 샌드박스에서 `gmp_hmi` pytest 는 `PYTHONPATH` 없이 돌리면 거짓 기준선이 된다** — `gmp_process` import 실패로 `test_v4_process.py` 등 69 건이 ERROR 로 빠지고 「55 passed」만 보인다. 9/23 #266 에서 이것을 「회귀 없음」으로 적었다가 조장 검토에서 실패 1 건이 드러났다. `PYTHONPATH=../gmp_process:.:../gmp_dosing python3 -m pytest -q test` 로 **131 passed** 가 기준(PR #276 사본↔운영 대조 1 건, 통합 전 정리 PR 계약 대조 4 건 추가).
- gmp_hmi 와 gmp_process 시험을 같은 pytest 실행에 넣지 않는다 — 노드 경합으로 process 시험이 깨진다(C CURRENT).
- **가상 모드로는 `PICK_CONTAINER` 를 통과 못 한다** — 파지 판정이 `grip = w > width_mm + grip_margin_mm`(`gmp_skills/adapters/rg2_gripper.py:246`)인데 `virtual` 백엔드(`:175`)는 명령한 관절각으로 그대로 이동해 `w ≈ width_mm` 이라 `grip_margin_mm`(`common.yaml`)을 못 넘는다. 9/23 실행에서 `GRIP_FAIL` ×4 → ERROR. **가상은 이동·상태 전이·기록 확인용이고 전체 사이클 완주 검증에는 못 쓴다.**
- **가상 모드 `NUDGE_WAIT` 은 안 풀린다** — `skill_node.py:126` 이 `scale.simulated` 면 NUDGE 감지를 끄는데 `process_node` 쪽 `safety.nudge_enabled`(`common.yaml`)는 이 조건을 모르고 `_await` 에 타임아웃이 없다(`process_node.py:315-318`). 우회: `/cell/event` 에 `code='NUDGE'` 를 한 번 발행하면 사람이 건드린 것과 같은 경로로 풀린다(파라미터 변경 불필요).

## 철회 이력 (최근 것 위)
- 2026-09-23 ~~KPI 완료율에서 DONE_UNMEASURED 「제외」만~~ → 두 지표로 분리(계량 검증 완료율 / 미측정 승인 완료, 완주율은 합). SOT D-32, PR #261.
- 2026-09-23 ~~DONE_UNMEASURED 는 VERIFY 미측정일 때만~~ → 원료 하나라도 미측정이어도. SOT D-32, PR #257·#261.
- 2026-09-23 ~~#242 진행 스트립 INVALID 「완료」 표시(열린 과제)~~ → PR #245 로 해소.
- 2026-09-23 ~~#213 5번 2단계(계약 확장: UNDETERMINED·unmeasured_cycles·BatchResult)~~ → 접음. weights.valid·scoop_cycles.outcome/valid·RunBatch.result 로 충분(D·C·문서 관리 합의).
- 2026-09-22 ~~배치 즉시 ERROR 원인 = 스쿠핑 `calibrated:false` 게이트~~ → 공유 도메인 70 에서 외부 취소. 이벤트 로그가 `BATCH_CANCELLED` 였고 격리 도메인(91) 재현에서 정상.
