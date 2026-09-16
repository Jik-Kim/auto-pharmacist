# Feature Todo — 파일·메서드 기준

각 줄 끝의 `마감 M/D` 는 실물 5일(9/17·18·21·22·23) 기준이다. 진행률은 아래 표가 체크박스를 세어 보여준다.
**이슈를 닫거나 상태를 바꿀 때는 여기 체크박스도 같이 본다.**

<!-- STATS:BEGIN -->

**전체 7/44 완료** (███░░░░░░░░░░░░░░░░░)  ·  기준 09/16

| 파트 | 완료 | 진행 | 지난 마감 |
|---|---|---|---|
| gmp_interfaces [조장] | 2/4 | `█████░░░░░` | — |
| gmp_skills [A 스킬] | 0/12 | `░░░░░░░░░░` | — |
| gmp_dosing [B 도징] | 0/6 | `░░░░░░░░░░` | — |
| gmp_process [C 공정] | 0/7 | `░░░░░░░░░░` | — |
| gmp_hmi [D HMI·기록] | 3/9 | `███░░░░░░░` | — |
| gmp_bringup [조장] | 2/6 | `███░░░░░░░` | — |

**오늘 마감 5건**

- gmp_skills — adapters/dsr_arm.py: DR_init·set_tool/tcp·movej/movel·get_tool_force·re…
- gmp_dosing — core/scale.py: WeightModel — 힘/작업물무게 → g, 영점(tare) 저장·적용, σ 계산, valid 판정
- gmp_dosing — core/dosing.py: decide(target_g, actual_g, tol_pct, attempts, history) …
- gmp_process — core/recipe.py: yaml → Recipe 변환, 순서·필수 필드 검증
- gmp_bringup — tools/env.sh 세 워크스페이스 source

> 이 표는 `python3 tools/todo_stats.py` 가 체크박스를 세어 다시 쓴다. 손으로 고치지 않는다.

<!-- STATS:END -->

## gmp_interfaces [조장]
- [x] 계약 v1.0 — msg 8 · srv 6 · action 5 정의, `docs/interfaces.md` · 마감 9/16
- [ ] G1 결과로 도징 단위 확정 → `RecipeItem.tol_pct` 기본값·레시피 yaml 갱신 (SOT D-08) · 마감 9/17
- [x] 계약 v1.1 — `Deviation.operator_id`, `record_summary` 폐지, 7절 DB 스키마 · 마감 9/16
- [ ] 계약 v1.2 — 실물 첫날 드러난 것 반영 (인터락 응답 지연 I-004, 그리퍼 백엔드 Q-02) · 마감 9/18

## gmp_skills [A 스킬]
- [ ] `adapters/dsr_arm.py`: `DR_init`·`set_tool/tcp`·`movej/movel`·`get_tool_force`·`reset/get_workpiece_weight`·힘제어 짝 함수 — **가상에서 movej 까지** · 마감 9/16
- [ ] `adapters/rg2_gripper.py`: `modbus` 백엔드 (`/onrobot/sendCommand` 폭 정수, `/onrobot_joint_states` → 폭 mm), `virtual` 백엔드 (rad 문자열), 폭 추론 · 마감 9/17
- [ ] `nodes/skill_node.py`: 워커 스레드 + 큐, `MoveToStation`·`Grip`·`MeasureForce`·`SafePose` — 가상 동작 · 마감 9/17
- [ ] **G1 외력 분해능 측정 (I-001)** — 결과를 SOT D-08 에 · 마감 9/17
- [ ] **G2 그리퍼 modbus 실물 확인 (Q-02)** — 안 되면 `dio` 백엔드 (Q-03 핀) · 마감 9/17
- [ ] **G3 `stations.yaml` 9곳 티칭** — 판 좌표계(D-15) 우선, 안 되면 base posx · 마감 9/17
- [ ] `Scoop`: 컴플라이언스 진입 → Z 힘제어 하강 → `check_force_condition` 접촉 → 깊이 상한 → 들어올림 → 해제 짝 (**G4**) · 마감 9/18
- [ ] `Pour`: 기울임 `movel`/`movesx` + `fraction<1` 시 `amove_periodic` 털어내기 · 마감 9/18
- [ ] `WeighContainer`: 파지 → 계량 자세 → `samples` 회 읽기 → 내려놓기 · 마감 9/18
- [ ] 기동 자가진단: `get_current_tool/tcp/collision_sensitivity` 가 파라미터와 다르면 기동 거부 · 마감 9/21
- [ ] I-004 이동 취소 수단 결정·구현 · 마감 9/21
- [ ] `gripper_state` 10 Hz 발행, 미끄러짐 감지(`slip_mm`) · 마감 9/21

