# gmp_process (C 파트) — 로직·입출력 흐름

> 담당 C 김병직. 계약은 `docs/interfaces.md` v1.3 (반환 Action·ScoopCycle 결과 추가) 기준이다. 이 문서는 **"무엇이 들어와서 무엇이 나가는지"** 를 현재 구조와 적용 목표 기준으로 그린 것이다. C/D 코드의 미적용 항목은 7절과 `docs/interfaces.md` 8절에 남긴다.
> 코드: `ros2_ws/src/gmp_process/gmp_process/` — `nodes/process_node.py` · `core/process_fsm.py` · `core/recipe.py` · `core/deviation.py` · `core/station_map.py` · `core/attempt.py`

## 0. 한 장 요약

```
                 HMI(hmi_web_node)                      record_node / HMI
   Service ──────────────┐                          ▲  Topic 6종
   submit_order          │                          │  state · weight · scoop_cycle · dispense_result · deviation · event
   qa_decision           ▼                          │
   interlock      ┌──────────────────────────────────┴──────┐
                  │  process_node  (ROS 껍데기, 스레드 2개)   │
                  │  ┌─ rclpy 콜백 스레드: 값 저장·Event 세움 │
                  │  └─ run_loop 스레드:                      │
                  │        req = fsm.start()                  │
                  │        while req:                         │
                  │           res = _execute(req)  ──────────┼──▶ skill_node 에 Action/Service 호출 (한 번에 하나)
                  │           req = fsm.on_result(req, res)   │◀── 결과
                  │           발행                            │
                  └──────────────┬────────────────────────────┘
                                 │ dict 요청 / dict 결과
                  ┌──────────────▼────────────────────────────┐
                  │  ProcessFSM (core, ROS 모름, pytest 대상)  │
                  │  상태 + 원료 인덱스 + tare + 일탈 카운터    │
                  │  decide() ← gmp_dosing   policy() ← deviation.py
                  └───────────────────────────────────────────┘
```

**원칙 3개**
1. **FSM 은 로봇을 모른다.** "다음에 이걸 해 달라" 는 dict 요청만 돌려주고, 결과 dict 를 받아 다음 상태를 정한다. 그래서 로봇 없이 pytest 로 전이표 전체를 검증한다 (`test/test_process_fsm.py`, 지금 5개).
2. **노드는 스킬을 한 번에 하나만 부른다.** 루프가 한 스레드라 구조적으로 두 스킬이 동시에 나가지 않는다. skill_node 쪽 DSR 워커 스레드(D-02)와 짝이 맞는다.
3. **콜백은 값만 저장한다.** QA 판정·인터락·NUDGE 는 콜백이 `threading.Event` 를 세우고, 루프가 `wait_*` 요청에서 기다린다. 콜백 안에서 로봇을 부르지 않는다.

## 1. 노드 입출력 (계약 v1.2, 9/18 확정)

모든 이름은 상대 이름이고 launch 가 `namespace:=cell` 을 붙인다 → 실제는 `/cell/...`.

### 들어오는 것 (process_node 가 서버)

| 종류 | 이름 | 타입 | 누가 | 언제 | 응답 규칙 |
|---|---|---|---|---|---|
| Service | `submit_order` | `SubmitOrder` (Recipe → accepted, batch_id) | HMI | 주문 버튼 | 실행 중(RUNNING/PAUSED/DEVIATION)이면 `accepted=false`. 수락하면 FSM 생성 + run_loop 스레드 시작, `batch_id` 발급 (`B-YYYYMMDD-NNN`) |
| Service | `qa_decision` | `QaDecision` (deviation_id, decision, operator_id) | HMI (셀 밖 QA) | DEVIATION 상태일 때 승인/폐기 | 대기 중인 `deviation_id`와 다르거나 DEVIATION이 아니면 거부. `operator_id`는 Deviation에 옮겨 재발행 |
| Service | `interlock` | `InterlockRequest` (ENTER=1 / EXIT=2, reason) | HMI | 사람 반입 전/후 | **ENTER**: 진행 중 스킬을 멈추고 `SafePose` 성공 후에야 `granted=true` (TODO). **EXIT**: `_interlock_exit.set()` → 루프 재개 |
| Action | `run_batch` | `RunBatch` (Recipe → result, feedback CellState + 마지막 DispenseResult) | HMI (`/order`) | `submit_order` 와 같은 접수 검사·실행 슬롯·FSM 을 공유하는 Action 경로 | **PR #163 (9/21) 구현.** 진행·최종 결과가 필요한 클라이언트용. Result: DONE 만 success, DISCARDED 는 SUCCEEDED+success=false, 취소 ABORTED(CANCELED), 안전정지·실패 ERROR. 취소는 진행 중 스킬 응답을 기다린 뒤 중단 — 즉시 정지 아님. 스킬 응답 유실 시 ERROR + 재기동 전까지 새 주문 차단 |
| Topic (구독) | `event` | `CellEvent` code=`NUDGE` | skill_node | 사람이 로봇을 건드림 (D-21) | 콜백은 토글만. 루프 게이트가 **다음 로봇 동작 전에** PAUSED 로 멈추고, 두 번째 NUDGE 로 재개 (추가 기능 7). `safety.nudge_enabled=false` 면 무시. 우리가 내는 event 도 같이 들어오므로 `code` 로 거른다 |

### 나가는 것 (process_node 가 발행) — record_node 가 전부 DB 에 쓰고, HMI 가 화면에 띄운다

