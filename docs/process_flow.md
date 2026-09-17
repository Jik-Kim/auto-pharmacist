# gmp_process (C 파트) — 로직·입출력 흐름

> 담당 C 김병직. 계약은 `docs/interfaces.md` v1.1, 결정은 `docs/SOT.md` (D-02·D-03·D-18·D-20·D-21). 이 문서는 **"무엇이 들어와서 무엇이 나가는지"** 를 코드 기준으로 그린 것이다.
> 코드: `ros2_ws/src/gmp_process/gmp_process/` — `nodes/process_node.py` · `core/process_fsm.py` · `core/recipe.py` · `core/deviation.py`

## 0. 한 장 요약

```
                 HMI(hmi_web_node)                      record_node / HMI
   Service ──────────────┐                          ▲  Topic 5종
   submit_order          │                          │  state · weight · dispense_result · deviation · event
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

## 1. 노드 입출력 (계약 v1.1)

모든 이름은 상대 이름이고 launch 가 `namespace:=cell` 을 붙인다 → 실제는 `/cell/...`.

### 들어오는 것 (process_node 가 서버)

| 종류 | 이름 | 타입 | 누가 | 언제 | 응답 규칙 |
|---|---|---|---|---|---|
| Service | `submit_order` | `SubmitOrder` (Recipe → accepted, batch_id) | HMI | 주문 버튼 | 실행 중(RUNNING/PAUSED/DEVIATION)이면 `accepted=false`. 수락하면 FSM 생성 + run_loop 스레드 시작, `batch_id` 발급 (`B-YYYYMMDD-NNN`) |
| Service | `qa_decision` | `QaDecision` (batch_id, deviation_id, decision, operator_id) | HMI (셀 밖 QA) | DEVIATION 상태일 때 승인/폐기 | DEVIATION 아니면 거부. `_qa_decision` 저장 후 `_qa.set()`. `operator_id` 는 Deviation 메시지에 옮겨 담아 재발행(감사 추적) |
| Service | `interlock` | `InterlockRequest` (ENTER=1 / EXIT=2, reason) | HMI | 사람 반입 전/후 | **ENTER**: 진행 중 스킬을 멈추고 `SafePose` 성공 후에야 `granted=true` (TODO). **EXIT**: `_interlock_exit.set()` → 루프 재개 |
| Action | `run_batch` | `RunBatch` (Recipe → result, feedback CellState) | CLI·시험용 | `submit_order` 와 같은 일을 Action 으로 | 골격에 아직 없음. 우선순위 낮음 — HMI 는 Service 를 쓴다 |
| Topic (구독) | `event` | `CellEvent` code=`NUDGE` | skill_node | 사람이 로봇을 건드림 (D-21) | 루프 게이트: 다음 요청 전에 PAUSED 로 멈추고, 두 번째 NUDGE 로 재개 (추가 기능 7) |

### 나가는 것 (process_node 가 발행) — record_node 가 전부 DB 에 쓰고, HMI 가 화면에 띄운다

| Topic | 타입 | QoS | 언제 | 무엇 |
|---|---|---|---|---|
| `state` | `CellState` | depth 1, **TRANSIENT_LOCAL** | 0.5 s 타이머 + 전이마다 | `mode`(IDLE/RUNNING/PAUSED/DEVIATION/ERROR/DONE) · `step`(FSM 상태 문자열) · `item_index` · `batch_id` · `station` · `note` |
| `weight` | `WeightReading` | 20 | `weigh` 결과 받을 때마다 | gross/tare/net/std/valid — TARE 도, 보정 재계량도 전부 |
| `dispense_result` | `DispenseResult` | 50 | 원료 1종이 **끝날 때** (OK 또는 일탈로) | target/actual/error_pct/verdict/attempts/duration. UNDER 는 최종에 남지 않는다 |
| `deviation` | `Deviation` | depth 10, TRANSIENT_LOCAL | `_deviate()` 가 불릴 때마다 + QA 판정 후 `decision` 갱신해 **재발행** | kind·detail·requires_decision·decision·operator_id. 자동 복구된 것도 낸다 (지속성 근거) |
| `event` | `CellEvent` | 100 | 아래 코드 시점 | `BATCH_START`(product 포함 — D 와 합의) · `STEP`(전이) · `INTERLOCK_ENTER/EXIT` · `INTERVENTION_FORCED`(ERROR 진입) · `BATCH_END` |

### 부르는 것 (process_node 가 클라이언트, 상대는 전부 skill_node)

| 종류 | 이름 | 타입 | 보내는 것 | 받아서 쓰는 것 |
|---|---|---|---|---|
| Action | `move_to_station` | `MoveToStation` | `station_id`, `approach`(ABOVE=0/AT=1), `vel_scale` | `success`, `reached` → `state.station` |
| Action | `scoop` | `Scoop` | `material_id`, `attempt` | `success`, **`contact_detected`** |
| Action | `pour` | `Pour` | `target_station`("scale"), `fraction`(0~1) | `success` |
| Action | `weigh_container` | `WeighContainer` | `container_station`("scale"), `tare_g` | **`reading`**(WeightReading: gross/net/std/valid) |
| Service | `grip` | `Grip` | `close`, `width_mm`, `force_n`, `timeout_s` | `success`, `final_width_mm`, **`grip_inferred`** |
| Service | `measure_force` | `MeasureForce` | `samples`, `settle_s` | `fz_mean_n`, `fz_std_n`, `valid` |
| Service | `safe_pose` | `SafePose` | `reason` | `success` |

## 2. FSM 요청 ↔ 스킬 호출 (`_execute` 가 할 번역)

FSM 이 돌려주는 요청은 `{'kind': ..., ...}` 하나. 노드는 kind 별로 아래처럼 스킬을 부르고 **결과 dict** 로 돌려준다. 결과 키는 FSM 이 읽는 것만 채우면 된다.

| kind | 요청 필드 | 노드가 부르는 것 | 돌려줄 결과 dict |
|---|---|---|---|
| `measure` | — | `measure_force(samples=0, settle_s=0)` | `{'valid', 'fz_std_n'}` (SELF_CHECK: valid 아니면 ERROR 로 보낼 것 — TODO) |
| `carry` | `src`, `dst`, `slot`, `target`='cup' | `move(src, ABOVE)` → `move(src, AT)` → `grip(close, cup_width)` → `move(src, ABOVE)` → `move(dst, ABOVE)` → `move(dst, AT)` → `grip(open)` → `move(dst, ABOVE)` | `{'grip_inferred': <close 의 결과>}` — 파지 실패면 dst 로 가지 말고 바로 반환 |
| `weigh` | `station`, `tare_g` | `weigh_container(station, tare_g)` | `{'gross_g', 'net_g', 'std_g', 'valid'}` ← `reading` 에서 복사. 동시에 `weight` 토픽 발행 |
| `move` | `station`, `approach`('AT'/'ABOVE') | `move_to_station` | `{'success', 'reached'}` |
| `grip` | `close`, `target`('scoop'/'cup') | `grip(close, width=scoop_width 또는 cup_width, force)` | `{'grip_inferred', 'final_width_mm'}` |
| `scoop` | `material_id`, `attempt`, (`fraction`) | `scoop(material_id, attempt)` | `{'contact_detected', 'fraction'}` — fraction 은 요청값을 그대로 되돌려 준다 (POUR 가 쓴다) |
| `pour` | `station`, `fraction` | `pour(station, fraction)` | `{'success'}` |
| `safe` | `reason`, `then` | `safe_pose(reason)` | `{}` — 전이는 요청의 `then` 이 정한다 |
| `wait_qa` | `deviation` | 아무 스킬도 안 부름. `_qa.wait()` | `{'decision': 'APPROVED'/'DISCARDED'}` |
| `wait_interlock` | — | `_interlock_exit.wait()` | `{}` |

슬롯 오프셋(carry): `stations.yaml` 의 `slot_pitch_mm × slot` 를 y 에 더한 좌표 — 이 계산은 **skill_node 의 StationTable** 이 하고, process 는 `slot` 번호만 넘긴다 (A 와 인터페이스 확인 필요: `MoveToStation` 에 slot 필드가 없다 → `station_id="magazine#1"` 식 접미사로 합의하거나 계약 v1.2 에 `uint8 slot` 추가).

## 3. 상태 전이도

```
 IDLE ──submit_order──▶ SELF_CHECK ──measure ok──▶ PICK_CONTAINER ──carry ok──▶ TARE ──weigh──▶ ┐
                            │ measure invalid                │ grip_inferred=false               │
                            ▼                                ▼ GRIP_FAIL ×3 → 재시도            │
                          ERROR                              ×4 → ERROR                          │
                                                                                                 │
   ┌─────────────────────────────── 원료 i ──────────────────────────────────────────────────────┘
   │
   ▼
 PICK_SCOOP ──move(scoop_rack)──▶ grip(close) ──inferred──▶ SCOOP ──contact──▶ POUR ──▶ WEIGH
   ▲                                 │ ×3 재시도                │ ×3 재시도            │
   │                                 ▼ ×4 ERROR                 ▼ 연속 3회             ├─ OK ────▶ RETURN_SCOOP ──▶ (다음 원료 → PICK_SCOOP)
   │                                                        MATERIAL_EMPTY            │                              (마지막이면 → FINISH)
   │                                                        → PAUSED ──interlock EXIT──┤─ UNDER ─▶ SCOOP (fraction↓, attempts+1, ≤3)
   │                                                            (safe + wait_interlock)│─ INVALID ▶ WEIGH 재계량 (≤2)
   │                                                                                   │─ OVER ──▶ DEVIATION(OVERFILL)
   │                                                                                   └─ 4회째 UNDER ▶ DEVIATION(TIMEOUT)
   │
   │  DEVIATION ──wait_qa──▶ APPROVED ──▶ RETURN_SCOOP (원료는 결과 목록에 남김)
   │                       └▶ DISCARDED ─▶ carry(scale→reject_bin) ──▶ DISCARDED(종료)
   │
 FINISH ──carry(scale→output_tray)──▶ DONE
