# Feature Todo — 파일·메서드 기준

각 줄 끝의 `마감 M/D` 는 실물 5일(9/17·18·21·22·23) 기준이다. 진행률은 아래 표가 체크박스를 세어 보여준다.
**이슈를 닫거나 상태를 바꿀 때는 여기 체크박스도 같이 본다.**

### 9/20 정리 · 오늘 우선순위

현재 체크아웃 `155418a` 기준으로 코드·설정·이력을 대조했다. `89c537f`·`5d50043`에서 주요 할 일이 유실된 증거는 없으며, 완료 상태·동작 설명·최근 변경의 미반영을 보정했다. `[x]`는 각 항목에 적힌 범위의 완료이며 실물 검증 완료를 뜻하지 않는다. 기존 마감은 유지하고 새 인계의 마감은 작업 제안으로 적는다.

1. **오늘 우선 — 조장·C:** SOT·공정 도면의 잔여 충돌 표시와 문서 간 오래된 설명 정리. 아래 bringup 문서 정합성 항목을 따른다.
2. **오늘 인계 — 조장·A·C·D:** 계약 v1.3 담당 승인 확인, 원료 반환 6개 자세 티칭 준비, HMI 반환 상태·결과 표시 인계. 코드 구현과 계약 확정은 별개다.
3. **9/21 실물 전 — A·B:** 표본 간격 0.82초 적용 후 실측·보정 3점 확인, 관절 이송 속도·가속도 확정 및 경로 검증. 현재 관절 경로는 비활성이고 원료 반환 자세는 null이다.
4. **사용자 통합 검증 — A·C·D:** 인터페이스·호출 패키지 재빌드 후 진짜 가상 브링업과 HMI 한 바퀴, 이후 실물 동작 확인. `WeighHeld` 서버는 구현됐으나 E2E 완료 근거는 아직 없다.

**9/20 확인:** 도징 단위 테스트 10건 통과, `tools/env.sh` 문법 검사 통과. 이번 정리는 로봇 구동·가상 E2E·실물 검증을 수행하지 않았다. `docs/issues.md`의 I-001은 아직 열림이며 측정 결과와 남은 후속을 구분하는 조장 정리가 필요하다.

<!-- STATS:BEGIN -->

**전체 48/94 완료** (██████████░░░░░░░░░░)  ·  기준 09/20

| 파트 | 완료 | 진행 | 지난 마감 |
|---|---|---|---|
| gmp_interfaces [조장] | 5/9 | `██████░░░░` | **3** |
| gmp_skills [A 스킬] | 9/21 | `████░░░░░░` | **8** |
| gmp_dosing [B 도징] | 7/11 | `██████░░░░` | **1** |
| gmp_process [C 공정] | 15/24 | `██████░░░░` | **1** |
| gmp_hmi [D HMI·기록] | 8/17 | `█████░░░░░` | **2** |
| gmp_bringup [조장] | 4/12 | `███░░░░░░░` | **3** |

**마감이 지난 항목 18건**