| Topic | 타입 | QoS | 언제 | 무엇 |
|---|---|---|---|---|
| `state` | `CellState` | depth 1, **TRANSIENT_LOCAL** | 0.5 s 타이머 + 전이마다 | `mode`(IDLE/RUNNING/PAUSED/DEVIATION/ERROR/DONE) · `step`(FSM 상태 문자열) · `item_index` · `batch_id` · `station` · `note` |
| `weight` | `WeightReading` | 20 | `weigh` 결과 받을 때마다 | gross/tare/net/std/valid — TARE 도, 보정 재계량도 전부 |
| `scoop_cycle` | `ScoopCycle` | 50 | 정상은 `WEIGH_RESIDUAL` 후, 반환/실패는 확정 시점에 시도마다 | material_N 계량 3회, Scoop 접촉·삽입, Pour 명령, 반환 결과, 시도별 투입량 |
| `dispense_result` | `DispenseResult` | 50 | 원료 1종이 **끝날 때** (OK 또는 일탈로) | target/actual/error_pct/verdict/attempts/duration. UNDER 는 최종에 남지 않는다 |
| `deviation` | `Deviation` | depth 10, TRANSIENT_LOCAL | `_deviate()` 가 불릴 때마다 + QA 판정 후 `decision` 갱신해 **재발행** | kind·detail·requires_decision·decision·operator_id. 자동 복구된 것도 낸다 (지속성 근거) |
| `event` | `CellEvent` | 100 | 아래 코드 시점 | `BATCH_START`(product 포함 — D 와 합의) · `STEP`(전이) · `INTERLOCK_ENTER/EXIT` · `INTERVENTION_FORCED`(ERROR 진입) · `BATCH_END` |

### 부르는 것 (process_node 가 클라이언트, 상대는 전부 skill_node)

| 종류 | 이름 | 타입 | 보내는 것 | 받아서 쓰는 것 |
|---|---|---|---|---|
| Action | `move_to_station` | `MoveToStation` | `station_id`, `approach`(ABOVE=0/AT=1), `vel_scale` | `success`, `reached` → `state.station` |
| Action | `scoop` | `Scoop` | `material_id`, `attempt` | `success`, **`contact_detected`**, `max_contact_force_n`, `insertion_depth_mm` |
| Action | `pour` | `Pour` | `fraction`(0~1) | `success` — 목적지는 skill 설정의 고정 `workbench` |
| Action | `weigh_container` | `WeighContainer` | `tare_g` | **`reading`**(WeightReading: gross/net/std/valid) — 고정 `workbench`의 **용기를 들어** 잰다 (파지 → 계량 자세 → 읽기 → 내려놓기), 그리퍼가 비어 있어야 한다 |
| Action | **`weigh_held`** (v1.2) | `WeighHeld` | `tare_g`(빈 스쿱) | **`reading`** — **들고 있는 스쿱을 그대로** 계량 자세로 가져가 잰다. 파지·내려놓기 없음, 계량 후 그 자세에 머문다. 빈 그리퍼면 `success=false` (I-007 해소) |
| Action | **`return_material`** (v1.3) | `ReturnMaterial` | `material_id` | 초과 원료를 원료통으로 반환한다. 원료별 `return_start_posx`·`return_end_posj` 가 없으면 이동하지 않고 실패한다. 끝 자세에서 종료, `RETURN` 피드백 없음 (v1.5.1). |
| Service | `set_gripper` | `SetGripper` | `close`, `width_mm`, `force_n`, `timeout_s` | `success`, `final_width_mm`, **`grip_inferred`** |
| Service | `measure_force` | `MeasureForce` | `samples`, `settle_s` | `force[6]`(힘+모멘트), `fz_mean_n`, `fz_std_n`, `valid` |
| Service | `safe_pose` | `SafePose` | `reason` | `success` |

## 2. FSM 요청 ↔ 스킬 호출 (`_dispatch` 의 번역표)

FSM 이 돌려주는 요청은 `{'kind': ..., ...}` 하나. 노드는 kind 별로 아래처럼 스킬을 부르고 **결과 dict** 로 돌려준다. 결과 키는 FSM 이 읽는 것만 채우면 된다.

| kind | 요청 필드 | 노드가 부르는 것 | 돌려줄 결과 dict |
|---|---|---|---|
| `measure` | — | `measure_force(scale.samples, scale.settle_s)` | `{'valid', 'fz_mean_n', 'fz_std_n'}`. **SELF_CHECK 는 `valid` 로 막지 않는다** — 가상은 `scale.simulated` 라 항상 `valid=false`(`'simulated'`)로 온다. 여기서 보는 것은 「스킬이 응답하는가」뿐이고, 응답이 없으면 아래 FORCE_LIMIT 경로로 간다 |
| `carry` | `src`, `dst`, `slot`, `target`='cup' | `move(src, ABOVE)` → `move(src, AT)` → `set_gripper(close, cup_width)` → `move(src, ABOVE)` → `move(dst, ABOVE)` → `move(dst, AT)` → `set_gripper(open)` → `move(dst, ABOVE)` | `{'grip_inferred': <close 의 결과>}` — 파지 실패면 dst 로 가지 말고 바로 반환 |
| `weigh` | `station`, `tare_g` | `weigh_container(tare_g)` — 내부 `station`은 상태 추적용이며 Action 목적지는 고정 `workbench` (TARE · VERIFY 두 곳) | `{'gross_g', 'net_g', 'std_g', 'valid'}` ← `reading` 에서 복사. 동시에 `weight` 토픽 발행 |
| `weigh_scoop` | `station`, `material_id`, `tare_g`(빈 스쿱) | `weigh_held(tare_g)` — **해당 `material_N.posx`에서** 들고 있는 스쿱을 계량 (SCOOP_TARE · WEIGH_SCOOP · WEIGH_RESIDUAL) | `{'gross_g', 'net_g', 'std_g', 'valid'}` + `weight` 발행 (`station=material_N`, `subject='scoop'`) |
| `move` | `station`, `approach`('AT'/'ABOVE') | `move_to_station(station_id, approach)` — `station='scoop'` 이면 `material_id` 로 `stations.yaml` 의 `scoop_N` 을 찾아 넣는다 (`core/station_map.py`, D-24) | `{'success', 'reached'}` — `reached` 는 `CellState.station` 이 된다 |
| `grip` | `close`, `target`('scoop'/'cup') | `set_gripper(close, width=scoop_width 또는 cup_width, force)` | `{'grip_inferred', 'final_width_mm'}` |
| `scoop` | `material_id`, `attempt`, `fraction` | `scoop(material_id, attempt, depth_fraction=fraction)` — 계약 v1.5: `fraction` 이 담그기 깊이 비율 [`min_fraction`, 1.0] 로 전달된다. `attempt` 는 기록용. A는 접촉 스쿱 끝 WORLD 높이와 기준 깊이×fraction으로 TW spline Z를 보정한다. 원료 A 보정 미확인 상태에서는 이동 전 거부하며 B/C는 미티칭 | `{'contact_detected', 'max_contact_force_n', 'insertion_depth_mm'}` — 뒤 둘은 FSM 이 안 쓰고 `ScoopCycle` 에 실린다. WEIGH_SCOOP 은 퍼낸 양으로 **전량 붓기와 원료통 반환**을 가른다 — 부분 붓기는 v1.3 으로 폐기됐고, 양 조절은 이 `fraction`(담그기 깊이)이 한다 |
| `pour` | `station`, `fraction=1` | `pour(1)` — `workbench_pour_start`에서 `workbench_pour_end`까지 전량 붓는다. | `{'success'}` |
| `return_material` | `material_id` | `return_material(material_id)` — `return_start_posx`·`return_end_posj` 가 없으면 실패 | `{'success', 'message'}` |
| `safe` | `reason`, `then` | `safe_pose(reason)` | `{'success'}` — 전이는 요청의 `then` 이 정한다 |
| `wait_qa` | `deviation` | 아무 스킬도 안 부름. `_qa.wait()` | `{'decision': 'APPROVED'/'DISCARDED', 'operator_id'}` |
| `wait_interlock` | — | `_interlock_exit.wait()` | `{}` |