```

**PAUSED 는 두 경로**: (a) MATERIAL_EMPTY → `safe` → `wait_interlock` → EXIT 로 `_resume` 요청 재실행 (b) 사람 접촉 NUDGE → 노드 게이트가 다음 요청 전에 멈춤 → 두 번째 NUDGE 로 재개 (D-21, FSM 은 모름).
**ERROR** 진입 = `event(INTERVENTION_FORCED)` 발행 — MTBI 분모. 그 뒤 `safe(then=None)` 로 종료.

## 4. 상태별 상세 — 들어오는 결과 / 판단 / 나가는 요청 / 발행

| 상태 | 받는 결과 (이전 요청) | 판단 | 다음 요청 | 이 시점 발행 |
|---|---|---|---|---|
| `SELF_CHECK` | `measure` → valid, fz_std | valid 아니면 ERROR (TODO). 툴·TCP·감도 확인은 skill_node 가 기동 시 함 | `carry(magazine→scale, slot)` | `event BATCH_START`, `state` |
| `PICK_CONTAINER` | `carry` → grip_inferred | false → `_deviate(GRIP_FAIL)` (같은 carry 재시도) | `weigh(scale, tare 0)` | `deviation` (실패 시) |
| `TARE` | `weigh` → gross | `tare_g = gross`, `scale.set_tare()`. cur = 원료 0 | `move(scoop_rack, AT)` | `weight` |
| `PICK_SCOOP` | `move` → 도착 / `grip` → inferred | 파지 실패 → GRIP_FAIL 재시도(≤3) → 4회 FORCED → ERROR. **[추가 1]** `final_width_mm` 가 원료 기대 폭 ±margin 밖이면 `WRONG_TOOL` → QA | `grip(close, scoop)` → `scoop(material, attempt=1)` | `deviation` |
| `SCOOP` | `scoop` → contact_detected | false → SCOOP_EMPTY 재시도(≤3) → 4회째 REFILL → PAUSED | `pour(scale, fraction)` | `deviation` |
| `POUR` | `pour` | — | `weigh(scale, tare)` | — |
| `WEIGH` | `weigh` → net, valid | `decide(target, net, tol, attempts, valid, invalid, cfg)` → DONE / SCOOP(fraction) / DEVIATION(kind) | DONE→`move(scoop_rack)` · SCOOP→`scoop(attempt+1, fraction)` · INVALID→`weigh` 재 · DEVIATION→`wait_qa` | `weight` 항상 · `dispense_result` (DONE·DEVIATION 시) · `deviation` |
| `RETURN_SCOOP` | `move` / `grip(open)` | idx+1. 남았으면 다음 원료, 없으면 FINISH | `move(scoop_rack)` → `grip(open)` → `move(scoop_rack)`(다음) 또는 `carry(scale→output_tray)` | `state` |
| `FINISH` | `carry` → grip_inferred | 실패 → GRIP_FAIL 재시도 | 없음 (None = 끝) | `event BATCH_END`, `state DONE` |
| `DEVIATION` | `wait_qa` → decision | APPROVED → 결과에 남기고 RETURN_SCOOP. DISCARDED → 용기째 폐기 | `move(scoop_rack)` / `carry(scale→reject_bin)` | `deviation` 재발행(decision·operator_id 채움) |
| `PAUSED` | `wait_interlock` → {} | `_resume` 요청을 그대로 다시 실행 | `_resume` | `event INTERLOCK_ENTER/EXIT`, `state PAUSED` |
| `ERROR` | `safe(then=None)` | 종료 | None | `event INTERVENTION_FORCED`, `state ERROR` |
| `DISCARDED` | `carry` | 종료 | None | `event BATCH_END`, `state DONE`(mode) |

## 5. 일탈 정책표 (`core/deviation.py` — 바꾸려면 여기만)

`policy(kind, count)` → `(action, requires_decision)`. **count 는 같은 배치·같은 스텝·같은 kind 의 몇 번째인가** (`_counts[(idx, step, kind)]`).

| kind | limit | ≤ limit | > limit | 뜻 |
|---|---|---|---|---|
| GRIP_FAIL | 3 | RETRY | FORCED | 파지 3번 실패 → 4번째에 사람 |
| SLIP | 2 | RETRY | FORCED | |
| SCOOP_EMPTY | 3 | RETRY | REFILL | 3번 빈 스쿱 → 4번째에 보충 인터락 |
| MATERIAL_EMPTY | 0 | REFILL | REFILL | 즉시 보충 |
| OVERFILL | 0 | QA | QA | 항상 사람 판정 |
| TIMEOUT | 0 | QA | QA | 보정 3회 후에도 미달 |
| WEIGH_INVALID | 2 | RETRY | QA | |
| SAFETY_SWITCH · FORCE_LIMIT | 1 | RETRY | FORCED | |
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

_srv_interlock(ENTER):               (스킬 실행 중이면) Action cancel → safe_pose → granted=true   ← TODO
_srv_interlock(EXIT):                _execute({'kind':'wait_interlock'}):
  _interlock_exit.set()          ──▶  _interlock_exit.wait() → return {}

event 구독(NUDGE):                   루프 맨 위 게이트:
  _nudge.set() / 토글               ──▶ if paused: state=PAUSED 발행, 두 번째 NUDGE 까지 wait
```