- `9/18` gmp_interfaces — 계약 v1.2 팀 채널 공지 — 위 항목 중 공지만 남았다 (WeighHeld 신설·Deviation.kind 3종 추가)
- `9/17` gmp_interfaces — G1 결과로 도징 단위 확정 → RecipeItem.tol_pct 기본값·레시피 yaml 갱신 (SOT D-08)
- `9/18` gmp_interfaces — 계약 v1.2 — 실물 첫날 드러난 것 반영 (인터락 응답 지연 I-004, 그리퍼 백엔드 Q-02)
- `9/18` gmp_skills — [추가 7] nudge 감지: 워커 유휴 루프·계량 settle·붓기 대기에서 get_tool_force 100 ms 폴링, 임…
- `9/18` gmp_skills — [추가 1] 폭 지문: 스쿱 손잡이 폭 A/B/C = 15.5/18/28 mm로 확정(9/19), stations.yaml/co…
- `9/16` gmp_skills — adapters/dsr_arm.py: DR_init·set_tool/tcp·movej/movel·get_tool_force·re…
- `9/17` gmp_skills — nodes/skill_node.py: 워커 스레드 + 큐, MoveToStation·Scoop·Pour·WeighContaine…
- `9/17` gmp_skills — G2 그리퍼 modbus 실물 확인 (Q-02) — 안 되면 dio 백엔드 (Q-03 핀)
- `9/18` gmp_skills — Scoop: 컴플라이언스 진입 → Z 힘제어 하강 → check_force_condition 접촉 → 깊이 상한 → 들어올림 →…
- `9/18` gmp_skills — [9/18 확정] 새 배치에서 용기를 파지한 채 passbox_done·reject_bin 도달 확인 — 현재 용기 AT Z=1…
- `9/18` gmp_skills — [9/18 확정] 원료 선반(판 바깥·높이 다름)에서 Scoop 힘제어 확인 — 뻗은 자세의 특이점 영향 (힘제어는 특이점 영역…
- `9/18` gmp_dosing — 스쿱 1회 퍼올림량 실측 → dosing.scoop_nominal_g (보정 투입 fraction 계산 근거)
- `9/18` gmp_process — [9/18 확정] 회수·넛지 운영 — 세트=배치 1건, 반송 뒤 NUDGE_WAIT 전이는 구현 완료. HMI 회수 확인 감사 …
- `9/17` gmp_hmi — [연동 대기 — 9/20 갱신] 가상 모드에서 주문 → 상태 → QA 승인 → 이력 조회 한 바퀴 — HMI 시험 공정 검증 이…
- `9/19` gmp_hmi — [추가 7] HMI: static/hmi.js에 NUDGE/REFILL 사유 표시·이벤트 조회 코드 존재. 실제 CellStat…
- `9/17` gmp_bringup — 가상 모드에서 4노드 기동 확인, ros2 node list/rqt_graph 캡처
- `9/18` gmp_bringup — [9/18 확정] common.yaml 에 회수 용량 추가 — passbox_done.capacity·reject_bin.cap…
- `9/18` gmp_bringup — [정책 확인] qa.decision_timeout_s·interlock.timeout_s 필요 여부를 D-23 무기한 QA 대기…

**오늘 마감 2건**

- gmp_bringup — [9/20 우선·문서 정합성] docs/SOT.md D-08과 docs/diagrams/process_flow.drawio의 커…
- gmp_bringup — [G1 이슈 정합성] docs/issues.md I-001의 “미측정” 설명을 9/18·19 측정 결과와 남은 판정/보정 후속으…

> 이 표는 `python3 tools/todo_stats.py` 가 체크박스를 세어 다시 쓴다. 손으로 고치지 않는다.

<!-- STATS:END -->

## gmp_interfaces [조장]
- [x] **[v1.3 구현]** `ReturnMaterial.action`·CMake 등록, `ScoopCycle.RETURNED=5/RETURN_FAILED=6`, 전량 붓기·원료별 스쿱 계량 의미를 `docs/interfaces.md`와 함께 반영. 팀 공유 완료 기록은 SOT에 있으며 **담당 승인 전 초안** · 마감 9/20
- [ ] **[v1.3 확정]** 영향 담당 최소 2명 승인 확인 및 v1.2/v1.3 승인 상태 문구 정합성 정리 — 구현 완료와 계약 확정을 구분 · 마감 9/21
- [x] **계약 v1.2**: `Deviation.kind` 에 `WRONG_TOOL`·`VERIFY_MISMATCH` 추가, **`WeighHeld` Action(들고 있는 스쿱 계량, D-22·I-007)** 신설, `docs/interfaces.md` 동시 갱신 — **PR #10 머지 완료 (9/18)** · 마감 9/17
- [ ] 계약 v1.2 **팀 채널 공지** — 위 항목 중 공지만 남았다 (`WeighHeld` 신설·`Deviation.kind` 3종 추가) · 마감 9/18
- [x] 계약 v1.0 — msg 8 · srv 6 · action 5 정의, `docs/interfaces.md` · 마감 9/16
- [ ] G1 결과로 도징 단위 확정 → `RecipeItem.tol_pct` 기본값·레시피 yaml 갱신 (SOT D-08) · 마감 9/17
- [x] 계약 v1.1 — `Deviation.operator_id`, `record_summary` 폐지, 7절 DB 스키마 · 마감 9/16
- [ ] 계약 v1.2 — 실물 첫날 드러난 것 반영 (인터락 응답 지연 I-004, 그리퍼 백엔드 Q-02) · 마감 9/18
- [x] **[D-22]** 계약 v1.2 에 `Deviation.kind BATCH_OUT_OF_SPEC` 추가 — `VERIFY` 를 **계측 신뢰성**(Σ투입량 대조)과 **제품 판정**(레시피 총량 대조)으로 나눈다. **조장 합의 완료 (9/17)**. I-007 과 같이 처리 · 마감 9/18

## gmp_skills [A 스킬]
- [x] **[관절 이송 구현]** `core/transfer.py`·`skill_node._run_transfer()`의 직선 이탈→관절 이송, 출발·도착·파지 검증, 취소/실패 시 이력 무효화 구현 및 단위 테스트 존재. 가상은 기존 직선 이동, 실물은 비활성/미등록 경로 거부. **실물 검증·활성화는 미완료** · 마감 9/20
- [x] **[원료 반환 구현]** `skill_node._do_return_material()`·Action 서버: 원료별 티칭 시작→종료→시작 반환, 잘못된/미입력 좌표 거부 구현 및 단위 테스트 존재. 실물 확인은 별도 · 마감 9/20
- [ ] **[반환 티칭·실물]** `stations.yaml` 원료 A/B/C의 `return_start_posx`·`return_end_posx` **총 6개** 입력(현재 null), 동일 원료통 낙하·왕복 간섭·잔량 확인. 사용자 검증 후 운용 · 마감 9/21
- [ ] **[추가 7] nudge 감지**: 워커 유휴 루프·계량 settle·붓기 대기에서 `get_tool_force` 100 ms 폴링, 임계 초과 시 `CellEvent(NUDGE)` 발행. **코드·순수 테스트 완료(9/19), 남은 것은 G1에서 빈 그리퍼 정지 외력 σ로 임계 8 N 실물 검증 — 실물 보류** · 마감 9/18
- [ ] **[추가 1] 폭 지문**: 스쿱 손잡이 폭 **A/B/C = 15.5/18/28 mm**로 확정(9/19), `stations.yaml`/`common.yaml` 반영 완료. **남은 것은 스쿱 제작 상태 확인, `SetGripper.final_width_mm` 정밀도 실측, C의 WRONG_TOOL 판정 연동 — 실물 보류** · 마감 9/18
- [ ] `adapters/dsr_arm.py`: `DR_init`·`set_tool/tcp`·`movej/movel`·`get_tool_force`·`reset/get_workpiece_weight`·힘제어 짝 함수 **코드·단위 테스트 완료**. **남은 것은 현재 기동 수정본으로 가상 `movej` 통합 재검증** · 마감 9/16
- [x] `adapters/rg2_gripper.py`: `modbus` 백엔드 (`/onrobot/sendCommand` 폭 정수, `/onrobot_joint_states` → 폭 mm), `virtual` 백엔드 (rad 문자열), 폭 추론 구현·단위 테스트 완료. **실물 Modbus 검증은 G2에서 별도 보류** · 마감 9/17
- [ ] `nodes/skill_node.py`: 워커 스레드 + 큐, `MoveToStation`·`Scoop`·`Pour`·`WeighContainer`·`WeighHeld`, `SetGripper`·`MeasureForce`·`SafePose`, 피드백·결과 연결 구현. **`WeighHeld`·Scoop 스테이션 조회 결함은 코드·단위 테스트 완료(9/19), 남은 것은 진짜 가상 브링업 E2E** · 마감 9/17
- [x] **G1 외력 분해능 측정 (I-001의 측정 범위)** — 9/18·19 실측 완료: 0.05 s 표본 조건 3σ=18.0 g → `min_resolvable_g=19`, 0.82 s 독립 표본 재측정 3σ=12.6/12.4 g. 근거 `gmp_dosing/calibration/`·`config/scale_reference.yaml`·SOT D-08. 넛지 임계 실물 검증·표본 간격 개선·3점 보정은 별도 미완료 항목이며 I-001 전체 해결을 뜻하지 않는다 · 마감 9/17
- [ ] **G2 그리퍼 modbus 실물 확인 (Q-02)** — 안 되면 `dio` 백엔드 (Q-03 핀) · 마감 9/17
- [x] **G3 `stations.yaml` 좌표 입력** — 실측 BASE 좌표 15곳(`workbench` 붓기 시작·끝, 원료 측정 바닥 접촉점 포함) 저장 및 YAML 파싱 확인 완료(9/19). **실제 접근 궤적·간섭 검증은 아래 실물 항목으로 보류** · 마감 9/17
- [ ] `Scoop`: 컴플라이언스 진입 → Z 힘제어 하강 → `check_force_condition` 접촉 → 깊이 상한 → 들어올림 → 해제 짝 **본문 구현 완료**. `StationTable.for_material()`은 `material_N`만 선택하도록 수정·회귀 테스트 완료(9/19). **남은 것은 G4 실물 검증 — 실물 보류** · 마감 9/18
- [x] `Pour`: `workbench.pour_start_posx → pour_end_posx → pour_start_posx` 티칭 경로로 **전량 붓기(fraction=1.0)** 구현. 기존 부분 붓기·털어내기는 폐기했으며 초과 스쿱은 `ReturnMaterial`로 처리한다. 관련 단위 테스트 존재, 실물 궤적·낙하·간섭 검증은 보류 · 마감 9/18
- [x] `WeighContainer`: 파지 → 계량 자세 → `samples` 회 읽기 → 내려놓기 구현 완료. **실물 계량 검증은 보류** · 마감 9/18
- [ ] 기동 자가진단: `get_current_tool/tcp` 불일치 기동 거부 구현 완료. **`collision_sensitivity` getter 확인·비교는 미구현** · 마감 9/21
- [ ] I-004 이동 취소: `MoveToStation`·`SafePose`는 `amovej/amovel` + `check_motion` + `MoveStop` 구현 완료. **Scoop·Pour·Weigh 내부 블로킹 이동 취소는 미구현** · 마감 9/21
- [x] `gripper_state` 10 Hz 발행, 미끄러짐 감지(`slip_mm`) 구현·단위 테스트 완료 · 마감 9/21
- [ ] **[I-009]** `skill_node` 의 `approach == 0` 숫자 비교를 `MoveToStation.Goal.ABOVE` 로 — 상수가 이제 Goal 절에 있어 쓸 수 있다 (급하지 않음) · 마감 9/21
- [x] **`WeighHeld`**: 들고 있는 스쿱을 대응 `material_N.posx`로 이동해 계량하고 그 자리에 머문다. `measure_posx`는 재고 접촉 자세이며 계량에 쓰지 않는다. 빈 그리퍼 실패·`LIFT/SETTLE/MEASURE`·`subject=scoop` 및 단위 테스트 구현. 첫 호출의 BASE +Y 150 mm 인출 유지. 실물 궤적·간섭 검증은 보류 · 마감 9/21
- [ ] **[9/18 확정]** 새 배치에서 **용기를 파지한 채** `passbox_done`·`reject_bin` 도달 확인 — 현재 용기 AT Z=100, ABOVE는 workbench +100 mm·passbox/reject +50 mm, EXIT는 각각 +200/+150 mm. 기존 60 mm 설명은 폐기. 관절 이송 비활성·보호 목적지 진입 제약과 실제 궤적·간섭을 함께 확인 · 마감 9/18
- [ ] **[9/18 확정]** 원료 선반(판 바깥·높이 다름)에서 `Scoop` 힘제어 확인 — 뻗은 자세의 특이점 영향 (힘제어는 특이점 영역에서 제한적) · 마감 9/18

## gmp_dosing [B 도징]
- [ ] **[추가 5] 원료 잔량 추정**: `core/inventory.py` — 원료별 초기량·누적 투입량·접촉 높이(z) 로 잔량 추정, 보충 임계 판단 (순수 함수 + 테스트) · 마감 9/21
- [x] `core/scale.py`: `WeightModel` 힘/작업물무게 → g, tare·표준편차·유효성 및 분해능 판정 구현. `valid`는 원본 유효성과 표준편차로, 목표 허용 폭 판정은 `resolvable()`로 구분한다. 9/20 도징 테스트 10건 통과 · 마감 9/16
- [x] `core/dosing.py`: `decide(target_g, actual_g, tol_pct, attempts, valid, invalid_count, cfg)` → `DONE/SCOOP/DEVIATION`, `OK/UNDER/OVER/INVALID` 및 시도 상한·무효 계량 처리 구현. 9/20 도징 테스트 10건 통과. 공정은 현재 전량 붓기·초과 반환을 사용하므로 라이브러리의 fraction 계산이 실제 부분 붓기를 뜻하지 않는다 · 마감 9/16
- [x] `test/test_dosing.py`: ±tol 경계·3회 시도 상한·OVER 즉시 일탈·무효 계량·tare·`resolvable()` 및 G1 CSV 재현 검증 — 9/20 **10건 통과**. 허용 폭이 분해능보다 작으면 `resolvable=false`이며 `WeightReading.valid=false`와는 다른 판단이다 · 마감 9/17
- [x] G1 σ 로 `scale.min_resolvable_g` 갱신 — **9/18 3σ = 18.0 g → 19**, `max_std_g 5`, tool_force `offset_g 260.2`. CSV·`core/calib.py`·`config/scale_reference.yaml` 로 재현 가능 (PR #22, C 가 이어서) · 마감 9/17
- [x] **[G1 후속]** 판정 근거 확정 (Q-11) — **확정 (9/19, 조장 "제안 기준 타당")**: 합격 판정은 VERIFY ①(용기 순량 vs Σtarget), 스쿱 계량(`WEIGH_RESIDUAL`)은 초과 예방·붓기 비율용으로만. 코드 변경 없음 — 레시피 `tol_pct`·FSM 판정 위치가 이미 이 형태 · 마감 9/21
- [x] **[G1 후속·코드]** `scale.period_s=0.82` 파라미터를 MeasureForce·WeighHeld·WeighContainer 계량 경로에 연결. 유한한 양수 검사 및 표본 사이 0.1초 넛지 관측 분리, 관측값의 계량 통계 혼입 방지 검증. 9/20 서브에이전트 교차검토 후 관련 단위 테스트 **60건 통과**. `min_resolvable_g=19` 유지 · 마감 9/21
- [ ] **[G1 후속·실물/A·B]** 0.82초 설정으로 표본 중복·3σ·계량 소요시간·넛지 관측 확인 후 분해능 19→14 적용 판단. B의 `config/scale_reference.yaml` 및 보정 근거 갱신 인계. 장치 호출 중 관측 지연은 실물 확인 필요 · 마감 9/21
- [x] **[G1 후속]** `get_workpiece_weight` 경로 측정 — 9/19 두 경로 동시 측정. **tool_force 확정, workpiece 탈락** (reset 이 안 먹어 잔류 오차가 값을 지배). `common.yaml` method/gain/offset/max_std_g 반영, SOT D-07 · 마감 9/19
- [ ] 스쿱 1회 퍼올림량 실측 → `dosing.scoop_nominal_g` (보정 투입 fraction 계산 근거) · 마감 9/18
- [ ] 보정 계수: 실제 저울 vs 로봇 측정 다중 무게 → `scale.gain`/`scale.offset` — **9/19 두 점(32·132 g) 으로 gain 0.886·offset 247.1 반영**. 3점째(≈86 g)로 직선 확인만 남음 · 마감 9/21

## gmp_process [C 공정]
- [x] **[초과 반환]** `process_fsm.py`·`process_node.py`·`attempt.py`: 허용 스쿱량 초과 → `RETURN_MATERIAL` → 시도 한도 내 재스쿱, 반환 실패 시 중단. 반환 기록은 `delivered_g=0`·`valid=false`·outcome 5/6으로 분리하며 관련 단위 테스트 존재. 실제 통합 검증은 별도 · 마감 9/20
- [ ] **[관절 이송 통합]** FINISH 후 `_park()`가 `nudge_wait` AT를 요청하는 코드는 이미 존재. 놓기 후 passbox_done ABOVE 후퇴→넛지 이동 사용자 검증 및 폐기/기타 출발지의 보호 목적지 경로 등록 인계가 남음 · 마감 9/21
- [x] `core/recipe.py`: yaml → `RecipeSpec` 변환, 필수 필드·원료 중복·양수 검증 + `test_recipe.py` 25건 (`Recipe` msg 변환은 계층 원칙상 `nodes/` 가 한다 / **순서는 검증 대상이 아니다** — 계약 1절 "순서 위반은 일탈이 아니라 버그") · 마감 9/16
- [x] `core/process_fsm.py`: 상태·전이표(`docs/architecture.md`) 순수 구현, 이벤트 입력 → 다음 상태 + 스킬 요청 — **D-22 6단계(스쿱 계량 3회 + VERIFY)** 반영 · 마감 9/17
- [x] `test/test_process_fsm.py`: 정상 완주(스쿱 계량 순서), 붓기 전 초과 반환·재시도로 초과 예방, UNDER 보정 누적, OVER → QA → DISCARD(스쿱 반납 후), VERIFY 불일치 → QA, 계량 무효 재시도, 파지 실패, REFILL 재개 — 이후 초과 반환·NUDGE_WAIT 회귀 테스트 추가 · 마감 9/17
- [x] **[D-22]** `pour_fraction()` 도징 라이브러리 이관은 완료. **현 공정에서는 부분 붓기를 사용하지 않는다**: 허용량 초과 시 `RETURN_MATERIAL`, 허용 시 `fraction=1.0`. `weigh_scoop` 요청은 `WeighHeld` Action으로 연결 · 마감 9/18
- [x] **[D-22]** `VERIFY` 이중 판정 구현 — ① `|net − Σtarget| > Σ(target×tol)` → `BATCH_OUT_OF_SPEC` ② `|net − Σ투입량| > min_resolvable_g` → `VERIFY_MISMATCH`. 기존 30 g 설명은 측정 전 값이며, 현 설정은 19 g·데모 총 허용 폭은 22.5 g이다. G1 이후 ② 유지 결정은 아래 항목 참조 · 마감 9/18
- [x] **[D-22]** G1 결과로 **VERIFY ② 유효성 판단** — **확정**: `min_resolvable_g`(19) `< Σ(target×tol)`(22.5, 데모 레시피 200/150/100 g × 5 %) → **② 유지**. 표본 간격 조정 후 14 가 되어도 여전히 22.5 보다 작아 결론 불변. 코드는 이미 무조건 ②를 돌리므로 변경 없음 — `process_fsm.py` VERIFY 절에 근거 주석 추가 (SOT Q-11) · 마감 9/21
- [x] `nodes/process_node.py`: 스킬 Action 6 · Service 3 연동(`ReturnMaterial` 포함), `grade/scoop_id` 없음, `Pour`·`WeighContainer` station 인자 없음, `SetGripper`, `QaDecision.deviation_id` 일치 검증 + 판정 후 같은 ID 재발행, `scoop_cycle` 발행, 원료 → `scoop_N` 해석(`core/station_map.py`), 인터락 ENTER(`safe_pose` → PAUSED → EXIT 후 같은 요청 재시도), 스킬 실패 → `FORCE_LIMIT` 1회 재시도 후 ERROR. **완주 확인은 가짜 skill_node 로 했다** (`test/fake_skill_node.py`, 6건) — 진짜 가상 브링업은 아래 항목 · 마감 9/18
- [ ] **가상 브링업으로 레시피 1건 완주** — `ros2 launch gmp_bringup cell.launch.py mode:=virtual`에서 진짜 `skill_node` 상대 확인. **`WeighHeld`·`ReturnMaterial` 서버 및 공정 클라이언트 구현 완료**; 인터페이스·호출 패키지 재빌드 후 사용자 E2E 검증이 남았다. 반환 경로를 시험하려면 원료별 반환 좌표도 필요하다 · 마감 9/21
- [ ] **[I-008]** `ScoopCycle` 6축 wrench 를 채울 경로 결정 — 계량 스킬이 `WeightReading` 만 돌려줘서 지금은 `*_wrench_valid=false` 다. (a) `WeighHeld`/`WeighContainer` 결과에 wrench 6축 추가(제일 쌈) (b) `weights` 로 옮김 (c) 필드 삭제. `DispenseResult.verdict` 에 `INVALID` 가 없는 것도 같이 본다. **G1 으로 wrench 가 쓸모 있는지 본 뒤** — 그 전에 계약을 또 흔들지 않는다 · 마감 9/21 (조장과)
- [x] 일탈 카탈로그(`core/deviation.py`): kind 별 자동 복구 규칙(재시도 상한·보충 요청·QA 요청) — 11종은 이미 있었고 `WRONG_TOOL`(추가 1, v1.2) 이 빠져 있었다. `docs/process_flow.md` 정책표대로 `(0, QA, QA)` 로 추가, `Deviation.msg` kind 12종과 1:1인지 확인하는 assert + `test_deviation.py` 14건 추가. 폭 지문 검출 로직 자체는 별개(A 와, 마감 9/21) · 마감 9/21
- [x] 스테이션 물리 배치·테이프 표시 (하드웨어) — **9/18 완료**. 좌표 실측(G3)과 SOT D-24 등록이 이제 가능하다 · 마감 9/17
- [ ] 고의 장애 주입 T6 (a)(b)(c) 재현 · 마감 9/22
- [x] **[추가 7] NUDGE 전이**: `event` 구독 → 토글 → 루프 게이트. 인터락과 **게이트 하나**로 합쳤다 — 둘 다 걸리면 둘 다 풀려야 간다. 정지는 **그 자리에 서는 것**(안전 자세 아님)이고, 로봇 동작 요청 **앞**에서만 잡는다(`wait_qa`·`wait_interlock` 앞에서는 안 잡는다 — 판정을 못 받고 서 버린다). 테스트 6건. **9/19 리뷰 반영**: 세트 끝 `NUDGE_WAIT` — 반송(passbox_done·reject_bin) 뒤 `nudge_wait` 로 이동해 NUDGE 대기, 그 뒤 DONE/DISCARDED. 대기 중 주문 거부, 이벤트 `SET_DONE`/`SET_NEXT`. 테스트 +4 · 마감 9/18
- [ ] **[추가 7] NUDGE 실물 확인** — 가상은 `scale.simulated` 라 `skill_node` 가 `CellEvent(NUDGE)` 를 내지 않는다 (`safety.nudge_enabled and not scale.simulated`). 임계 8 N 검증과 함께 **G1 때** 한다 · 마감 9/21 (A 와)
- [ ] **[추가 1] 폭 지문**: `PICK_SCOOP`·`PICK_CONTAINER` 의 `SetGripper` 결과 폭이 원료별 기대 폭(±margin) 과 다르면 `Deviation(WRONG_TOOL)` → QA · 마감 9/21 (A 와)
- [ ] **[추가 3] 재기동 이어하기**: 기동 시 DB 의 미완료 배치 조회 → 상태·원료 인덱스·tare 복원 → 용기 재계량 후 재개. 시연: 실행 중 Ctrl-C → 재실행 · 마감 9/22 (D 와)
- [x] **[9/18 확정]** 배치 변경 반영 → **SOT D-24 로 등록 완료**. 판 450×450, 기준 원점 [X:0, Y:45], 로봇 좌측 28 cm, 중앙 300×300 배치 불가, 넛지 대기 위치 신설, 원료·스쿱은 판 바깥 아래. **스테이션 매핑 3건은 Q-12 로 분리** · 마감 9/21
- [x] **[Q-12]** Pass Box 가 `magazine`·`output_tray` 를 대체 — **확정 (9/18)**. `passbox_empty`(빈통) · `passbox_done`(완성품) 으로 이름과 FSM `carry` 목적지를 바꿨다 · 마감 9/21
- [x] **[D-24]** `nudge_wait` 스테이션 추가 — 세트 완료 후 이동해 NUDGE 대기. `safe` 와 별개 (판 우상단). **FSM 전이는 「추가 7 NUDGE 전이」 항목에서 같이** · 마감 9/21
- [ ] **[9/18 확정]** 회수·넛지 운영 — **세트=배치 1건, 반송 뒤 NUDGE_WAIT 전이는 구현 완료**. HMI 회수 확인 감사 기록도 구현됨. 남은 것은 `passbox_done.capacity`·`reject_bin.capacity` 확정, C 적재 카운터·만재 인터락·회수 확인 후 카운터 초기화 연결 · 마감 9/18
- [x] ~~[버그] 일탈 QA 대기가 무한 블로킹~~ — **문제 없음으로 결론 (9/18)**. D-23 반자동 운전이라 사람이 붙어 있는 것이 설계이고(세트 경계 대기도 마찬가지), 워커가 `daemon=True` 스레드라 Ctrl+C 로 정상 종료된다. 일탈 대기에만 타임아웃을 걸면 세트 경계 대기와 일관성이 깨진다 · 마감 9/18
- [ ] **Ctrl+C 종료 시 로봇 상태 확인** — 순응/힘제어가 켜진 채 남는지, 그리퍼가 스쿱을 쥔 채 멈추는지. `release_force`·`release_compliance_ctrl` 이 `dsr_arm.py` 짝 함수 안에만 있고 종료 경로에는 없다 → 재기동 시 `2.3501`(15Nm 외부 토크)·`2.1903` 가능. 남으면 **종료 훅에 `release_force` + `release_compliance_ctrl` 추가** (cancel 콜백보다 작고 Ctrl+C·cancel 둘 다 커버). `RunBatch` cancel 이 이것으로 대체 가능한지 같이 판단 · 마감 9/21

## gmp_hmi [D HMI·기록]
- [ ] **[v1.3 인계]** `static/hmi.js`의 `RETURN_MATERIAL` 상태명 및 `ScoopCycle` outcome 5/6 표시 연결. `record_node`는 숫자 outcome·원본을 저장하므로 DB 스키마 변경 없이 반환 기록 표시·조회 확인 · 마감 9/21
- [x] `config/schema.sql` · `core/db.py`: 6 테이블, 쓰기·조회·KPI·JSON 내보내기, 단위 테스트 · 마감 9/16
- [x] `nodes/record_node.py`: 구독 5종 → SQLite, 배치 종료 시 JSON 내보내기, `HMI_*` → audit · 마감 9/16
- [x] v1.2 적용: 주문 메시지의 `grade/scoop_id`, `QaDecision.Request`의 `batch_id` 제거(웹 표시는 유지), `scoop_cycle` 구독·DB 테이블·JSON 내보내기 추가 · 마감 9/18
- [x] `nodes/hmi_web_node.py` + `templates/index.html`: Flask 골격 — 주문·상태·계량 그래프·일탈 판정·인터락·이력·KPI·감사 추적 · 마감 9/16
- [ ] **[연동 대기 — 9/20 갱신]** 가상 모드에서 **주문 → 상태 → QA 승인 → 이력 조회 한 바퀴** — HMI 시험 공정 검증 이력은 있으나 실제 C·A 통합 완주는 별도다. `WeighHeld` 미구현이라는 기존 장애 설명은 해소됐으며, 현재 인터페이스 재빌드 후 사용자 E2E 확인 대기 · 마감 9/17
- [x] 다른 기기(폰·노트북)에서 HMI 접속 확인 — 9/18 휴대폰에서 `http://172.24.0.3:5002` 접속 성공 (ROS 통신 시험 서버) · 마감 9/18
- [x] process_node 가 `BATCH_START` 이벤트에 product 를 싣게 C 와 합의 — **C 측 구현 완료 (9/18)**. `CellEvent(code='BATCH_START', text=product, batch_id=...)` 로 나간다 · 마감 9/18
- [x] **[I-009]** `hmi_web_node.py` 의 `InterlockRequest.ENTER` 참조가 `AttributeError` 였다 — 계약 상수가 응답 절에 있었다. 상수를 요청 절로 옮기고 `InterlockRequest.Request.ENTER` 로 고쳤다 (9/18, C 가 처리) · 마감 9/18
- [x] **[9/18 구현]** 회수 확인 버튼 — QA가 패스박스·폐기함을 모두 비웠음을 확인한 뒤 `HMI_COLLECTION_CONFIRMED` 감사 기록(작업자·시각)을 남김. C 적재 카운터 초기화 연동은 별도 TODO · 마감 9/18
- [ ] 배치 중단 버튼 — `RunBatch` cancel 호출 + `HMI_*` audit (C 의 cancel 콜백과 짝) · 마감 9/21
- [ ] 계량 그래프 목표선·허용 오차 밴드 구현/확인. 배치 클릭 → `/batch/<id>` 상세 조회는 `static/hmi.js`·`hmi_web_node.py`에 이미 구현되어 있으며 실제 공정 기록으로 통합 확인은 남음 · 마감 9/21
- [ ] **[추가 3] 재기동 이어하기**: `db.py` 에 미완료 배치·마지막 상태 조회 API, `record_node` 가 상태 전이마다 저장 · 마감 9/21 (C 와)
- [ ] **[추가 7] HMI**: `static/hmi.js`에 NUDGE/REFILL 사유 표시·이벤트 조회 코드 존재. 실제 `CellState` 수신값과 표시 조건 연결 및 NUDGE 이벤트 타임라인 통합 검증 필요 — 화면 코드만으로 완료 처리하지 않는다 · 마감 9/19
- [ ] **[추가 5] 잔량 표시**: HMI 에 원료별 잔량 게이지 + "보충 권고" 배너 · 마감 9/22 (B 와)
- [ ] `tools/report.py`: DB 에서 계약 6절 지표 표 출력 (`/kpi` 와 같은 쿼리) · 마감 9/25
- [ ] 1분 영상 편집·PPT (조장과) · 마감 9/28

## gmp_bringup [조장]
- [ ] **[9/20 우선·문서 정합성]** `docs/SOT.md` D-08과 `docs/diagrams/process_flow.drawio`의 커밋된 충돌 표시 해소. `tools/make_process_drawio.py`와 산출물 대조·XML 파싱, SOT의 오래된 nudge 미구현 문구 및 `docs/setup.md`의 FINISH→DONE/C 후속 인계 설명을 현 NUDGE_WAIT 구현과 맞춤. **이번 todo 정리에서는 해당 파일을 수정하지 않음** · 마감 9/20
- [ ] **[G1 이슈 정합성]** `docs/issues.md` I-001의 “미측정” 설명을 9/18·19 측정 결과와 남은 판정/보정 후속으로 구분해 정리. 이번에는 측정 범위만 완료 처리하며 이슈 전체를 자동 종결하지 않음 · 마감 9/20
- [ ] **[관절 이송 설정]** `stations.yaml: transfers` 2개 경로는 관절각 입력 완료·`enabled=false`, `common.yaml` 관절 속도·가속도는 0. 사용자 경로 검증과 값 확정 후 활성화. 추가 보호 목적지 진입 경로는 별도 티칭·등록 필요 · 마감 9/21
- [x] `cell.launch.py` — 벤더 브링업 include + 우리 노드 4개 (ns `cell`), `mode`/`host`/`vel_scale` 인자 · 마감 9/16
- [x] `params/common.yaml` · `stations.yaml`(placeholder) · `recipes/demo_batch.yaml` · 마감 9/16
- [x] `tools/env.sh`: ROS2 → Doosan 언더레이 → 프로젝트 오버레이 순서 source, 상대경로·홈 폴백·언더레이 누락 검사 구현. 9/20 `bash -n tools/env.sh` 통과; 실제 노드 기동은 별도 항목 · 마감 9/16
- [ ] 가상 모드에서 4노드 기동 확인, `ros2 node list`/`rqt_graph` 캡처 · 마감 9/17
- [ ] `docs/demo_run_procedure.md` T1~T7 실물 검증 · 마감 9/23
- [x] **[9/18 확정]** `stations.yaml` 구조 개편 — `scoop_rack` 폐지, `scoop_1`~`scoop_4` 신설 (원료통 아래, `material_id` 짝). FSM 은 `material_id` 만 넘기고 `process_node` 가 짝을 찾는다. **현 BASE 좌표 반영 완료; 반환 시작·종료 자세 6개는 별도 미티칭** · 마감 9/18
- [ ] **[9/18 확정]** `common.yaml` 에 회수 용량 추가 — `passbox_done.capacity`·`reject_bin.capacity`, 도달 시 인터락 요청 · 마감 9/18
- [ ] **[정책 확인]** `qa.decision_timeout_s`·`interlock.timeout_s` 필요 여부를 D-23 무기한 QA 대기 결정 및 `docs/interfaces.md` §4와 대조. 키는 현재 없으며, 기존 TODO만 근거로 타임아웃을 추가하지 않는다. QA 대기 정책과 인터락 요청 응답 제한을 구분해 조장이 문서를 정리 · 마감 9/18
- [ ] BRD v1.0 · SDD v1.0 (`docs/spec/`) · 마감 9/28
