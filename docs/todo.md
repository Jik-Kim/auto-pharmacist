# Feature Todo — 파일·메서드 기준

> **동결 (2026-09-21).** 이 파일은 더 고치지 않는다. 할 일의 정본은 **[GitHub Issues](https://github.com/Jik-Kim/auto-pharmacist/issues)** 다 — 아래 항목 전부가 Issue 로 옮겨졌고 완료 항목은 Closed 다 (조장 승인, `AGENTS.md` 「이슈 · 진행」). 여기 남긴 것은 9/21 시점 스냅샷이며 체크박스·통계는 갱신하지 않는다.

각 줄 끝의 `마감 M/D` 는 실물 5일(9/17·18·21·22·23) 기준이다. 진행률은 아래 표가 체크박스를 세어 보여준다.
**이슈를 닫거나 상태를 바꿀 때는 여기 체크박스도 같이 본다.**

### 9/21 정리 · 오늘 우선순위

현재 체크아웃 `942d422`와 작업 중인 반환 경로·좌표 변경을 기준으로 코드·설정·이력을 대조했다. `[x]`는 각 항목에 적힌 구현 범위의 완료이며 통합·실물 검증 완료를 뜻하지 않는다. 기존 수정과 마감은 유지하며, 새 후속 항목의 마감은 작업 제안이다.

1. **실물 전 — A·B:** G2 기구 점검 후 운전 가능 여부 확인, 0.82초 계량 실측·3점 보정, 관절 이송 속도·가속도 확정 및 경로 검증. 관절 경로는 비활성이며 원료 A/B/C 반환 자세는 입력 완료다.
2. **스쿱 후속 — A·C:** 반환 끝 자세→재스쿱 연결과 `Scoop.depth_fraction`의 실제 스킬 적용·보정이 남아 있다. HMI 반환 상태·결과 표시는 구현 완료이며, 반환 끝 유지·RETURN 피드백 생략은 C/D 인계와 계약 설명 정정이 필요하다.
3. **안전 복구 — 조장·A·C·D:** C/D 구현과 PR #41의 이벤트 상관관계 보완을 반영했다. 실제 A/C/D 통합 검증과 계약 v1.6 제안 검토는 별도다. SOT의 C/D 구현 대기 설명 및 계약 상태와 최신 코드·interfaces 문서 사이의 차이는 조장 정리 대상으로 남긴다.
4. **사용자 통합 검증 — A·C·D:** 인터페이스·호출 패키지 재빌드 후 가상 브링업·HMI 연동, 이후 실물 확인. 가상 파지·계량 모델 부재로 레시피 완주는 미완료다. 운영 데모 레시피는 `9d2db8f`에서 삭제됐으므로 과거 데모 수치를 현재 운영 레시피로 간주하지 않는다.

**확인 범위:** 이번 갱신은 코드·기존 검증 기록·문서 대조와 TODO 통계 검사다. 기존 테스트 통과 기록은 당시 범위로 보존하며 이번에 재실행한 결과가 아니다. `docs/issues.md` I-001은 측정 완료와 실물 후속이 이미 구분되어 있으며 열림을 유지한다. 로봇 구동·가상 E2E·실물 검증은 수행하지 않았다.

<!-- STATS:BEGIN -->

**전체 63/106 완료** (████████████░░░░░░░░)  ·  기준 09/21

| 파트 | 완료 | 진행 | 지난 마감 |
|---|---|---|---|
| gmp_interfaces [조장] | 9/11 | `████████░░` | **1** |
| gmp_skills [A 스킬] | 12/27 | `████░░░░░░` | **7** |
| gmp_dosing [B 도징] | 8/11 | `███████░░░` | **1** |
| gmp_process [C 공정] | 17/27 | `██████░░░░` | **1** |
| gmp_hmi [D HMI·기록] | 11/18 | `██████░░░░` | **2** |
| gmp_bringup [조장] | 6/12 | `█████░░░░░` | **3** |

**마감이 지난 항목 15건**

- `9/17` gmp_interfaces — G1 결과로 도징 단위 확정 → RecipeItem.tol_pct 기본값·레시피 yaml 갱신 (SOT D-08)
- `9/18` gmp_skills — [추가 7] nudge 감지: 워커 유휴 루프·계량 settle·붓기 대기에서 get_tool_force 100 ms 폴링, 임…
- `9/18` gmp_skills — [추가 1] 폭 지문: 스쿱 손잡이 폭 A/B/C = 15.5/18/28 mm로 확정(9/19), stations.yaml/co…
- `9/17` gmp_skills — nodes/skill_node.py: 워커 스레드 + 큐와 Action/Service 연결 구현. WeighHeld·Scoop …
- `9/17` gmp_skills — G2 그리퍼 modbus 실물 확인 (Q-02) — 9/20 연결·빈 그리퍼 개폐·실제 busy/grip 전달·완료 폭 안정 6…
- `9/18` gmp_skills — Scoop: 컴플라이언스·Z 힘제어 접촉·깊이 상한·들어올림·해제 구현과 스테이션 선택 회귀 테스트 존재. 현재 실제 skill…
- `9/18` gmp_skills — [9/18 확정] 새 배치에서 용기를 파지한 채 passbox_done·reject_bin 도달 확인 — 현재 용기 AT Z=1…
- `9/18` gmp_skills — [9/18 확정] 원료 선반(판 바깥·높이 다름)에서 Scoop 힘제어 확인 — 뻗은 자세의 특이점 영향 (힘제어는 특이점 영역…
- `9/18` gmp_dosing — 스쿱 1회 퍼올림량 실측 → dosing.scoop_nominal_g (보정 투입 fraction 계산 근거)
- `9/18` gmp_process — [9/18 확정] 회수·넛지 운영 — 세트=배치 1건, 반송 뒤 NUDGE_WAIT 전이는 구현 완료. HMI 회수 확인 감사 …
- `9/17` gmp_hmi — [연동 대기 — 9/20 갱신] HMI 시험 공정에서 주문→상태→QA 승인/폐기→SQLite 이력·KPI까지 자동 검증 17항목…
- `9/19` gmp_hmi — [추가 7] HMI: static/hmi.js에 NUDGE/REFILL 사유 표시·이벤트 조회 코드 존재. 실제 CellStat…
- `9/17` gmp_bringup — 가상 모드에서 4노드 기동 확인, ros2 node list/rqt_graph 캡처
- `9/18` gmp_bringup — [9/18 확정] common.yaml 에 회수 용량 추가 — passbox_done.capacity·reject_bin.cap…
- `9/18` gmp_bringup — [정책 확인] qa.decision_timeout_s·interlock.timeout_s 필요 여부를 D-23 무기한 QA 대기…

**오늘 마감 22건**

- gmp_interfaces — [v1.4·v1.6 안전 복구 검토] RecoverSafety.srv·이벤트·A/C/D 구현은 존재하며 PR #41에서 요청 상…
- gmp_skills — [G2 기구 점검·실물 전 우선] 손가락–그리퍼 본체 연결부의 열림 시 달그락거림·체결 상태·좌우 유격을 강사/장비 담당자와 확…
- gmp_skills — [G2 방향별 보정 후보] 아래 실측표를 원시 근거로 보존. 기구 점검 후 운영 파지력을 고정하고 닫힘/열림별 구간 선형 보간 …
- gmp_skills — [안전 복구 실물] 벤더 자동 리셋·RobotError 수신·상태별 복구 전이·알람 중 재요청·복구 후 자동 동작 없음 확인. …
- gmp_skills — [반환 티칭·실물] stations.yaml 원료 A/B/C 시작 posx·끝 posx/posj 입력 완료(9/21). 동일 원…
- gmp_skills — [반환→재스쿱 연결] skill_node._do_scoop(): 반환 끝 자세에서 재스쿱으로 이어지는 경로를 스쿱 모션과 함께 …
- gmp_skills — 기동 자가진단: get_current_tool/tcp 불일치 기동 거부 구현 완료. collision_sensitivity ge…
- gmp_skills — [I-004 실물 검증] 이동 중 취소·시간 초과 시 감속/완전 정지, 후속 작업 차단, 인터락 ENTER 응답 지연 확인. 서…
- gmp_skills — [I-009] skill_node 의 approach == 0 숫자 비교를 MoveToStation.Goal.ABOVE 로 — …
- gmp_dosing — [G1 후속·실물/A·B] 0.82초 설정으로 표본 중복·3σ·계량 소요시간·넛지 관측 확인 후 분해능 19→14 적용 판단. …
- gmp_dosing — 보정 계수: 실제 저울 vs 로봇 측정 다중 무게 → scale.gain/scale.offset — 9/19 두 점(32·132…
- gmp_process — [안전 복구 A/C/D 통합 검증] 정상 복구·현장 조치 필요·복구 중 새 알람·지연/중복 결과·ERROR 배치 유지·주문 차단…
- gmp_process — [관절 이송 통합] FINISH 후 _park()가 nudge_wait AT를 요청하는 코드는 이미 존재. 놓기 후 passbo…
- gmp_process — 가상 브링업으로 레시피 1건 완주 — 9/20 전체 6패키지 재빌드, 격리 도메인에서 진짜 skill_node의 서버 6개·서비…
- gmp_process — [I-008] ScoopCycle 6축 wrench 를 채울 경로 결정 — 계량 스킬이 WeightReading 만 돌려줘서 지…
- gmp_process — [추가 7] NUDGE 실물 확인 — 가상은 scale.simulated 라 skill_node 가 CellEvent(NUDGE…
- gmp_process — [추가 1] 폭 지문: PICK_SCOOP·PICK_CONTAINER 의 SetGripper 결과 폭이 원료별 기대 폭(±mar…
- gmp_process — Ctrl+C 종료 시 로봇 상태 확인 — PR #30의 process 종료 SafePose 요청 반영. A 자체 종료 훅·워커 …
- gmp_hmi — 배치 중단 버튼 — RunBatch cancel 호출 + HMI_* audit (C 의 cancel 콜백과 짝)
- gmp_hmi — 계량 그래프 목표선·허용 오차 밴드 미구현. 주문 시 확정 레시피와 배치·계량 단계 문맥 보존부터 필요하며 기존 최근 계량값에 …
- gmp_hmi — [추가 3] 재기동 이어하기: db.py의 active_batch 조회는 존재하나 상태·원료 인덱스·tare 체크포인트 저장/조…
- gmp_bringup — [관절 이송 설정] stations.yaml: transfers 2개 경로는 관절각 입력 완료·enabled=false, com…

> 이 표는 `python3 tools/todo_stats.py` 가 체크박스를 세어 다시 쓴다. 손으로 고치지 않는다.

<!-- STATS:END -->

## gmp_interfaces [조장]
- [ ] **[v1.4·v1.6 안전 복구 검토]** `RecoverSafety.srv`·이벤트·A/C/D 구현은 존재하며 PR #41에서 요청 상관관계를 보완했다. v1.6은 문서상 미확정 제안이므로 영향 담당 검토·확정과 SOT의 기존 C/D 인계 설명 정리가 남음 · 마감 9/21
- [x] **[v1.5 스쿱 깊이 전달]** `Scoop.action`의 `depth_fraction` 필드와 C의 Goal 전달 구현. `docs/interfaces.md`에 9/20 팀 합의·A 승인 기록 존재. 실제 A 스킬 적용은 아래 미완료 항목이며, SOT의 확정 버전 설명과 차이는 조장 정리 필요 · 마감 9/21
- [x] **[v1.3 구현]** `ReturnMaterial.action`·CMake 등록, `ScoopCycle.RETURNED=5/RETURN_FAILED=6`, 전량 붓기·원료별 스쿱 계량 의미를 `docs/interfaces.md`와 함께 반영. 팀 공유 및 9/20 팀 승인 완료 확인은 SOT에 기록 · 마감 9/20
- [x] **[v1.2·v1.3 확정]** 9/20 사용자에게 팀 승인 완료 확인. interfaces·SOT 승인 상태 문구 정리, 실물 검증은 별도 유지 · 마감 9/21
- [x] **계약 v1.2**: `Deviation.kind` 에 `WRONG_TOOL`·`VERIFY_MISMATCH` 추가, **`WeighHeld` Action(들고 있는 스쿱 계량, D-22·I-007)** 신설, `docs/interfaces.md` 동시 갱신 — **PR #10 머지 완료 (9/18)** · 마감 9/17
- [x] 계약 v1.2 **팀 공유·승인** — 9/20 사용자 확인 (`WeighHeld` 신설·`Deviation.kind` 3종 추가 포함) · 마감 9/18
- [x] 계약 v1.0 — msg 8 · srv 6 · action 5 정의, `docs/interfaces.md` · 마감 9/16
- [ ] G1 결과로 도징 단위 확정 → `RecipeItem.tol_pct` 기본값·레시피 yaml 갱신 (SOT D-08) · 마감 9/17
- [x] 계약 v1.1 — `Deviation.operator_id`, `record_summary` 폐지, 7절 DB 스키마 · 마감 9/16
- [x] 계약 v1.2 변경 내용 확정 — 9/20 사용자 팀 승인 확인. I-004 실물 검증·Q-02 장치 확인은 A의 별도 검증 항목으로 유지 · 마감 9/18
- [x] **[D-22]** 계약 v1.2 에 `Deviation.kind BATCH_OUT_OF_SPEC` 추가 — `VERIFY` 를 **계측 신뢰성**(Σ투입량 대조)과 **제품 판정**(레시피 총량 대조)으로 나눈다. **조장 합의 완료 (9/17)**. I-007 과 같이 처리 · 마감 9/18

## gmp_skills [A 스킬]
- [ ] **[G2 기구 점검·실물 전 우선]** 손가락–그리퍼 본체 연결부의 열림 시 달그락거림·체결 상태·좌우 유격을 강사/장비 담당자와 확인. 동일 60 mm 명령에서 닫힘 실측 평균 51.0 mm, 열림 평균 55.9 mm로 방향 차이 4.9 mm. 점검 및 운전 가능 확인 전 추가 실물 구동·보정 적용 보류 · 마감 9/21
- [ ] **[G2 방향별 보정 후보]** 아래 실측표를 원시 근거로 보존. 기구 점검 후 운영 파지력을 고정하고 닫힘/열림별 구간 선형 보간 후보를 중간 목표에서 검증. 검증 범위 밖 외삽 금지, 실리콘 두께 이중 차감 금지. 보정값은 코드·설정에 미적용 · 마감 9/21
- [ ] **[안전 복구 실물]** 벤더 자동 리셋·RobotError 수신·상태별 복구 전이·알람 중 재요청·복구 후 자동 동작 없음 확인. 가상 모드 성공은 실물 검증으로 인정하지 않음 · 마감 9/21
- [x] **[HMI 안전 복구/A]** 기존 벤더 복구 서비스 호출, 상태 재확인, 안전 정지 차단·중복 요청 방지 구현. 서브에이전트 경합 재검토 및 gmp_skills 단위 테스트 198건 통과. C/D 연동은 각 담당 인계 항목으로 유지 · 마감 9/21
- [x] **[관절 이송 구현]** `core/transfer.py`·`skill_node._run_transfer()`의 직선 이탈→관절 이송, 출발·도착·파지 검증, 취소/실패 시 이력 무효화 구현 및 단위 테스트 존재. 가상은 기존 직선 이동, 실물은 비활성/미등록 경로 거부. **실물 검증·활성화는 미완료** · 마감 9/20
- [x] **[원료 반환 구현]** `skill_node._do_return_material()`·Action 서버: 원료별 시작 posx 직선→끝 posj 관절 이동 후 유지(9/21). 시작 복귀 제거, 잘못된/미입력 좌표 거부. 실물 확인은 별도 · 마감 9/20
- [ ] **[반환 티칭·실물]** `stations.yaml` 원료 A/B/C 시작 posx·끝 posx/posj 입력 완료(9/21). 동일 원료통 낙하·관절 이동 간섭·잔량 확인 대기. 사용자 검증 후 운용 · 마감 9/21
- [ ] **[반환→재스쿱 연결]** `skill_node._do_scoop()`: 반환 끝 자세에서 재스쿱으로 이어지는 경로를 스쿱 모션과 함께 구현·검증. C/D에 반환 끝 유지·RETURN 피드백 생략 인계, 팀 공유 후 계약 설명 정정 · 마감 9/21
- [ ] **[추가 7] nudge 감지**: 워커 유휴 루프·계량 settle·붓기 대기에서 `get_tool_force` 100 ms 폴링, 임계 초과 시 `CellEvent(NUDGE)` 발행. **코드·순수 테스트 완료(9/19), 남은 것은 G1에서 빈 그리퍼 정지 외력 σ로 임계 8 N 실물 검증 — 실물 보류** · 마감 9/18
- [ ] **[추가 1] 폭 지문**: 스쿱 손잡이 폭 **A/B/C = 15.5/18/28 mm**로 확정(9/19), `stations.yaml`/`common.yaml` 반영 완료. **남은 것은 스쿱 제작 상태 확인, `SetGripper.final_width_mm` 정밀도 실측, C의 WRONG_TOOL 판정 연동 — 실물 보류** · 마감 9/18
- [x] `adapters/dsr_arm.py`: `DR_init`·`set_tool/tcp`·`movej/movel`·`get_tool_force`·`reset/get_workpiece_weight`·힘제어 짝 함수 코드·단위 테스트 완료. **9/20 격리 도메인 71에서 가상 자가진단 후 `MoveToStation(safe)` HOMING→SUCCEEDED 확인** · 마감 9/16
- [x] `adapters/rg2_gripper.py`: `modbus` 백엔드 (`/onrobot/sendCommand` 폭 정수, `/onrobot_joint_states` → 폭 mm), `virtual` 백엔드 (rad 문자열), 폭 추론 구현·단위 테스트 완료. **실물 Modbus 검증은 G2에서 별도 보류** · 마감 9/17
- [ ] `nodes/skill_node.py`: 워커 스레드 + 큐와 Action/Service 연결 구현. `WeighHeld`·Scoop 스테이션 조회 결함은 코드·단위 테스트 완료, 9/20 가상 자가진단·`MoveToStation(safe)` 성공. **남은 진짜 가상 E2E는 가상 그리퍼가 목표 폭까지 닫혀 `grip_inferred=false`이고 가상 계량이 `valid=false`라 PICK_CONTAINER에서 차단됨(I-005)** · 마감 9/17
- [x] **G1 외력 분해능 측정 (I-001의 측정 범위)** — 9/18·19 실측 완료: 0.05 s 표본 조건 3σ=18.0 g → `min_resolvable_g=19`, 0.82 s 독립 표본 재측정 3σ=12.6/12.4 g. 근거 `gmp_dosing/calibration/`·`config/scale_reference.yaml`·SOT D-08. 넛지 임계 실물 검증·표본 간격 개선·3점 보정은 별도 미완료 항목이며 I-001 전체 해결을 뜻하지 않는다 · 마감 9/17
- [ ] **G2 그리퍼 modbus 실물 확인 (Q-02)** — 9/20 연결·빈 그리퍼 개폐·실제 busy/grip 전달·완료 폭 안정 6회 확인. 남은 항목: 물체 파지·실제 파지력·안전 스위치·캘리퍼 폭 및 방향/힘별 보정 검증. 전체 완료 아님 · 마감 9/17
- [x] **G3 `stations.yaml` 좌표 입력** — 실측 BASE 좌표 15곳(`workbench` 붓기 시작·끝, 원료 측정 바닥 접촉점 포함) 저장 및 YAML 파싱 확인 완료(9/19). **실제 접근 궤적·간섭 검증은 아래 실물 항목으로 보류** · 마감 9/17
- [ ] `Scoop`: 컴플라이언스·Z 힘제어 접촉·깊이 상한·들어올림·해제 구현과 스테이션 선택 회귀 테스트 존재. **현재 실제 skill_node에는 `depth_fraction` 전달·범위 검사·깊이 반영이 없으므로 이를 구현하고 Z 변환식·보정값을 실물 시험으로 확정해야 한다.** 반환 끝→재스쿱 연결은 별도 항목, G4 실물 검증도 미완료 · 마감 9/18
- [x] `Pour`: `workbench.pour_start_posx → pour_end_posx → pour_start_posx` 티칭 경로로 **전량 붓기(fraction=1.0)** 구현. 기존 부분 붓기·털어내기는 폐기했으며 초과 스쿱은 `ReturnMaterial`로 처리한다. 관련 단위 테스트 존재, 실물 궤적·낙하·간섭 검증은 보류 · 마감 9/18
- [x] `WeighContainer`: 파지 → 계량 자세 → `samples` 회 읽기 → 내려놓기 구현 완료. **실물 계량 검증은 보류** · 마감 9/18
- [ ] 기동 자가진단: `get_current_tool/tcp` 불일치 기동 거부 구현 완료. **`collision_sensitivity` getter 확인·비교는 미구현** · 마감 9/21
- [x] **[I-004 구현]** `dsr_arm.movej_cancellable()`·`movel_cancellable()`·`wait_motion_cancellable()`·`stop_motion()`: 비동기 이동 감시와 취소·시간 초과 시 `MoveStop(DR_SSTOP)` 호출 구현. `skill_node`의 취소 콜백 연결로 Scoop·Pour·ReturnMaterial·Weigh 내부 직선 이동에도 적용. 관련 단위 테스트 존재 · 마감 9/21
- [ ] **[I-004 실물 검증]** 이동 중 취소·시간 초과 시 감속/완전 정지, 후속 작업 차단, 인터락 `ENTER` 응답 지연 확인. 서비스 성공만으로 실제 정지를 판정하지 않으며 사용자 검증 후 이슈 종료 · 마감 9/21
- [x] `gripper_state` 10 Hz 발행, 미끄러짐 감지(`slip_mm`) 구현·단위 테스트 완료 · 마감 9/21
- [ ] **[I-009]** `skill_node` 의 `approach == 0` 숫자 비교를 `MoveToStation.Goal.ABOVE` 로 — 상수가 이제 Goal 절에 있어 쓸 수 있다 (급하지 않음) · 마감 9/21
- [x] **`WeighHeld`**: 들고 있는 스쿱을 대응 `material_N.posx`로 이동해 계량하고 그 자리에 머문다. `measure_posx`는 재고 접촉 자세이며 계량에 쓰지 않는다. 빈 그리퍼 실패·`LIFT/SETTLE/MEASURE`·`subject=scoop` 및 단위 테스트 구현. 첫 호출은 BASE +Y 150 mm 인출 → 실제 도착 자세에서 BASE +Z 100 mm 상승 → 원료 계량 자세 이동(9/21 원료통 충돌 후 사용자 승인 수정). 수정 경로의 실물 궤적·간섭 검증은 보류 · 마감 9/21
- [ ] **[9/18 확정]** 새 배치에서 **용기를 파지한 채** `passbox_done`·`reject_bin` 도달 확인 — 현재 용기 AT Z=100, ABOVE는 workbench +100 mm·passbox/reject +50 mm, EXIT는 각각 +200/+150 mm. 기존 60 mm 설명은 폐기. 관절 이송 비활성·보호 목적지 진입 제약과 실제 궤적·간섭을 함께 확인 · 마감 9/18
- [ ] **[9/18 확정]** 원료 선반(판 바깥·높이 다름)에서 `Scoop` 힘제어 확인 — 뻗은 자세의 특이점 영향 (힘제어는 특이점 영역에서 제한적) · 마감 9/18

## gmp_dosing [B 도징]
- [x] **[추가 5] 원료 잔량 추정**: `core/inventory.py`의 초기량−누적 투입량, 접촉 높이 기반 추정·보충 임계 판단 및 `test/test_inventory.py` 6개 테스트 구현(PR #37). 높이 보정값 실측과 운영 C/D 연동 완료를 뜻하지 않음 · 마감 9/21
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
- [x] **[안전 복구/C 구현]** `process_node.py`의 안전 이벤트 ERROR·주문 차단·안전 정지 시 FORCE_LIMIT 재시도 제외, `request_safety_recovery`→A 중계와 요청 필드 검사 구현. `core/safety_events.py`에서 요청 상관관계·과거 성공 이벤트 차단 보완(PR #41). 복구는 기존 배치 자동 재개가 아니며 명시적 새 주문 필요 · 마감 9/21
- [ ] **[안전 복구 A/C/D 통합 검증]** 정상 복구·현장 조치 필요·복구 중 새 알람·지연/중복 결과·ERROR 배치 유지·주문 차단/해제·감사 기록 확인. 관련 테스트·검증 스크립트는 존재하나 실제 연동 완료 근거는 별도 필요 (`docs/safety_recovery_pr_validation.md`) · 마감 9/21
- [x] **[v1.5 깊이 비율/C]** FSM의 초과 반환 후 재스쿱 비율 계산과 `Scoop.Goal.depth_fraction` 전달, 가짜 스킬의 비율 반영 구현. 실제 퍼올림량 제어는 A 스킬 적용·실측 후 확인 · 마감 9/21
- [x] **[초과 반환]** `process_fsm.py`·`process_node.py`·`attempt.py`: 허용 스쿱량 초과 → `RETURN_MATERIAL` → 시도 한도 내 재스쿱, 반환 실패 시 중단. 반환 기록은 `delivered_g=0`·`valid=false`·outcome 5/6으로 분리하며 관련 단위 테스트 존재. 실제 통합 검증은 별도 · 마감 9/20
- [ ] **[관절 이송 통합]** FINISH 후 `_park()`가 `nudge_wait` AT를 요청하는 코드는 이미 존재. 놓기 후 passbox_done ABOVE 후퇴→넛지 이동 사용자 검증 및 폐기/기타 출발지의 보호 목적지 경로 등록 인계가 남음 · 마감 9/21
- [x] `core/recipe.py`: yaml → `RecipeSpec` 변환, 필수 필드·원료 중복·양수 검증 + `test_recipe.py` 25건 (`Recipe` msg 변환은 계층 원칙상 `nodes/` 가 한다 / **순서는 검증 대상이 아니다** — 계약 1절 "순서 위반은 일탈이 아니라 버그") · 마감 9/16
- [x] `core/process_fsm.py`: 상태·전이표(`docs/architecture.md`) 순수 구현, 이벤트 입력 → 다음 상태 + 스킬 요청 — **D-22 6단계(스쿱 계량 3회 + VERIFY)** 반영 · 마감 9/17
- [x] `test/test_process_fsm.py`: 정상 완주(스쿱 계량 순서), 붓기 전 초과 반환·재시도로 초과 예방, UNDER 보정 누적, OVER → QA → DISCARD(스쿱 반납 후), VERIFY 불일치 → QA, 계량 무효 재시도, 파지 실패, REFILL 재개 — 이후 초과 반환·NUDGE_WAIT 회귀 테스트 추가 · 마감 9/17
- [x] **[D-22]** `pour_fraction()` 도징 라이브러리 이관은 완료. **현 공정에서는 부분 붓기를 사용하지 않는다**: 허용량 초과 시 `RETURN_MATERIAL`, 허용 시 `fraction=1.0`. `weigh_scoop` 요청은 `WeighHeld` Action으로 연결 · 마감 9/18
- [x] **[D-22]** `VERIFY` 이중 판정 구현 — ① `|net − Σtarget| > Σ(target×tol)` → `BATCH_OUT_OF_SPEC` ② `|net − Σ투입량| > min_resolvable_g` → `VERIFY_MISMATCH`. 기존 30 g 설명은 측정 전 값이며, 현 설정은 19 g·데모 총 허용 폭은 22.5 g이다. G1 이후 ② 유지 결정은 아래 항목 참조 · 마감 9/18
- [x] **[D-22]** G1 결과로 **VERIFY ② 유효성 판단** — **확정**: `min_resolvable_g`(19) `< Σ(target×tol)`(22.5, 데모 레시피 200/150/100 g × 5 %) → **② 유지**. 표본 간격 조정 후 14 가 되어도 여전히 22.5 보다 작아 결론 불변. 코드는 이미 무조건 ②를 돌리므로 변경 없음 — `process_fsm.py` VERIFY 절에 근거 주석 추가 (SOT Q-11) · 마감 9/21
- [x] `nodes/process_node.py`: 스킬 Action 6 · Service 4 연동(`ReturnMaterial` 포함), `grade/scoop_id` 없음, `Pour`·`WeighContainer` station 인자 없음, `SetGripper`, `QaDecision.deviation_id` 일치 검증 + 판정 후 같은 ID 재발행, `scoop_cycle` 발행, 원료 → `scoop_N` 해석(`core/station_map.py`), 인터락 ENTER(`safe_pose` → PAUSED → EXIT 후 같은 요청 재시도), 일반 스킬 실패 → `FORCE_LIMIT` 1회 재시도 후 ERROR, 안전 정지는 재시도 제외. **완주 확인은 가짜 skill_node 로 했다** (`test/fake_skill_node.py`, 6건) — 진짜 가상 브링업은 아래 항목 · 마감 9/18
- [ ] **가상 브링업으로 레시피 1건 완주** — 9/20 전체 6패키지 재빌드, 격리 도메인에서 진짜 `skill_node`의 서버 6개·서비스와 `process_node` 연결 및 가상 이동 확인. 데모 주문은 수락됐으나 가상 그리퍼의 물체 접촉 모델이 없어 `PICK_CONTAINER`의 `GRIP_FAIL` 4회 뒤 ERROR로 종료(I-005). 가상 계량도 `valid=false`이므로 완주용 가상 파지·계량 모델이 남음 · 마감 9/21
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
- [ ] **Ctrl+C 종료 시 로봇 상태 확인** — PR #30의 process 종료 SafePose 요청 반영. A 자체 종료 훅·워커 취소·정지/힘제어 해제 구현 완료(9/20), gmp_skills 단위 테스트 198건 통과. 실제 동시 SIGINT, 힘제어 잔류 및 재기동 확인은 사용자 실물 검증으로 유지. RunBatch cancel은 C 담당 보류 · 마감 9/21

## gmp_hmi [D HMI·기록]
- [x] **[안전 복구/D 구현]** 인증된 operator/admin의 단일 복구 버튼·`POST /recover`→C 중계, 작업자·요청 ID·현재 상태·조치 확인, 상태 안내·감사 기록 및 이벤트 상관관계 처리 구현(PR #41). 실제 A/C/D 검증은 C의 통합 항목에서 추적 · 마감 9/21
- [x] **[v1.3 인계]** `static/hmi.js`의 `RETURN_MATERIAL` 상태명 및 `ScoopCycle` outcome 5/6 표시 연결. 정적 계약 테스트를 추가했으며 `record_node`는 기존 숫자 outcome·원본 저장을 유지해 DB 스키마 변경 없음 · 마감 9/21
- [x] `config/schema.sql` · `core/db.py`: 6 테이블, 쓰기·조회·KPI·JSON 내보내기, 단위 테스트 · 마감 9/16
- [x] `nodes/record_node.py`: 구독 5종 → SQLite, 배치 종료 시 JSON 내보내기, `HMI_*` → audit · 마감 9/16
- [x] v1.2 적용: 주문 메시지의 `grade/scoop_id`, `QaDecision.Request`의 `batch_id` 제거(웹 표시는 유지), `scoop_cycle` 구독·DB 테이블·JSON 내보내기 추가 · 마감 9/18
- [x] `nodes/hmi_web_node.py` + `templates/index.html`: Flask 골격 — 주문·상태·계량 그래프·일탈 판정·인터락·이력·KPI·감사 추적 · 마감 9/16
- [ ] **[연동 대기 — 9/20 갱신]** HMI 시험 공정에서 주문→상태→QA 승인/폐기→SQLite 이력·KPI까지 자동 검증 **17항목 통과**. 실제 C·A 통합은 인터페이스 재빌드와 가상 이동까지 확인했으나 가상 파지·계량 모델 부재로 레시피 완주가 차단됨(I-005) · 마감 9/17
- [x] 다른 기기(폰·노트북)에서 HMI 접속 확인 — 9/18 휴대폰에서 `http://172.24.0.3:5002` 접속 성공 (ROS 통신 시험 서버) · 마감 9/18
- [x] process_node 가 `BATCH_START` 이벤트에 product 를 싣게 C 와 합의 — **C 측 구현 완료 (9/18)**. `CellEvent(code='BATCH_START', text=product, batch_id=...)` 로 나간다 · 마감 9/18
- [x] **[I-009]** `hmi_web_node.py` 의 `InterlockRequest.ENTER` 참조가 `AttributeError` 였다 — 계약 상수가 응답 절에 있었다. 상수를 요청 절로 옮기고 `InterlockRequest.Request.ENTER` 로 고쳤다 (9/18, C 가 처리) · 마감 9/18
- [x] **[9/18 구현]** 회수 확인 버튼 — QA가 패스박스·폐기함을 모두 비웠음을 확인한 뒤 `HMI_COLLECTION_CONFIRMED` 감사 기록(작업자·시각)을 남김. C 적재 카운터 초기화 연동은 별도 TODO · 마감 9/18
- [ ] 배치 중단 버튼 — `RunBatch` cancel 호출 + `HMI_*` audit (C 의 cancel 콜백과 짝) · 마감 9/21
- [ ] 계량 그래프 목표선·허용 오차 밴드 미구현. 주문 시 확정 레시피와 배치·계량 단계 문맥 보존부터 필요하며 기존 최근 계량값에 선택 레시피 목표를 바로 적용하지 않는다. `/batch/<id>` 상세 조회는 구현, 실제 공정 기록 통합 확인은 별도 · 마감 9/21
- [ ] **[추가 3] 재기동 이어하기**: `db.py`의 active_batch 조회는 존재하나 상태·원료 인덱스·tare 체크포인트 저장/조회는 미완료. `record_node` 기록과 C 복원 경로 연결 필요 · 마감 9/21 (C 와)
- [ ] **[추가 7] HMI**: `static/hmi.js`에 NUDGE/REFILL 사유 표시·이벤트 조회 코드 존재. 실제 `CellState` 수신값과 표시 조건 연결 및 NUDGE 이벤트 타임라인 통합 검증 필요 — 화면 코드만으로 완료 처리하지 않는다 · 마감 9/19
- [ ] **[추가 5] 잔량 표시**: 시험 공정의 잔량·보충·높이 UI는 구현. B `core/inventory.py`를 이용한 운영 C/D 재고 계약·누적 투입량/접촉 높이 전달·보충 권고 연동과 실물 높이 보정이 남음 (`gmp_hmi/docs/completion_audit.md`) · 마감 9/22 (B·C 와)
- [x] `ros2_ws/src/gmp_hmi/tools/report.py`: DB를 읽기 전용으로 열어 `/kpi`와 같은 `CellDB.kpis()`로 지표 표/JSON 출력, 필터 및 `test/test_report.py` 구현(`ff8fee4`) · 마감 9/25
- [ ] 1분 영상 편집·PPT (조장과) · 마감 9/28

## gmp_bringup [조장]
- [x] **[9/20 우선·문서 정합성]** `docs/SOT.md` D-08과 `docs/diagrams/process_flow.drawio`의 VERIFY 판정을 현재 코드와 일치시켰다. `tools/make_process_drawio.py`와 산출물 대조·XML 파싱 완료, SOT의 오래된 nudge 미구현 문구 및 `docs/setup.md`의 FINISH→DONE 설명을 현 NUDGE_WAIT 구현과 맞춤 · 마감 9/20
- [x] **[G1 이슈 정합성]** `docs/issues.md` I-001을 9/18·19 측정 결과, 0.82초 코드 반영 완료, 남은 실물 재측정·3점 보정으로 구분했다. 측정 범위만 완료 처리하며 이슈 전체는 열림 유지 · 마감 9/20
- [ ] **[관절 이송 설정]** `stations.yaml: transfers` 2개 경로는 관절각 입력 완료·`enabled=false`, `common.yaml` 관절 속도·가속도는 0. 사용자 경로 검증과 값 확정 후 활성화. 추가 보호 목적지 진입 경로는 별도 티칭·등록 필요 · 마감 9/21
- [x] `cell.launch.py` — 벤더 브링업 include + 우리 노드 4개 (ns `cell`), `mode`/`host`/`vel_scale` 인자 · 마감 9/16
- [x] `params/common.yaml`·`stations.yaml`·초기 데모 레시피 생성 완료. 이후 좌표 티칭 반영, `params/recipes/demo_batch.yaml`은 `9d2db8f`에서 운영 목록에서 삭제됨 · 마감 9/16
- [x] `tools/env.sh`: ROS2 → Doosan 언더레이 → 프로젝트 오버레이 순서 source, 상대경로·홈 폴백·언더레이 누락 검사 구현. 9/20 `bash -n tools/env.sh` 통과; 실제 노드 기동은 별도 항목 · 마감 9/16
- [ ] 가상 모드에서 4노드 기동 확인, `ros2 node list`/`rqt_graph` 캡처 · 마감 9/17
- [ ] `docs/demo_run_procedure.md` T1~T7 실물 검증 · 마감 9/23
- [x] **[9/18 확정]** `stations.yaml` 구조 개편 — `scoop_rack` 폐지, `scoop_1`~`scoop_4` 신설 (원료통 아래, `material_id` 짝). FSM 은 `material_id` 만 넘기고 `process_node` 가 짝을 찾는다. **현 BASE 좌표 반영 완료; 9/21 배치 변경 및 반환 시작·끝 자세 입력 완료, 실물 검증 대기** · 마감 9/18
- [ ] **[9/18 확정]** `common.yaml` 에 회수 용량 추가 — `passbox_done.capacity`·`reject_bin.capacity`, 도달 시 인터락 요청 · 마감 9/18
- [ ] **[정책 확인]** `qa.decision_timeout_s`·`interlock.timeout_s` 필요 여부를 D-23 무기한 QA 대기 결정 및 `docs/interfaces.md` §4와 대조. 키는 현재 없으며, 기존 TODO만 근거로 타임아웃을 추가하지 않는다. QA 대기 정책과 인터락 요청 응답 제한을 구분해 조장이 문서를 정리 · 마감 9/18
- [ ] BRD v1.0 · SDD v1.0 (`docs/spec/`) · 마감 9/28


### G2 캘리퍼 측정 기록 · 9/20 · 보정 미적용

실측은 **실리콘 골무 장착 상태의 안쪽 표면 사이 간격**이다. 사용자가 언급한 실리콘 두께 합계 2 mm는 이미 실측에 포함되므로 다시 빼지 않는다. 센서 열은 offset 포함 폭(`gwdf/10`), 명령 파지력은 40 N 조건이다. 장치 offset 2.0 mm, `ggwd−gwdf=4.0 mm`와 실리콘 두께는 서로 다른 정보다.

| 방향 | 명령 폭 mm | 센서 폭 mm | 사용자 실측 mm | 보정 후보용 대표 실측 mm |
|---|---:|---:|---|---:|
| 닫힘 | 40 | 37.7 | 30.8 | 30.8 |
| 닫힘 | 60 | 57.8 | 50.8 / 51.2 | 51.0 |
| 열림 | 60 | 61.9 | 55.8 / 56.0 | 55.9 |
| 열림 | 80 | 81.5 | 75.2 | 75.2 |
| 열림 | 100 | 100.9 | 94.0 | 94.0 |

- 임시 후보는 위 **명령→실측 대응점**을 방향별로 보간하고, 원하는 실제 간격에 대해 역으로 명령을 구하는 방식이다. 센서 표시 보정과 구동 명령 보정을 혼용하지 않는다.
- 닫힘 60 mm 반복 범위 0.4 mm, 열림 60 mm 반복 범위 0.2 mm는 각 2회 관측값일 뿐 정밀도 보증이 아니다.
- 열림 중 연결부 달그락거림 보고로 후보의 유효성은 미확정이다. 기구 점검 후 동일 조건 재측정이 먼저이며, 현재 자료로 자동 보정하지 않는다.
- 마지막 확인 상태: 목표 60 mm 닫힘, 센서 57.8 mm, 사용자 실측 51.2 mm. 이 문서 갱신에서는 실물 명령을 보내지 않았다.