슬롯 오프셋(carry): `stations.yaml` 의 `slot_pitch_mm × slot` 를 y 에 더한 좌표. **Pass Box 두 칸은 지금 `slots: 1` 이라 오프셋이 0 이다** (D-24 — 1통마다 QA 가 회수, D-23). 여러 통을 쌓게 되면 그때 pitch 를 준다 — 이 계산은 **skill_node 의 StationTable** 이 하고, process 는 `slot` 번호만 넘긴다 (A 와 인터페이스 확인 필요: `MoveToStation` 에 slot 필드가 없다 → `station_id="passbox_empty#1"` 식 접미사로 합의하거나 계약 v1.2 에 `uint8 slot` 추가).

## 3. 상태 전이도

그림판: `docs/diagrams/process_flow.drawio` (draw.io, `tools/make_process_drawio.py` 가 생성) — 1 노드 입출력 · 2 상태 전이도 · 3 요청↔스킬 번역 · **4 상태별 노드·토픽 흐름 (A·B·D 와 무엇이 오가는지 상태마다)**.

원료 1종은 빈 스쿱 계량 → 퍼올림 → 붓기 전 계량(원료별 `material_N.posx`) → 초과면 원료통 반환 후 재시도 → 전량 붓기 → 붓기 후 계량(잔량 → 투입량) → 판정이다. 용기 계량은 배치 끝 VERIFY 한 번이다.

```
 IDLE ──submit_order──▶ SELF_CHECK ──measure ok──▶ PICK_CONTAINER ──carry ok──▶ TARE ──measure·weigh──▶ ┐
                            │ measure invalid                │ grip_inferred=false                      │
                            ▼                                ▼ GRIP_FAIL ×3 → 재시도                   │
                          ERROR                              ×4 → ERROR                                 │
                                                                                                        │
   ┌──────────────────────────────── 원료 i (D-22 6단계) ───────────────────────────────────────────────┘
   │
   ▼
 PICK_SCOOP ──grip ok──▶ SCOOP_TARE ──weigh_scoop──▶ SCOOP ──contact──▶ WEIGH_SCOOP ──weigh_scoop──▶ POUR ──▶ WEIGH_RESIDUAL
   │ GRIP_FAIL ×3           (빈 스쿱 무게)             ▲  │ SCOOP_EMPTY ×3   (퍼낸 양 →                 (fraction=1.0)  │ (잔량 → 투입량 누적
   ▼ ×4 ERROR                                          │  ▼ 연속 3회 = MATERIAL_EMPTY  전량 붓기 / 원료통 반환)            │  → decide)
                                                       │ PAUSED ──interlock EXIT──▶ _resume(scoop)                    ├─ OK ────────▶ RETURN_SCOOP
                                                       └──────────────── UNDER, attempts<8 → scoop(attempt+1) ◀───────┤                 │ 다음 원료 → PICK_SCOOP
                                                                                                                      ├─ OVER ──────▶ RETURN_MATERIAL(material_id) → SCOOP 재시도
   계량 무효(valid=false): 각 계량 상태에서 같은 요청 재시도 ≤2, 3회째 WEIGH_INVALID → DEVIATION                       └─ 9회째 UNDER ▶ DEVIATION(TIMEOUT)

 RETURN_SCOOP ──마지막 원료였음──▶ VERIFY ──move(workbench ABOVE) · measure(영점 재확인) · weigh(용기를 들어) · ① 규격 OK──▶ FINISH ──carry(workbench→passbox_done)──▶ NUDGE_WAIT ──move(nudge_wait) · wait_nudge(사람이 건드림)──▶ DONE
                                     (폐기도 같다: DISCARDED ──carry(→reject_bin)──▶ NUDGE_WAIT ──▶ DISCARDED)
                                     └─ ① |net − Σtarget| > Σ(target×tol) ─▶ DEVIATION(BATCH_OUT_OF_SPEC) ──APPROVED──▶ FINISH

 DEVIATION ──wait_qa──▶ APPROVED ──▶ RETURN_SCOOP (원료는 결과 목록에 남김) · VERIFY 에서 왔으면 FINISH
                      └▶ DISCARDED ─▶ (스쿱 든 채면 move(scoop_N)·grip(open) 먼저) ─▶ carry(workbench→reject_bin) ──▶ DISCARDED(종료)
```

**PAUSED 는 두 경로**: (a) MATERIAL_EMPTY → `safe` → `wait_interlock` → EXIT 로 `_resume` 요청 재실행 (b) 사람 접촉 NUDGE → 노드 게이트가 다음 요청 전에 멈춤 → 두 번째 NUDGE 로 재개 (D-21, FSM 은 모름).
**ERROR** 진입 = `event(INTERVENTION_FORCED)` 발행 — MTBI 분모. 그 뒤 `safe(then=None)` 로 종료.

## 4. 상태별 상세 — 들어오는 결과 / 판단 / 나가는 요청 / 발행