ENTER 가 어려운 이유: 루프가 블로킹 Action 을 기다리는 중일 수 있다. 그래서 ENTER 는 (1) 진행 중 goal 에 `cancel_goal_async()` (2) 취소가 돌아오면 `safe_pose` (3) 성공하면 `granted=true`. 블로킹 `movel` 은 취소가 안 되므로(I-004) 실제로는 **현재 스킬이 끝난 직후** 안전 자세로 간다 — HMI 에는 "요청 접수, 안전 자세 도달 대기" 를 보여 준다.

## 7. C 가 할 일 (순서대로)

| # | 일 | 파일·함수 | 확인 방법 | 마감 |
|---|---|---|---|---|
| 1 | 스킬 클라이언트 7종 생성 (`ActionClient` 4, 서비스 클라이언트 3), `wait_for_server` | `ProcessNode.__init__` | 가상 브링업에서 서버 발견 로그 | 9/17 |
| 2 | `_execute` 의 kind 10종 번역 (2절 표) — `carry` 조합 포함 | `_execute`, 보조 `_call_action(client, goal)` (send → result 동기 대기, `Future` + `Event`) | 가상에서 `submit_order` → `state` 가 전이하는지 `ros2 topic echo /cell/state` | 9/18 |
| 3 | 발행 5종 채우기 — `weight`(weigh 마다) · `dispense_result`(원료 종료) · `deviation`(`_deviate` 후 + QA 후 재발행) · `event`(BATCH_START/END, STEP, INTERLOCK, INTERVENTION_FORCED, product 포함) | `_run_loop` 안, FSM 의 `results`/`deviations` 길이 변화를 보고 발행 | record_node 의 `cell.db` 에 행이 쌓이는지 | 9/18 |
| 4 | `_srv_interlock` ENTER — cancel → safe_pose → granted | `_srv_interlock`, `_execute` | HMI 인터락 버튼 → `state PAUSED` → EXIT → 재개 | 9/19 |
| 5 | SELF_CHECK 실패 → ERROR 전이, `batch_id` 형식 `B-YYYYMMDD-NNN` | `process_fsm.on_result`, `_srv_submit` | 테스트 추가 | 9/18 |
| 6 | **[추가 7]** NUDGE 게이트 (`event` 구독, 토글, PAUSED 표시) | `_run_loop` | skill_node 가 NUDGE 를 가짜로 쏘면 멈추고 다시 쏘면 가는지 | 9/18 |
| 7 | **[추가 1]** WRONG_TOOL — `grip` 결과 폭 검사 (기대 폭은 파라미터 `gripper.scoop_width_mm` 를 원료별 dict 로) + RULES 추가 + 계약 v1.2 (조장) | `process_fsm`, `deviation.py` | 테스트: 폭 불일치 → DEVIATION | 9/21 |
| 8 | **[추가 3]** 재기동 이어하기 — 기동 시 D 의 DB API 로 미완료 배치 조회 → FSM 을 `state/idx/tare_g/results` 로 복원 → 용기 재계량 → 재개 | `ProcessNode.__init__`, `ProcessFSM.restore()` | 실행 중 Ctrl-C → 재실행 → 이어서 DONE | 9/22 |
| 9 | 고의 장애 T6 (a)(b)(c) 가상·실물 재현 | — | `deviation` 3종이 DB 에 남는지 | 9/22 |

