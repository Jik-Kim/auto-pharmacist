# Feature Todo — 파일·메서드 기준

각 줄 끝의 `마감 M/D` 는 실물 5일(9/17·18·21·22·23) 기준이다. 진행률은 아래 표가 체크박스를 세어 보여준다.
**이슈를 닫거나 상태를 바꿀 때는 여기 체크박스도 같이 본다.**

<!-- STATS:BEGIN -->

**전체 18/73 완료** (█████░░░░░░░░░░░░░░░)  ·  기준 09/18

| 파트 | 완료 | 진행 | 지난 마감 |
|---|---|---|---|
| gmp_interfaces [조장] | 2/6 | `███░░░░░░░` | **2** |
| gmp_skills [A 스킬] | 0/17 | `░░░░░░░░░░` | **6** |
| gmp_dosing [B 도징] | 0/7 | `░░░░░░░░░░` | **4** |
| gmp_process [C 공정] | 10/19 | `█████░░░░░` | — |
| gmp_hmi [D HMI·기록] | 3/15 | `██░░░░░░░░` | **1** |
| gmp_bringup [조장] | 3/9 | `███░░░░░░░` | **2** |

**마감이 지난 항목 15건**

- `9/17` gmp_interfaces — 계약 v1.2: Deviation.kind 에 WRONG_TOOL·VERIFY_MISMATCH 추가, WeighHeld Acti…
- `9/17` gmp_interfaces — G1 결과로 도징 단위 확정 → RecipeItem.tol_pct 기본값·레시피 yaml 갱신 (SOT D-08)
- `9/16` gmp_skills — adapters/dsr_arm.py: DR_init·set_tool/tcp·movej/movel·get_tool_force·re…
- `9/17` gmp_skills — adapters/rg2_gripper.py: modbus 백엔드 (/onrobot/sendCommand 폭 정수, /onrobo…
- `9/17` gmp_skills — nodes/skill_node.py: 워커 스레드 + 큐, MoveToStation·SetGripper·MeasureForce·…
- `9/17` gmp_skills — G1 외력 분해능 측정 (I-001) — 결과를 SOT D-08 에
- `9/17` gmp_skills — G2 그리퍼 modbus 실물 확인 (Q-02) — 안 되면 dio 백엔드 (Q-03 핀)
- `9/17` gmp_skills — G3 stations.yaml 9곳 티칭 — 판 좌표계(D-15) 우선, 안 되면 base posx
- `9/16` gmp_dosing — core/scale.py: WeightModel — 힘/작업물무게 → g, 영점(tare) 저장·적용, σ 계산, valid 판정
- `9/16` gmp_dosing — core/dosing.py: decide(target_g, actual_g, tol_pct, attempts, history) …
- `9/17` gmp_dosing — test/test_dosing.py: 경계값(±tol 정확히), 3회 재시도 후 TIMEOUT, OVER 즉시 일탈, 분해능 σ…
- `9/17` gmp_dosing — G1 σ 로 scale.min_resolvable_g 갱신, 30 g / 100 g 단위 확정 (조장과)
- `9/17` gmp_hmi — sudo apt install python3-flask 후 가상 모드에서 주문 → 상태 → QA 승인 → 이력 조회 한 바퀴
- `9/16` gmp_bringup — tools/env.sh 세 워크스페이스 source
- `9/17` gmp_bringup — 가상 모드에서 4노드 기동 확인, ros2 node list/rqt_graph 캡처

**오늘 마감 19건**

- gmp_interfaces — 계약 v1.2 — 실물 첫날 드러난 것 반영 (인터락 응답 지연 I-004, 그리퍼 백엔드 Q-02)
- gmp_interfaces — [D-22] 계약 v1.2 에 Deviation.kind BATCH_OUT_OF_SPEC 추가 — VERIFY 를 계측 신뢰성(…
- gmp_skills — [추가 7] nudge 감지: 워커 유휴 루프·계량 settle·붓기 대기에서 get_tool_force 100 ms 폴링, 임…
- gmp_skills — [추가 1] 폭 지문: 스쿱 손잡이 폭 15/18/21 mm 제작(테이프), stations.yaml/common.yaml 에 …
- gmp_skills — Scoop: 컴플라이언스 진입 → Z 힘제어 하강 → check_force_condition 접촉 → 깊이 상한 → 들어올림 →…
- gmp_skills — Pour: 기울임 movel/movesx + fraction<1 시 amove_periodic 털어내기
- gmp_skills — WeighContainer: 파지 → 계량 자세 → samples 회 읽기 → 내려놓기
- gmp_skills — [9/18 확정] 새 배치에서 용기를 파지한 채 passbox·reject_bin 도달 확인 (접근 높이 60 mm 포함) — …
- gmp_skills — [9/18 확정] 원료 선반(판 바깥·높이 다름)에서 Scoop 힘제어 확인 — 뻗은 자세의 특이점 영향 (힘제어는 특이점 영역…
- gmp_dosing — 스쿱 1회 퍼올림량 실측 → dosing.scoop_nominal_g (보정 투입 fraction 계산 근거)
- gmp_process — nodes/process_node.py: 레시피 grade/scoop_id 제거, Pour·WeighContainer stati…
- gmp_process — [추가 7] NUDGE 전이: RUNNING→PAUSED(NUDGE), 다음 NUDGE 로 이전 요청 재개 (인터락 재개 로직 …
- gmp_process — [9/18 확정] 회수·넛지 운영 — 운영 방식은 SOT D-23 으로 확정. 남은 미정 3건: (a) 가득참 판단은 카운트(비…
- gmp_hmi — v1.2 적용: 주문 메시지의 grade/scoop_id, QaDecision.Request의 batch_id 제거(웹 표시는 …
- gmp_hmi — 다른 기기(폰·노트북)에서 http://<로봇PC>:5000 접속 확인 — 시연 T6(c) 장면
- gmp_hmi — process_node 가 BATCH_START 이벤트에 product 를 싣게 C 와 합의 (batches.product 채우…
- gmp_hmi — [9/18 확정] 회수 확인 버튼 — QA 가 패스박스·폐기함 비운 뒤 누르면 카운터 리셋 + HMI_* audit 기록 (누가…
- gmp_bringup — [9/18 확정] common.yaml 에 회수 용량 추가 — output_tray.capacity·reject_bin.capa…
- gmp_bringup — common.yaml 에 qa.decision_timeout_s·interlock.timeout_s 추가 — docs/inter…

> 이 표는 `python3 tools/todo_stats.py` 가 체크박스를 세어 다시 쓴다. 손으로 고치지 않는다.

<!-- STATS:END -->

## gmp_interfaces [조장]
- [ ] **계약 v1.2**: `Deviation.kind` 에 `WRONG_TOOL`·`VERIFY_MISMATCH` 추가, **`WeighHeld` Action(들고 있는 스쿱 계량, D-22·I-007)** 신설, `docs/interfaces.md` 동시 갱신, 팀 채널 공지 · 마감 9/17
- [x] 계약 v1.0 — msg 8 · srv 6 · action 5 정의, `docs/interfaces.md` · 마감 9/16
- [ ] G1 결과로 도징 단위 확정 → `RecipeItem.tol_pct` 기본값·레시피 yaml 갱신 (SOT D-08) · 마감 9/17
- [x] 계약 v1.1 — `Deviation.operator_id`, `record_summary` 폐지, 7절 DB 스키마 · 마감 9/16
- [ ] 계약 v1.2 — 실물 첫날 드러난 것 반영 (인터락 응답 지연 I-004, 그리퍼 백엔드 Q-02) · 마감 9/18
- [ ] **[D-22]** 계약 v1.2 에 `Deviation.kind BATCH_OUT_OF_SPEC` 추가 — `VERIFY` 를 **계측 신뢰성**(Σ투입량 대조)과 **제품 판정**(레시피 총량 대조)으로 나눈다. **조장 합의 완료 (9/17)**. I-007 과 같이 처리 · 마감 9/18

## gmp_skills [A 스킬]
- [ ] **[추가 7] nudge 감지**: 워커 유휴 루프·계량 settle·붓기 대기에서 `get_tool_force` 100 ms 폴링, 임계 초과 시 `CellEvent(NUDGE)` 발행. G1 때 빈 그리퍼 정지 외력 σ 로 임계 8 N 검증 · 마감 9/18
- [ ] **[추가 1] 폭 지문**: 스쿱 손잡이 폭 15/18/21 mm 제작(테이프), `stations.yaml`/`common.yaml` 에 원료별 기대 폭, `SetGripper` 응답 `final_width_mm` 정밀도 실측 · 마감 9/18
- [ ] `adapters/dsr_arm.py`: `DR_init`·`set_tool/tcp`·`movej/movel`·`get_tool_force`·`reset/get_workpiece_weight`·힘제어 짝 함수 — **가상에서 movej 까지** · 마감 9/16
- [ ] `adapters/rg2_gripper.py`: `modbus` 백엔드 (`/onrobot/sendCommand` 폭 정수, `/onrobot_joint_states` → 폭 mm), `virtual` 백엔드 (rad 문자열), 폭 추론 · 마감 9/17
- [ ] `nodes/skill_node.py`: 워커 스레드 + 큐, `MoveToStation`·`SetGripper`·`MeasureForce`·`SafePose` — 가상 동작. `Scoop`의 접촉력·삽입 깊이 Feedback/Result와 `GripperState` 실값 연결 · 마감 9/17
- [ ] **G1 외력 분해능 측정 (I-001)** — 결과를 SOT D-08 에 · 마감 9/17
- [ ] **G2 그리퍼 modbus 실물 확인 (Q-02)** — 안 되면 `dio` 백엔드 (Q-03 핀) · 마감 9/17
- [ ] **G3 `stations.yaml` 9곳 티칭** — 판 좌표계(D-15) 우선, 안 되면 base posx · 마감 9/17
- [ ] `Scoop`: 컴플라이언스 진입 → Z 힘제어 하강 → `check_force_condition` 접촉 → 깊이 상한 → 들어올림 → 해제 짝 (**G4**) · 마감 9/18
- [ ] `Pour`: 기울임 `movel`/`movesx` + `fraction<1` 시 `amove_periodic` 털어내기 · 마감 9/18
- [ ] `WeighContainer`: 파지 → 계량 자세 → `samples` 회 읽기 → 내려놓기 · 마감 9/18
- [ ] **`WeighHeld`**: 들고 있는 스쿱을 계량 자세로 → `samples` 회 읽기. **파지·내려놓기 없음**. 계량 후 그 자세에 머물고(복귀 없음), 빈 그리퍼면 `success=false`. phase 는 `LIFT`/`SETTLE`/`MEASURE`. **D-22 의 `SCOOP_TARE`·`WEIGH_SCOOP`·`WEIGH_RESIDUAL` 세 단계가 이 하나를 쓴다 — 없으면 원료 1종 흐름이 안 돈다** (계약 v1.2, I-007) · 마감 9/21
- [ ] 기동 자가진단: `get_current_tool/tcp/collision_sensitivity` 가 파라미터와 다르면 기동 거부 · 마감 9/21
- [ ] I-004 이동 취소 수단 결정·구현 · 마감 9/21
- [ ] `gripper_state` 10 Hz 발행, 미끄러짐 감지(`slip_mm`) · 마감 9/21
- [ ] **[9/18 확정]** 새 배치에서 **용기를 파지한 채** `passbox`·`reject_bin` 도달 확인 (접근 높이 60 mm 포함) — 둘 다 사용 불가 영역에 붙어 있어 자세 제약 우려 · 마감 9/18
- [ ] **[9/18 확정]** 원료 선반(판 바깥·높이 다름)에서 `Scoop` 힘제어 확인 — 뻗은 자세의 특이점 영향 (힘제어는 특이점 영역에서 제한적) · 마감 9/18

## gmp_dosing [B 도징]
- [ ] **[추가 5] 원료 잔량 추정**: `core/inventory.py` — 원료별 초기량·누적 투입량·접촉 높이(z) 로 잔량 추정, 보충 임계 판단 (순수 함수 + 테스트) · 마감 9/21
- [ ] `core/scale.py`: `WeightModel` — 힘/작업물무게 → g, 영점(tare) 저장·적용, σ 계산, `valid` 판정 · 마감 9/16
- [ ] `core/dosing.py`: `decide(target_g, actual_g, tol_pct, attempts, history) → Decision(action, fraction)` — `OK/UNDER/OVER/TIMEOUT`, 보정 투입 시 `fraction` 축소 규칙 · 마감 9/16
- [ ] `test/test_dosing.py`: 경계값(±tol 정확히), 3회 재시도 후 TIMEOUT, OVER 즉시 일탈, 분해능 σ 가 tol 보다 클 때 `valid=false` · 마감 9/17
- [ ] G1 σ 로 `scale.min_resolvable_g` 갱신, 30 g / 100 g 단위 확정 (조장과) · 마감 9/17
- [ ] 스쿱 1회 퍼올림량 실측 → `dosing.scoop_nominal_g` (보정 투입 fraction 계산 근거) · 마감 9/18
- [ ] 보정 계수: 실제 저울 vs 로봇 측정 5점 비교 → `scale.gain`/`scale.offset` · 마감 9/21

## gmp_process [C 공정]
- [x] `core/recipe.py`: yaml → `RecipeSpec` 변환, 필수 필드·원료 중복·양수 검증 + `test_recipe.py` 25건 (`Recipe` msg 변환은 계층 원칙상 `nodes/` 가 한다 / **순서는 검증 대상이 아니다** — 계약 1절 "순서 위반은 일탈이 아니라 버그") · 마감 9/16
- [x] `core/process_fsm.py`: 상태·전이표(`docs/architecture.md`) 순수 구현, 이벤트 입력 → 다음 상태 + 스킬 요청 — **D-22 6단계(스쿱 계량 3회 + VERIFY)** 반영 · 마감 9/17
- [x] `test/test_process_fsm.py`: 정상 완주(6단계 순서), 붓기 전 계량으로 초과 예방, UNDER 보정 누적, OVER → QA → DISCARD(스쿱 반납 후), VERIFY 불일치 → QA, 계량 무효 재시도, 파지 실패, REFILL 재개 — 9건 · 마감 9/17
- [x] **[D-22]** `_pour_fraction` → `gmp_dosing/core/dosing.py` 의 `pour_fraction(need_g, scooped_g, cfg)` 로 이관 완료. `weigh_scoop` 요청은 계약 v1.2 의 `WeighHeld` Action 에 대응한다 (매핑은 `process_node` 구현 시) · 마감 9/18
- [x] **[D-22]** `VERIFY` 이중 판정 구현 — ① `|net − Σtarget| > Σ(target×tol)` → `BATCH_OUT_OF_SPEC` ② `|net − Σ투입량| > min_resolvable_g` → `VERIFY_MISMATCH`. **임계 제약 발견**: `min_resolvable_g < Σ(target×tol)` 가 아니면 ②는 죽은 검사다 (현재 30 > 22.5) → SOT Q-11 에 기록 · 마감 9/18
- [ ] **[D-22]** G1 결과로 **VERIFY ② 유효성 판단** — `min_resolvable_g < Σ(target×tol)` 이면 ②를 유지하고, 아니면 **②(`VERIFY_MISMATCH`)를 제거**한다. 현재 값(30 vs 22.5)이면 ②는 한 번도 안 울린다. 있으나 마나 한 검사를 남기면 나중에 "왜 안 울리지" 로 또 헤맨다. **G1(A) · `min_resolvable_g` 갱신(B) 이후** · 마감 9/21
- [ ] `nodes/process_node.py`: 레시피 `grade/scoop_id` 제거, `Pour`·`WeighContainer` station 인자 제거, `SetGripper` 클라이언트, `QaDecision.deviation_id` 일치 검증, 시도 종료 시 `scoop_cycle` 발행을 포함한 스킬 Action/Service 연동 — **가상에서 레시피 1건 완주** · 마감 9/18
- [ ] 일탈 카탈로그(`core/deviation.py`): kind 별 자동 복구 규칙(재시도 상한·보충 요청·QA 요청) · 마감 9/21
- [x] 스테이션 물리 배치·테이프 표시 (하드웨어) — **9/18 완료**. 좌표 실측(G3)과 SOT D-24 등록이 이제 가능하다 · 마감 9/17
- [ ] 고의 장애 주입 T6 (a)(b)(c) 재현 · 마감 9/22
- [ ] **[추가 7] NUDGE 전이**: `RUNNING→PAUSED(NUDGE)`, 다음 NUDGE 로 이전 요청 재개 (인터락 재개 로직 재사용) + 테스트 · 마감 9/18
- [ ] **[추가 1] 폭 지문**: `PICK_SCOOP`·`PICK_CONTAINER` 의 `SetGripper` 결과 폭이 원료별 기대 폭(±margin) 과 다르면 `Deviation(WRONG_TOOL)` → QA · 마감 9/21 (A 와)
- [ ] **[추가 3] 재기동 이어하기**: 기동 시 DB 의 미완료 배치 조회 → 상태·원료 인덱스·tare 복원 → 용기 재계량 후 재개. 시연: 실행 중 Ctrl-C → 재실행 · 마감 9/22 (D 와)
- [x] **[9/18 확정]** 배치 변경 반영 → **SOT D-24 로 등록 완료**. 판 450×450, 기준 원점 [X:0, Y:45], 로봇 좌측 28 cm, 중앙 300×300 배치 불가, 넛지 대기 위치 신설, 원료·스쿱은 판 바깥 아래. **스테이션 매핑 3건은 Q-12 로 분리** · 마감 9/21
- [x] **[Q-12]** Pass Box 가 `magazine`·`output_tray` 를 대체 — **확정 (9/18)**. `passbox_empty`(빈통) · `passbox_done`(완성품) 으로 이름과 FSM `carry` 목적지를 바꿨다 · 마감 9/21
- [x] **[D-24]** `nudge_wait` 스테이션 추가 — 세트 완료 후 이동해 NUDGE 대기. `safe` 와 별개 (판 우상단). **FSM 전이는 「추가 7 NUDGE 전이」 항목에서 같이** · 마감 9/21
- [ ] **[9/18 확정]** 회수·넛지 운영 — **운영 방식은 SOT D-23 으로 확정**. 남은 미정 3건: (a) 가득참 판단은 **카운트**(비전 없음) → `output_tray.capacity`·`reject_bin.capacity` (b) QA 가 비운 것을 아는 방법 — HMI 확인 버튼 유력 (c) **"한 세트"의 정의**(배치 1건 / 레시피 1건 / 용기 N통) · 마감 9/18
- [x] ~~[버그] 일탈 QA 대기가 무한 블로킹~~ — **문제 없음으로 결론 (9/18)**. D-23 반자동 운전이라 사람이 붙어 있는 것이 설계이고(세트 경계 대기도 마찬가지), 워커가 `daemon=True` 스레드라 Ctrl+C 로 정상 종료된다. 일탈 대기에만 타임아웃을 걸면 세트 경계 대기와 일관성이 깨진다 · 마감 9/18
- [ ] **Ctrl+C 종료 시 로봇 상태 확인** — 순응/힘제어가 켜진 채 남는지, 그리퍼가 스쿱을 쥔 채 멈추는지. `release_force`·`release_compliance_ctrl` 이 `dsr_arm.py` 짝 함수 안에만 있고 종료 경로에는 없다 → 재기동 시 `2.3501`(15Nm 외부 토크)·`2.1903` 가능. 남으면 **종료 훅에 `release_force` + `release_compliance_ctrl` 추가** (cancel 콜백보다 작고 Ctrl+C·cancel 둘 다 커버). `RunBatch` cancel 이 이것으로 대체 가능한지 같이 판단 · 마감 9/21

## gmp_hmi [D HMI·기록]
- [x] `config/schema.sql` · `core/db.py`: 6 테이블, 쓰기·조회·KPI·JSON 내보내기, 단위 테스트 · 마감 9/16
- [x] `nodes/record_node.py`: 구독 5종 → SQLite, 배치 종료 시 JSON 내보내기, `HMI_*` → audit · 마감 9/16
- [ ] v1.2 적용: 주문 메시지의 `grade/scoop_id`, `QaDecision.Request`의 `batch_id` 제거(웹 표시는 유지), `scoop_cycle` 구독·DB 테이블·JSON 내보내기 추가 · 마감 9/18
- [x] `nodes/hmi_web_node.py` + `templates/index.html`: Flask 골격 — 주문·상태·계량 그래프·일탈 판정·인터락·이력·KPI·감사 추적 · 마감 9/16
- [ ] `sudo apt install python3-flask` 후 가상 모드에서 **주문 → 상태 → QA 승인 → 이력 조회 한 바퀴** · 마감 9/17
- [ ] 다른 기기(폰·노트북)에서 `http://<로봇PC>:5000` 접속 확인 — 시연 T6(c) 장면 · 마감 9/18
- [ ] process_node 가 `BATCH_START` 이벤트에 product 를 싣게 C 와 합의 (batches.product 채우기) · 마감 9/18
- [ ] **[9/18 확정]** 회수 확인 버튼 — QA 가 패스박스·폐기함 비운 뒤 누르면 카운터 리셋 + `HMI_*` audit 기록 (누가 언제 회수했는지) · 마감 9/18
- [ ] 배치 중단 버튼 — `RunBatch` cancel 호출 + `HMI_*` audit (C 의 cancel 콜백과 짝) · 마감 9/21
- [ ] 계량 그래프에 목표선·허용 오차 밴드, 배치 클릭 → `/batch/<id>` 상세 · 마감 9/21
- [ ] **[추가 3] 재기동 이어하기**: `db.py` 에 미완료 배치·마지막 상태 조회 API, `record_node` 가 상태 전이마다 저장 · 마감 9/21 (C 와)
- [ ] **[추가 7] HMI**: PAUSED 사유(NUDGE/REFILL) 표시, 이벤트 타임라인에 NUDGE · 마감 9/19
- [ ] **[추가 5] 잔량 표시**: HMI 에 원료별 잔량 게이지 + "보충 권고" 배너 · 마감 9/22 (B 와)
- [ ] `tools/report.py`: DB 에서 계약 6절 지표 표 출력 (`/kpi` 와 같은 쿼리) · 마감 9/25
- [ ] 1분 영상 편집·PPT (조장과) · 마감 9/28

## gmp_bringup [조장]
- [x] `cell.launch.py` — 벤더 브링업 include + 우리 노드 4개 (ns `cell`), `mode`/`host`/`vel_scale` 인자 · 마감 9/16
- [x] `params/common.yaml` · `stations.yaml`(placeholder) · `recipes/demo_batch.yaml` · 마감 9/16
- [ ] `tools/env.sh` 세 워크스페이스 source · 마감 9/16
- [ ] 가상 모드에서 4노드 기동 확인, `ros2 node list`/`rqt_graph` 캡처 · 마감 9/17
- [ ] `docs/demo_run_procedure.md` T1~T7 실물 검증 · 마감 9/23
- [x] **[9/18 확정]** `stations.yaml` 구조 개편 — `scoop_rack` 폐지, `scoop_1`~`scoop_4` 신설 (원료통 아래, `material_id` 짝). FSM 은 `material_id` 만 넘기고 `process_node` 가 짝을 찾는다. **좌표값은 실측 후 조장이 채운다** · 마감 9/18
- [ ] **[9/18 확정]** `common.yaml` 에 회수 용량 추가 — `output_tray.capacity`·`reject_bin.capacity`, 도달 시 인터락 요청 · 마감 9/18
- [ ] `common.yaml` 에 `qa.decision_timeout_s`·`interlock.timeout_s` 추가 — `docs/interfaces.md` §4 는 common.yaml 이 "타임아웃" 을 담는다고 적혀 있으나 **실제 키가 하나도 없다** · 마감 9/18
- [ ] BRD v1.0 · SDD v1.0 (`docs/spec/`) · 마감 9/28