| 상태 | 받는 결과 (이전 요청) | 판단 | 다음 요청 | 이 시점 발행 |
|---|---|---|---|---|
| `SELF_CHECK` | `measure` → valid, fz_std | valid 아니면 ERROR (TODO). 툴·TCP·감도 확인은 skill_node 가 기동 시 함 | `carry(passbox_empty→workbench, slot)` | `event BATCH_START`, `state` |
| `PICK_CONTAINER` | `carry` → grip_inferred | false → `_deviate(GRIP_FAIL)` (같은 carry 재시도). **[추가 1]** `final_width_mm` 가 기대 약통 폭(`gripper.cup_width_mm`) ±margin 밖이면 `WRONG_TOOL` → QA (승인 시 TARE 로 이어감, 거부 시 DISCARDED) | `weigh(workbench, tare 0)` | `deviation` (실패 시) |
| `TARE` | `measure` → fz_mean_n(영점 기준), 그다음 `weigh` → gross, valid | `carry` 가 workbench ABOVE·그리퍼 열림으로 끝나므로 그 자리에서 **빈 그리퍼 영점**을 잡는다(`zero_fz_n`) — VERIFY 직전과 같은 자세여야 비교가 성립한다. `tare_g = gross` (빈 용기, 배치마다 1회). cur = 원료 0. 무효 ≤2 재계량 → 3회 WEIGH_INVALID | `move(scoop_N, AT)` | `weight` |
| `PICK_SCOOP` | `move` → 도착 / `grip` → inferred | 파지 실패 → GRIP_FAIL 재시도(≤3) → 4회 FORCED → ERROR. **[추가 1]** `final_width_mm` 가 원료 기대 폭 ±margin 밖이면 `WRONG_TOOL` → QA (승인 시 그 스쿱으로 SCOOP_TARE 이어감 — 원료를 건너뛰지 않는다, 거부 시 스쿱 반납 후 DISCARDED. A 리뷰 정정, PR #165) | `grip(close, scoop)` → `weigh_scoop(tare 0)` | `deviation` |
| `SCOOP_TARE` | `weigh_scoop` → gross, valid | `scoop_tare_g = gross` (빈 스쿱, 원료마다 1회). 무효 ≤2 재계량 → 3회째 `WEIGH_INVALID` → **`CLEANUP` → `ERROR`** (QA 아님 — 투입 전이라 되돌린다, #213 결정 3) | `scoop(material, attempt=1)` | `weight` |
| `SCOOP` | `scoop` → contact_detected | false → SCOOP_EMPTY 재시도(≤3) → 4회째 REFILL → PAUSED. **담그기 깊이(`fraction`)는 첫 사이클부터 목표량을 반영한다** — `max(min_fraction, min(1, 남은 목표 ÷ scoop_nominal_g))`, 둘째 사이클부터는 `decide()` 가 같은 식으로 정한다 (#221). 종전 첫 사이클 1.0 고정은 목표가 1회량보다 작을 때 곧장 초과 반환이 났다 | `weigh_scoop(tare=scoop_tare)` | `deviation` |
| `WEIGH_SCOOP` | `weigh_scoop(material_N)` → gross, valid | `scooped = gross − scoop_tare`. `scooped > max(0, target − actual) + target×tol_pct/100`이면 초과다. 반환 전에는 `actual`을 증가시키지 않는다. 무효 ≤2 재계량 → 3회째 `WEIGH_INVALID` → **`CLEANUP` → `ERROR`** (원료를 먼저 반환한다, #213 결정 3) | 초과→`return_material(material_id)` · 이하→`pour(fraction=1)` | `weight` |
| `RETURN_MATERIAL` | `return_material` → success | 반환 좌표가 없거나 Action이 실패하면 `safe` 후 즉시 `ERROR`로 종료한다. 성공이고 returns<max면 같은 원료를 다시 `SCOOP`한다 (깊이 = 직전 fraction × 남은량/퍼낸 양, 하한 `min_fraction`). **실물 주의 (v1.5.1, 9/21): 반환 끝→재스쿱 연결 경로가 없어(#64) 실물 `skill_node` 가 반환 중 세운 플래그로 후속 `scoop` 을 전부 거부한다 → `SKILL_FAIL`. 플래그는 풀리지 않아 재시도해도 같은 이유로 거부되므로, 재시도 없이 `FORCE_LIMIT`(FORCED) **1건**에 사유(`반환 후 재스쿱 차단 — …`)를 남기고 `safe → ERROR` 로 끝낸다. fake 스킬은 `block_rescoop` 으로 같은 동작을 흉내 낸다.** 한도 초과면 `TIMEOUT` 일탈이다. 반환은 약통에 아무것도 넣지 않았으므로 붓기 시도(`attempts`)를 소모하지 않고 별도 `returns`로 센다. 재스쿱은 `직전 fraction × (남은 목표량 / 방금 잰 초과 스쿱량)` 으로 더 얕은 깊이를 요청한다 (하한 `min_fraction`). | 성공→`scoop(같은 attempt, 더 얕은 fraction)` · 실패/미티칭→`safe → ERROR` | `scoop_cycle(RETURNED/RETURN_FAILED, delivered=0, valid=false)` |
| `POUR` | `pour(fraction=1)` | workbench의 pour start→end 전량 이동 | `weigh_scoop(material_N)` | — |
| `WEIGH_RESIDUAL` | `weigh_scoop` → gross, valid | `residual = gross − scoop_tare`, **`actual += scooped − residual`** (실제 투입량 누적). `decide(target, actual, tol, attempts, True, invalid, cfg)` → DONE / SCOOP(fraction 힌트) / DEVIATION(kind). 무효 ≤2 재계량 → 3회째 `WEIGH_INVALID` → **QA** — 이미 부은 뒤라 되돌릴 게 없다. 승인하면 그 사이클을 누산하지 않고 `unmeasured` 로 세고 배치를 잇는다 (#213 결정 3) | DONE→`move(scoop_N)` · SCOOP→`scoop(attempt+1)` · DEVIATION→`wait_qa` | `weight` · **`scoop_cycle`** · `dispense_result` (DONE·DEVIATION 시) · `deviation` |
| `RETURN_SCOOP` | `move` / `grip(open)` | idx+1. 남았으면 다음 원료, 없으면 VERIFY | `move(scoop_N)` → `grip(open)` → `move(scoop_N)`(다음) 또는 `weigh(workbench, tare)` | `state` |
| `VERIFY` | `move(workbench, ABOVE)` → `measure` → `weigh` → net, valid | **TARE 때와 같은 자세로 옮긴 뒤** 빈 그리퍼 영점을 다시 잰다 (`tool_force` 는 자세 의존이라 다른 자세끼리 비교하면 자세 차이가 영점 이동으로 둔갑한다). TARE 의 `zero_fz_n` 과 대조해 `\|이동\| > scale.zero_drift_limit_n`(기본 0.1 N — 같은 자세 반복 산포 기준, 0 이면 끔)이면 재측정, `max_invalid_retries` 도달 시 `WEIGH_INVALID` → QA. NUDGE 는 정지·재개 장치일 뿐 계량 유효성과 연결돼 있지 않아 오염된 값이 그냥 장부에 들어가던 구멍을 막는다. 통과하면 **용기를 들어** 순량 계량 (그리퍼 비어 있음). **판정은 ① 하나뿐이다** — `\|net − Σspec.target\| > Σ(target×tol)` → BATCH_OUT_OF_SPEC → QA(폐기 권고). 종전 ②(`\|net − Σresults.actual\|`)는 **9/22 폐지** — 값은 `verify_detail` 에 관측으로만 남기고 영점 이동량과 함께 `CellEvent(INFO, VERIFY)` 로 발행한다. 무효 ≤2 재계량 | `carry(workbench→passbox_done)` | `weight` · `deviation` |
| `FINISH` | `carry` → grip_inferred | 실패 → GRIP_FAIL 재시도 | `move(nudge_wait)` | — |
| `NUDGE_WAIT` | `move(nudge_wait)` → 도착 · `wait_nudge` → 사람이 건드림 | **세트 경계 (D-23)** — 반송 뒤 nudge_wait 로 물러나 서서 기다린다. 이동 중 RUNNING, 대기 중 **PAUSED**(주문 거부 · HMI 는 note 로 사유). `safety.nudge_enabled=false` 면 대기 없이 통과. 이 대기의 NUDGE 는 정지 토글이 아니라 「다음 세트」 신호 | `wait_nudge` → None (끝: DONE 또는 DISCARDED) | `event SET_DONE`(대기 진입) · `SET_NEXT`(건드림) · `BATCH_END`, `state DONE` |
| `CLEANUP` | `return_material` → success · `move` · `grip` | **투입 전 계량 무효의 정리 경로** (#213). `TARE` → 정리 없음 · `SCOOP_TARE` → `move(material_N,AT)` → `move(scoop_N,AT)` → `grip(open)` · `WEIGH_SCOOP` → `return_material` 을 맨 앞에. 빈 스쿱을 원료통에 기울이지 않으므로 `SCOOP_TARE` 에는 반환이 없다. 재스쿱 없음(#64). 정리 중 반환 실패는 `FORCE_LIMIT`(FORCED, step `CLEANUP`) 별건 | 남은 요청이 없으면 `safe(then=None)` → `ERROR` | `deviation`(WEIGH_INVALID/FORCED, 정리 **시작 전** 기록) |
| `DEVIATION` | `wait_qa` → decision | APPROVED → (원료 일탈) 결과에 남기고 RETURN_SCOOP / (VERIFY) FINISH. DISCARDED → 스쿱 든 채면 먼저 반납 → 용기째 폐기 | `move(scoop_N)` / `carry(workbench→passbox_done)` / `carry(workbench→reject_bin)` | `deviation` 재발행(decision·operator_id 채움) |
| `PAUSED` | `wait_interlock` → {} | `_resume` 요청을 그대로 다시 실행 | `_resume` | `event INTERLOCK_ENTER/EXIT`, `state PAUSED` |
| `ERROR` | `safe(then=None)` | 종료 | None | `event INTERVENTION_FORCED`, `state ERROR` |
| `DISCARDED` | `move` / `grip(open)` / `carry` | 스쿱 반납 후 용기 폐기 → 폐기도 세트의 끝이라 `NUDGE_WAIT` 로 | `grip(open)` → `carry(workbench→reject_bin)` → `move(nudge_wait)` | 종료 상태는 `DISCARDED` 로 남는다 (record_node 가 본다) |

## 5. 일탈 정책표 (`core/deviation.py` — 바꾸려면 여기만)

`policy(kind, count)` → `(action, requires_decision)`. **count 는 같은 배치·같은 스텝·같은 kind 의 몇 번째인가** (`_counts[(idx, step, kind)]`).

| kind | limit | ≤ limit | > limit | 뜻 |
|---|---|---|---|---|
| GRIP_FAIL | 3 | RETRY | FORCED | 파지 3번 실패 → 4번째에 사람 |
| SLIP | 2 | RETRY | FORCED | |
| SCOOP_EMPTY | 3 | RETRY | REFILL | 3번 빈 스쿱 → 4번째에 보충 인터락 |
| MATERIAL_EMPTY | 0 | REFILL | REFILL | 즉시 보충 |
| OVERFILL | 0 | QA | QA | 약통에 실제 과다 투입된 경우의 일탈. 사전 스쿱 초과 판정(`RETURN_MATERIAL`)과 별개 |
| TIMEOUT | 0 | QA | QA | 보정 3회 후에도 미달 |
| WEIGH_INVALID | 0 | QA | QA | 재계량은 `max_invalid_retries` 가 전담한다 — 이 표에 온 것은 이미 그 상한을 넘긴 뒤라 즉시 처분이다 (#213 결정 1). 투입 전 단계는 `_cleanup_then_error` 가 FORCED 로 직접 기록해 이 표를 타지 않으므로, 여기 오는 것은 `WEIGH_RESIDUAL`·`VERIFY` 뿐이다 |
| SAFETY_SWITCH · FORCE_LIMIT | 1 | RETRY | FORCED | |
| ~~**VERIFY_MISMATCH**~~ [D-22, v1.2] | — | — | — | **9/22 폐지 (사용자·조장 확정) — 발생하지 않는다.** `Deviation.kind` 열거값은 계약이라 남지만 FSM 이 내지 않는다. 끈다는 것은 **배치 기록 교차검증을 포기한다**는 뜻이다 — 제품은 규격 안인데 원료별 투입 기록이 틀린 배치를 검출할 수단이 없다 |
| **BATCH_OUT_OF_SPEC** [D-22, v1.2] | 0 | QA | QA | 배치 끝 용기 순량 vs **레시피 총 목표량** 차이 > `Σ(target×tol)` — **제품 규격 판정**. 개별 원료가 전부 같은 방향으로 치우친 경우를 잡는다 (9/17 조장 합의) |
| **WRONG_TOOL** [추가 1, v1.2] | 0 | QA | QA | 스쿱·약통 규격 불일치 = 교차오염 의심 |

action 별 FSM 처리 (`_deviate`): RETRY → `retry` 요청 그대로 · REFILL → `_resume` 저장, PAUSED, `safe(then='wait_interlock')` · QA → DEVIATION, `wait_qa` · FORCED → ERROR, `safe(then=None)`.
`requires_decision` 는 QA 일 때만 true → HMI 가 승인/폐기 버튼을 띄우는 조건.

## 6. 콜백 ↔ 루프 동기화

```
콜백 스레드                          run_loop 스레드
─────────────                        ────────────────
_srv_qa:                             _execute({'kind':'wait_qa'}):
  검사(mode==DEVIATION)                 _qa.clear()
  _qa_decision = 'APPROVED'/...   ──▶  _qa.wait()          ← 여기서 멈춰 있음
  _qa.set()                            return {'decision': _qa_decision}

_srv_interlock(ENTER):               _pause = True → safe_pose (취소는 skill_node 가)
  _pause = True                   ──▶  게이트가 EXIT 까지 잡는다
_srv_interlock(EXIT):                _execute({'kind':'wait_interlock'}):
  _interlock_exit.set()          ──▶  _interlock_exit.wait() → return {}

event 구독(NUDGE):                   _gate() — 로봇 동작 요청 **앞**에서만
  _nudge_paused 토글               ──▶  while _pause_reason(): 0.1 s 폴링
                                        (mode=PAUSED·note 발행, 두 번째 NUDGE 로 풀림)
```

**정지는 게이트 하나로 본다** (9/18). NUDGE(D-21)와 인터락 ENTER 는 이유만 다르고 "지금 다음 동작을 시작하면
안 된다"는 같은 뜻이라, `_pause_reason()` 이 비지 않는 동안 `_gate()` 가 잡아 둔다. 둘 다 걸려 있으면 **둘 다**
풀려야 간다 — NUDGE 로 세워 놓고 인터락으로 들어왔다면 다시 건드리고 EXIT 도 눌러야 움직인다.

**게이트를 폴링으로 돌리는 이유**: 깨우는 신호가 둘(두 번째 NUDGE, EXIT)이다. `threading.Event` 하나로 받으면
스킬이 도는 동안 정지·재개가 **다 지나간** 경우에 신호가 남아 **다음** 정지를 즉시 풀어 버린다. 불린을 0.1 s 로
들여다보면 남는 신호가 없다.

**게이트를 두는 자리**: `wait_qa`·`wait_interlock`·**`safe`** 앞에는 두지 않는다 (`GATE_BYPASS`). `safe` 는 "사람이 곧 들어오니 물러나라" 는 이동이라 NUDGE·인터락 정지보다 우선한다 — 안 그러면 NUDGE 로 세워 둔 로봇이 원료 소진에도 스쿱을 든 채 서서 두 번째 nudge 를 기다린다. 정지를 잡는 자리는 **다음 요청 앞 하나**뿐이다 — 스킬 결과 직후에 잡으면 FSM 이 `safe` 를 내야 하는 상황(원료 소진·강제 개입)에서도 결정을 못 하고 서 버린다. **EXIT 는 기다리는 쪽이 있을 때만 받는다** — `_pause`(ENTER) 또는 `_refill_waiting`(REFILL 대기). mode==PAUSED 로 가르면 NUDGE 정지 중에 눌린 EXIT 가 신호로 남아 다음 보충 대기가 보충 없이 풀린다. REFILL 대기가 EXIT 를 소비할 때 `_pause` 도 같이 내린다 — 대기 중에 ENTER 가 또 왔어도 EXIT 는 한 번이다.

**세트 끝의 NUDGE 는 정지가 아니다 (9/19, PR #23 리뷰 반영)**: FSM 이 `NUDGE_WAIT` 에서 `wait_nudge` 를 내면 노드는 `_nudge_waiting` 을 세우고 `_nudge_go` 를 기다린다. 그동안 들어온 NUDGE 는 `_on_event` 가 토글하지 않고 `_nudge_go` 를 세운다 — 로봇은 이미 서 있으니 「정지」 는 뜻이 없고, 이 접촉은 「다음 세트」 다. 대기에 들어갈 때 `_nudge_paused` 를 지운다 (nudge_wait 로 가는 동안의 접촉이 남아 다음 배치 첫 동작을 막지 않게). 인터락 ENTER 는 이 대기 중에도 `safe_pose` 를 실제로 부른다 — nudge_wait 는 안전 자세가 아니다.

**사람 대기 앞에 게이트를 두지 않는 이유**: `wait_qa`·`wait_interlock` **앞에는 두지 않는다**. 그 요청들은 로봇을 움직이지 않으니
멈출 것이 없고, 거기서 잡으면 QA 판정을 받기도 전에 서 버린다 — 그리고 판정 대기 중에는 mode 를 PAUSED 로
올릴 수 없으므로(아래 ②) 사람은 왜 섰는지도 못 본다. 그 요청들 뒤의 정지는 그다음 로봇 동작 요청 앞에서 잡는다.

ENTER 가 어려운 이유: 루프가 블로킹 Action 을 기다리는 중일 수 있다. **구현 (9/18)** — ENTER 는 `_pause` 를 세우고 곧바로 `safe_pose` 를 부른다. 취소는 process 가 아니라 **skill_node 가** 한다 (`SafePose` 계약: 대기 중인 Job 은 버리고 진행 중 Job 에 cancel 플래그). 그래서 진행 중이던 스킬은 `success=false` 로 돌아오고, 루프는 `_pause` 가 서 있으면 그 실패를 **취소로 읽어** EXIT 를 기다렸다가 **같은 요청을 처음부터 다시** 부른다 (부분 실행은 버린다 — `carry` 중간이면 접근점부터 다시). 취소가 안 걸리고 스킬이 그냥 끝났으면 **다음 요청 전에** 멈춘다.

**ENTER 가 오는 순간의 상태별 규칙 (9/18 리뷰 반영)** — ① RUNNING: 위 그대로, mode 를 PAUSED 로 올린다. ② **판정 대기 중인 일탈이 있으면 mode 를 덮지 않는다** — `_srv_qa` 가 `mode==DEVIATION` 만 받으므로 덮으면 QA 가 영영 거부되고 루프는 QA 만 기다리는 교착이 된다. `note` 로 알리고, 판정이 오면 `_execute` 가 `_pause` 를 보고 EXIT 까지 멈춘다. 이 규칙은 NUDGE 정지에도 똑같이 적용된다. ③ 이미 PAUSED(REFILL 대기·앞선 ENTER): `safe_pose` 를 다시 부르지 않고 `granted=true, '이미 대기 중'` — 다시 부르면 `_interlock_exit` 가 지워져 EXIT 를 두 번 눌러야 풀린다. **단 NUDGE 정지는 여기 해당하지 않는다** — PAUSED 지만 로봇은 그 자리에 선 것이라 안전 자세가 아니다. 사람이 들어오려면 `safe_pose` 를 실제로 불러야 한다. 판정값은 APPROVED·DISCARDED 만 받고 그 외는 거부한다(0 을 폐기로 읽지 않는다). FORCED 로 끝난 일탈은 `AUTO_RECOVERED` 가 아니라 `Deviation.FORCED`(계약 v1.2.1, PR #19) 로 나간다 — 그 계약 전 빌드에서는 `PENDING`+detail `FORCED` 폴백. 어느 쪽이든 자동 복구율 분자에서 빠진다.

`_pause` 를 **루프에서만 내리는 것**이 핵심이다. EXIT 핸들러에서 내리면, 취소된 스킬이 아직 돌아오지 않은 사이에 플래그가 풀려 그 실패가 **진짜 실패로 읽히고 FORCE_LIMIT 일탈이 찍힌다** (9/18 실제로 그렇게 났다 — 인터락을 걸었다 푸는 것만으로 일탈이 하나 쌓였다). EXIT 는 이벤트만 세우고, 내리는 것은 대기에서 깨어난 루프가 한다.

블로킹 `movel` 은 중간 취소가 안 되므로(I-004) 실제 안전 자세 도달은 **현재 동작이 끝난 직후**다 — HMI 에는 "요청 접수, 안전 자세 도달 대기" 를 보여 준다. 대기 중이 아닌데 들어온 EXIT 는 **무시한다** — 받아 두면 다음 REFILL 대기가 보충 없이 저절로 풀린다.

## 7. C 가 할 일 (순서대로)

**1~5·10·11 은 9/18 에 끝났다** (`nodes/process_node.py` 전면 작성, `core/station_map.py`·`core/attempt.py` 신설).
확인은 `test/test_process_node.py` — 가짜 skill_node(`test/fake_skill_node.py`) 를 세우고 레시피 1건을 끝까지 돌린다.

| # | 일 | 파일·함수 | 확인 방법 | 상태 |
|---|---|---|---|---|
| 1 | 스킬 클라이언트 8종 (`ActionClient` 5, 서비스 3) | `ProcessNode.__init__` 의 `self.act`·`self.srv` | 가짜 스킬 상대로 8종 전부 호출됨 | ✅ 9/18 |
| 2 | `_dispatch` 의 kind 11종 번역 — `carry` 조합, `weigh_held`, 원료→`scoop_N` 해석 | `_dispatch`·`_carry`·`_station_of`, 동기 대기 `_wait`(`Future`+`Event`) | `test_skill_call_sequence` | ✅ 9/18 |
| 3 | 발행 6종 — `weight`·`scoop_cycle`·`dispense_result`·`deviation`(+QA 후 재발행)·`event`·`state` | `_drain()` 한 곳에서 FSM 의 `results`/`deviations` 길이 변화를 보고 발행 | `test_batch_runs_to_completion` | ✅ 9/18 |
| 4 | `_srv_interlock` ENTER — `safe_pose` → granted, 루프는 `SkillCancelled` 로 받아 EXIT 까지 PAUSED | `_srv_interlock`·`_execute` | 6절 그림. **실물 확인은 남았다** | ✅ 9/18 (실물 미확인) |
| 5 | `batch_id` 형식 `B-YYYYMMDD-NNN`, 모르는 원료는 주문 단계 거부 | `_srv_submit`·`StationMap.check` | `test_rejects_unknown_material` | ✅ 9/18 |
| 6 | **[추가 7]** NUDGE 게이트 (`event` 구독, 토글, PAUSED 표시) | `_on_event`·`_pause_reason`·`_gate` | `test_process_node.py` NUDGE 6건 (가짜 skill_node 가 `CellEvent(NUDGE)` 를 쏜다). **실물 확인은 G1 과 함께** — 가상은 `scale.simulated` 라 skill_node 가 NUDGE 를 내지 않는다 | ✅ 9/18 |
| 7 | **[추가 1]** WRONG_TOOL — `grip` 결과 폭 검사 (기대 폭은 `stations.yaml` 의 `expected_scoop_width_mm`, 이미 `StationMap.widths` 로 읽고 있다) + RULES 추가 | `process_fsm`, `deviation.py` | 테스트: 폭 불일치 → DEVIATION | 9/21 |
| 8 | **[추가 3]** 재기동 이어하기 — 기동 시 D 의 DB API 로 미완료 배치 조회 → FSM 을 `state/idx/tare_g/results` 로 복원 → 용기 재계량 → 재개 | `ProcessNode.__init__`, `ProcessFSM.restore()` | 실행 중 Ctrl-C → 재실행 → 이어서 DONE | 9/22 |
| 9 | 고의 장애 T6 (a)(b)(c) 가상·실물 재현 | — | `deviation` 3종이 DB 에 남는지 | 9/22 |
| 10 | **[D-22]** `weigh_scoop` 계약 v1.2 — `WeighHeld` Action, `Deviation.kind` 3종 | `gmp_interfaces`, `docs/interfaces.md` | 계약 확정 (PR #10) | ✅ 9/18 |
| 11 | **[D-22]** `_pour_fraction` 을 `gmp_dosing/core/dosing.py` 로 이관 (B 와) | `process_fsm.py`, `dosing.py` | `test_prepour_check_prevents_overfill` | ✅ 9/18 |

**(해소됨)** 한때 A 의 `skill_node` 에 `weigh_held` Action 서버가 없어 진짜 가상 브링업이 `SCOOP_TARE` 에서 섰다 — 지금은 구현돼 있다(`skill_node` `WeighHeld`, 9/21 이후). 서버 없음이 FORCE_LIMIT 일탈로 잡혀 ERROR 로 끝나는 방어(무한 대기가 아니다, `test_missing_skill_server_does_not_hang`)는 그대로 남아 있고, FSM 배선 검증은 여전히 가짜 skill_node 로 한다.

가상 모드 기동: `source ~/auto-pharmacist/tools/env.sh && ros2 launch gmp_bringup cell.launch.py mode:=virtual` (Flask 미설치면 `hmi:=false`). 주문은 HMI 없이도 `ros2 service call /cell/submit_order gmp_interfaces/srv/SubmitOrder "{recipe: {product: DEMO, items: [{material_id: A, target_g: 200, tol_pct: 5}]}}"` 로 넣을 수 있다.

가짜 스킬만으로 끝까지 돌려 보기 (로봇·브링업 없이, 2초):
```
colcon build --packages-select gmp_interfaces && source install/setup.bash
python3 -m pytest ros2_ws/src/gmp_process/test/test_process_node.py -q
```

## 8. 함정

- `on_result` 는 전이표 밖이면 `RuntimeError('전이 없음')` 를 **일부러** 던진다. 조용히 넘기지 말 것 — 새 kind·상태를 넣으면 전이도 같이.
- `weigh`/`weigh_scoop` 결과의 `valid=false` 는 값이 아니라 **재계량 신호**다. FSM 은 `_invalid_or()` 로 같은 요청을 다시 내고(≤2), 3회째 `WEIGH_INVALID` 다. **끝나는 곳은 단계마다 다르다** (#213 결정 3) — 투입 **전**(`SCOOP_TARE`·`WEIGH_SCOOP`)은 `CLEANUP` 으로 원료·스쿱을 정리한 뒤 FORCED 기록과 함께 `ERROR`, 투입 **후**(`WEIGH_RESIDUAL`)는 되돌릴 게 없으므로 **QA** 다. 카운터는 단계별이고 유효 결과·단계 전환에서 0 으로 돌아간다 (결정 2). `TARE`·`VERIFY` 는 그 시점에 `self.cur`(원료)가 없거나 원료 단위가 아니라 `_tare_invalid`·`_verify_invalid` **배치 카운터**를 쓴다 — 규칙은 같다. `decide()` 는 항상 `valid=True` 로 부른다 (무효는 그 앞에서 걸러진다).
- **투입량은 스쿱 계량의 차이**(붓기 전 − 붓기 후)로 누적한다. 붓기 후 스쿱에 남은 잔량은 투입량이 아니며, 다음 스쿱에 섞여 들어가도 다시 붓기 전 계량에 잡히므로 이중으로 세지 않는다. 용기 계량은 배치 끝 VERIFY 에서 한 번 — 스쿱을 든 채로는 용기를 잡을 수 없다.
- 스쿱을 든 채 QA 로 간 일탈이 DISCARD 되면 **스쿱을 먼저 반납**(move → set_gripper open)하고 용기를 폐기함으로 옮긴다. `_qa_step` 이 이 분기를 가른다.
- `attempts` 는 SCOOP 진입마다 +1, `invalid` 는 계량 무효마다 +1 — 둘 다 `ItemRun` 에 있고 `DispenseResult.attempts` 로 나간다.
- `deviation` 은 TRANSIENT_LOCAL 이라 HMI 가 늦게 붙어도 최근 10건을 받는다. QA 판정 후 **같은 `deviation_id` 로 재발행**해야 record_node 가 upsert 한다.
- `state` 는 0.5 s 타이머가 계속 쏘므로, 전이 직후 한 번 더 쏘는 `_pub_state()` 는 지연을 줄이는 용도다. 빼도 동작은 한다.
- 스킬 실패(`success=false`·서버 없음·시간 초과)는 **FORCE_LIMIT 일탈**이 된다 — 노드가 `fsm.skill_failed(req, 사유)` 로 넘기고, RULES `(1, RETRY, FORCED)` 가 **1회 재시도 후 ERROR** 로 끊는다. 카운터는 (원료, 스텝, kind) 별이라 다른 스텝에서 또 실패하면 거기서 다시 1회 준다. 예외는 `safe` 요청 자체의 실패 — 더 물러설 곳이 없으니 바로 ERROR 로 끝낸다.
- **NUDGE 는 안전 자세로 보내지 않는다.** 그 자리에 선다 — 사람이 툭 건드려 세운 것이지 들어오겠다는 뜻이 아니다 (D-21). 스쿱을 든 채로도 선다. 사람이 **들어오려면** 인터락 ENTER 를 따로 눌러야 하고, 그때 `safe_pose` 가 나간다.
- **NUDGE 는 스킬 중간을 끊지 않는다.** 게이트는 요청 **사이**에 있다. 블로킹 `movel` 은 취소가 안 되고(I-004), 애초에 그 구간에서는 skill_node 가 NUDGE 를 감지하지도 않는다 (D-21) — 감지되는 자리는 유휴·계량 settle·붓기 대기다.
- **인터락이 끊은 실패는 실패가 아니다.** `_pause` 가 서 있으면 같은 `success=false` 를 취소로 읽어 EXIT 까지 기다렸다가 같은 요청을 다시 부른다. 이 구분이 없으면 인터락을 걸 때마다 FORCE_LIMIT 일탈이 쌓인다 — 그리고 `_pause` 를 EXIT 핸들러에서 내리면 구분이 있어도 똑같이 쌓인다 (6절).
- `ScoopCycle` 의 **6축 wrench 통계는 아직 못 채운다** — `WeighHeld`/`WeighContainer` 가 `WeightReading` 만 돌려주기 때문(I-008). 0 으로 두면 학습에서 진짜 0 과 구분되지 않으므로 `*_wrench_valid=false` 로 남긴다.