가상 모드 기동: `source ~/auto-pharmacist/tools/env.sh && ros2 launch gmp_bringup cell.launch.py mode:=virtual` (Flask 미설치면 `hmi:=false`). 주문은 HMI 없이도 `ros2 service call /cell/submit_order gmp_interfaces/srv/SubmitOrder "{recipe: {product: DEMO, items: [{material_id: A, target_g: 200, tol_pct: 5}]}}"` 로 넣을 수 있다.

## 8. 함정

- `on_result` 는 전이표 밖이면 `RuntimeError('전이 없음')` 를 **일부러** 던진다. 조용히 넘기지 말 것 — 새 kind·상태를 넣으면 전이도 같이.
- `weigh` 결과의 `valid=false` 는 값이 아니라 **재계량 신호**다. `decide()` 가 `SCOOP(fraction=0)` 을 돌려주면 붓지 말고 다시 재라는 뜻 (현재 FSM 은 INVALID 를 `weigh` 재요청으로 처리 — 그대로 두면 됨).
- `attempts` 는 SCOOP 진입마다 +1, `invalid` 는 계량 무효마다 +1 — 둘 다 `ItemRun` 에 있고 `DispenseResult.attempts` 로 나간다.
- `deviation` 은 TRANSIENT_LOCAL 이라 HMI 가 늦게 붙어도 최근 10건을 받는다. QA 판정 후 **같은 `deviation_id` 로 재발행**해야 record_node 가 upsert 한다.
- `state` 는 0.5 s 타이머가 계속 쏘므로, 전이 직후 한 번 더 쏘는 `_pub_state()` 는 지연을 줄이는 용도다. 빼도 동작은 한다.
- 스킬이 실패(`success=false`)로 돌아오는 경우가 표에 없다 — **move/pour 실패는 FORCE_LIMIT 일탈로 취급**해 `_deviate('FORCE_LIMIT', state)` 로 보내는 것을 권한다 (RETRY 1회 → FORCED).