## gmp_dosing [B 도징]
- [ ] `core/scale.py`: `WeightModel` — 힘/작업물무게 → g, 영점(tare) 저장·적용, σ 계산, `valid` 판정 · 마감 9/16
- [ ] `core/dosing.py`: `decide(target_g, actual_g, tol_pct, attempts, history) → Decision(action, fraction)` — `OK/UNDER/OVER/TIMEOUT`, 보정 투입 시 `fraction` 축소 규칙 · 마감 9/16
- [ ] `test/test_dosing.py`: 경계값(±tol 정확히), 3회 재시도 후 TIMEOUT, OVER 즉시 일탈, 분해능 σ 가 tol 보다 클 때 `valid=false` · 마감 9/17
- [ ] G1 σ 로 `scale.min_resolvable_g` 갱신, 30 g / 100 g 단위 확정 (조장과) · 마감 9/17
- [ ] 스쿱 1회 퍼올림량 실측 → `dosing.scoop_nominal_g` (보정 투입 fraction 계산 근거) · 마감 9/18
- [ ] 보정 계수: 실제 저울 vs 로봇 측정 5점 비교 → `scale.gain`/`scale.offset` · 마감 9/21

## gmp_process [C 공정]
- [ ] `core/recipe.py`: yaml → `Recipe` 변환, 순서·필수 필드 검증 · 마감 9/16
- [ ] `core/process_fsm.py`: 상태·전이표(`docs/architecture.md`) 순수 구현, 이벤트 입력 → 다음 상태 + 스킬 요청 · 마감 9/17
- [ ] `test/test_process_fsm.py`: 정상 3원료 완주, UNDER 보정 루프, OVER → DEVIATION → APPROVE/DISCARD, 인터락 ENTER/EXIT 재개 · 마감 9/17
- [ ] `nodes/process_node.py`: 스킬 Action/Service 클라이언트, `RunBatch`·`SubmitOrder`·`QaDecision`·`InterlockRequest` 서버, `state`(2 Hz, TRANSIENT_LOCAL)·`weight`·`dispense_result`·`deviation`·`event` 발행 — **가상에서 레시피 1건 완주** · 마감 9/18
- [ ] 일탈 카탈로그(`core/deviation.py`): kind 별 자동 복구 규칙(재시도 상한·보충 요청·QA 요청) · 마감 9/21
- [ ] 스테이션 물리 배치·테이프 표시 (하드웨어) · 마감 9/17
- [ ] 고의 장애 주입 T6 (a)(b)(c) 재현 · 마감 9/22

## gmp_hmi [D HMI·기록]
- [x] `config/schema.sql` · `core/db.py`: 6 테이블, 쓰기·조회·KPI·JSON 내보내기, 단위 테스트 · 마감 9/16
- [x] `nodes/record_node.py`: 구독 5종 → SQLite, 배치 종료 시 JSON 내보내기, `HMI_*` → audit · 마감 9/16
- [x] `nodes/hmi_web_node.py` + `templates/index.html`: Flask 골격 — 주문·상태·계량 그래프·일탈 판정·인터락·이력·KPI·감사 추적 · 마감 9/16
- [ ] `sudo apt install python3-flask` 후 가상 모드에서 **주문 → 상태 → QA 승인 → 이력 조회 한 바퀴** · 마감 9/17
- [ ] 다른 기기(폰·노트북)에서 `http://<로봇PC>:5000` 접속 확인 — 시연 T6(c) 장면 · 마감 9/18
- [ ] process_node 가 `BATCH_START` 이벤트에 product 를 싣게 C 와 합의 (batches.product 채우기) · 마감 9/18
- [ ] 계량 그래프에 목표선·허용 오차 밴드, 배치 클릭 → `/batch/<id>` 상세 · 마감 9/21
- [ ] `tools/report.py`: DB 에서 계약 6절 지표 표 출력 (`/kpi` 와 같은 쿼리) · 마감 9/25
- [ ] 1분 영상 편집·PPT (조장과) · 마감 9/28

## gmp_bringup [조장]
- [x] `cell.launch.py` — 벤더 브링업 include + 우리 노드 4개 (ns `cell`), `mode`/`host`/`vel_scale` 인자 · 마감 9/16
- [x] `params/common.yaml` · `stations.yaml`(placeholder) · `recipes/demo_batch.yaml` · 마감 9/16
- [ ] `tools/env.sh` 세 워크스페이스 source · 마감 9/16
- [ ] 가상 모드에서 4노드 기동 확인, `ros2 node list`/`rqt_graph` 캡처 · 마감 9/17
- [ ] `docs/demo_run_procedure.md` T1~T7 실물 검증 · 마감 9/23
- [ ] BRD v1.0 · SDD v1.0 (`docs/spec/`) · 마감 9/28
