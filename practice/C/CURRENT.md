# C 공정 현행 상태 (갱신: 2026-09-23)

**담당**: Jik-Kim

> 세션 시작 때 이 파일을 읽는다. 값을 쓰기 전에 근거 링크의 원본을 직접 연다.

## 지금 유효한 값
| 항목 | 값 | 근거 |
|---|---|---|
| 계약 | **v1.8** — `DispenseResult.verdict` OK/UNDER/OVER/**INVALID=3** | PR #241(C 발행) · #240(D 소비), `docs/interfaces.md` |
| 계량 무효(WEIGH_INVALID) 정책 | `max_invalid_retries: 2`(총 3회); 카운터는 단계별·유효 시 초기화; 투입 전(TARE·SCOOP_TARE·WEIGH_SCOOP) → **CLEANUP → ERROR**, 투입 후(WEIGH_RESIDUAL·VERIFY) → QA; QA 승인 시 미측정을 기록한다. **어디를 모르는지는 다른 사건이지만 배치 결과에서는 합쳐진다** — `WEIGH_RESIDUAL` 무효는 `ItemRun.unmeasured` → `DispenseResult.verdict=INVALID`(그 원료의 투입량을 모름), `VERIFY` 무효는 `fsm.verify_unmeasured`(배치 최종 순량을 모름). **`RunBatch.result` 는 둘 중 하나만 있어도 `DONE_UNMEASURED`** 이고(9/23 조장 결정), 그때 `CellEvent(WARN, BATCH_UNMEASURED)` 가 같이 나간다 — `DONE_UNMEASURED` 는 `RunBatch.result` 에만 실려 DB 에 닿지 않기 때문이다. ⚠️ **이 이벤트가 최종 `CellState(DONE)` 보다 먼저 간다고 전제하지 말 것** — `_pub_state` 가 0.5 s 타이머로도 돌아 역전될 수 있고, D 가 UPDATE 로 흡수한다 | #213 결정 1~5, PR #225·#227·#241 |
| VERIFY | ① `\|net − Σtarget\| > Σ(target×tol)` → BATCH_OUT_OF_SPEC 만. ② 는 기록만 | SOT D-26, PR #209 |
| 원료 소진 | SCOOP_EMPTY 재시도 ×3, **4회째 MATERIAL_EMPTY** → REFILL 인터락. 보충 뒤 재소진도 MATERIAL_EMPTY | #111 A안, PR #233·#234 |
| 첫 SCOOP 깊이 | `max(min_fraction, min(1, 남은 목표 ÷ scoop_nominal_g))` — 둘째 사이클부터 `decide()` 와 같은 식 | #221 C 몫, PR #224 |
| 무효 계량 통합 시험 | fake_skill_node 손잡이 없이 `_publish_result` 직접 호출 | PR #225 |
| 통합 시험 기준선 | gmp_process **193 passed / 6 skipped** · gmp_dosing 19 (= 합계 **222 / 6**, 9/23 C 실측 — `feature/fixed-scoop-decide` 위. gmp_dosing 이 29 로 늘었다) | ROS 소싱 필수 — 133 이면 소싱 누락. `.msg` 바뀐 브랜치는 워크트리 안에 `gmp_interfaces` 빌드 먼저 |

## 열린 과제 (이슈 번호)
- #108 본래 주제: `ScoopCycle` 6축 wrench 채울 경로 — 전제(모멘트 = 파지 품질) 근거 부족(노션 9/22), 미정리.
- #221 C 몫 완료, B 몫 대기(scoop_nominal_g 65 는 교착 구간 진입 → decide() 하한 동반).
- #228·#242 (D): DONE_UNMEASURED 소비 4곳, 진행 스트립 INVALID 「완료」 표시 — C 는 대기.
- 계량 경로 전체를 태우는 무효 계량 통합 시험(후속). **막힘 해소** — `fake_skill_node` 에 무효 손잡이가 필요해 `test/t6-fault-injection` 과 같은 파일에서 충돌하던 것이, 양쪽 다 머지돼 지금은 가능하다.

## 알려진 함정
- **`fixed_scoop` 플래그는 결과를 안 바꾼다** — 반환 3회와 RETURNED 기록이 사라지고 일탈 `detail` 이 생길 뿐, **투입량도 QA 횟수(2회)도 그대로**다. 「플래그를 켜면 QA 가 한 번으로 준다」고 적은 적이 있는데 틀렸다(9/23 실측 정정) — `test_fixed_scoop_플래그는_반환_루프를_없애지만_QA_횟수는_그대로다` 가 고정.
- **`TIMEOUT` 은 두 사실을 덮는다** — ① 보정 시도 소진 ② 보충하면 상한 초과(고정 스쿱). 처분이 같아 한 kind 로 뒀고 **`detail` 로만 구분된다**. 기록에서 TIMEOUT 을 보고 「재시도를 다 썼다」로 단정하지 말 것. #111 `MATERIAL_EMPTY` 와 같은 모양인데 **v1.8 직후라 계약을 안 열기로 한 것**이다(9/23 팀장 동의) — 다시 열 일이 생기면 그때 가른다.
- **잔량은 사이클마다 잃지 않는다** — 중간 사이클의 스쿱 잔량은 다음 스쿱에 섞여 회수되고, **마지막 사이클 것 하나만** 잃는다. `투입 = 스쿱수 × 1회량 − 잔량`(× 스쿱수 아님). 9/23 에 이걸 틀려 고정 스쿱 임계를 잘못 계산했다 — 스쿱이 많을수록 임계가 **내려간다** (85 g 1스쿱 78.5 g · 170 g 2스쿱 77.5 g, 운영 기준은 높은 쪽 78.5).
- 빈 verdict ≠ 미측정. 첫 사이클 TIMEOUT 뒤 전량 반환은 `actual_g` 0 이 참값 → UNDER 가 맞고 INVALID 는 거짓(#241 시험 2건이 고정).
- 교착 구간: `decide()` 하한 × 반환 가드 → 최소채취 > 2×허용오차 일 때 (허용오차, 최소채취−허용오차) 구간에서 스쿱↔반환 반복. 65 g 나노미널이면 데모 C 100 g 이 경계.
- 설정 dataclass 는 키워드 인자만(AGENTS). `.msg` 바꾼 브랜치는 **워크트리 안에서** `colcon build --packages-select gmp_interfaces --cmake-force-configure` 뒤 시험. 공유 install 갈아끼우기 금지.
- **NUDGE 계열 시험은 경합에 약하다.** 다른 세션과 겹쳐 돌면 무더기로 깨진다 (9/23: 9건 실패 → 단독 재실행 205/6 전부 통과). **실행 시간이 평소의 2~3배면 경합을 의심한다** — 84 s 가 224 s 였다. 실패를 볼 수 있는 실행에는 `tail` 을 붙이지 말 것 (9/23 에 `tail -4` 로 9건 중 3건만 남겨 판단이 한 번 막혔다). `-rf` 로 요약을 뽑는다.
- gmp_hmi 를 gmp_process 와 **같은 pytest 실행**에 넣으면 노드 경합으로 `test_scoop_cycle_attempt_numbers_are_unique_per_material` 이 `FORCE_LIMIT @PICK_CONTAINER` 로 깨진다(격리 3/3 통과, #242 코멘트). 기준선은 패키지별로 따로 잰다.
- 스크래치패드는 통째로 지워질 수 있다 — 멈추기 전 커밋·푸시.

## 철회 이력 (최근 것 위)
- 2026-09-23 ~~「문서에만 있고 코드에 없는 것」 9/23 **네 건**~~ → **둘.** BRD 3.1.3 은 애초에 불일치가 아니었고(폐기 표시·SOT Q-11 근거가 이미 달려 있음), `depth_fraction` 은 전날 #216 이 고쳤다(`skill_node.py:972`). **기억에서 꺼낸 목록을 근거로 썼다** — 항목마다 현행 main 에서 다시 확인해야 했다.
- 2026-09-23 ~~「`verdict` 가 비면 INVALID」~~ → **`unmeasured > 0` 으로 가른다.** 첫 사이클 TIMEOUT 뒤 전량 반환은 `actual_g` 0 이 참값이라 UNDER 가 맞다(실측). `test_108_안_들어간_것은_INVALID_가_아니라_UNDER_다` 가 고정.
- 2026-09-23 ~~「`hmi.js` 에 INVALID 이 영문 원문으로 뜬다」~~ → **`'?'` 가 뜬다.** `hmi.js` 는 정수 verdict 가 아니라 `hmi_web_node:339` 가 `VERDICTS` 로 변환한 문자열을 받는다 → `hmi.js` 수정은 `db.py:15` 에 딸린다.
- 2026-09-23 ~~「`session_inventory` 도 v1.8 과 동시 머지 필요」~~ → **거동 무변경.** `observe` 는 `OK`·`OVER` 만 차감하고 `UNDER` 도 이미 제외라 v1.8 전후가 같다. 별건(재고 과대표시는 전부터 있던 문제).
- 2026-09-23 ~~미측정 원료 verdict 되매김 UNDER(임시)~~ → INVALID=3 (v1.8). PR #241.
- 2026-09-22 ~~미측정 원료 verdict 빈 값 → 'OK' 폴백~~ → verdict_of 되매김 UNDER + WARN DISPENSE_UNMEASURED. PR #225.
- 2026-09-22 ~~RULES['WEIGH_INVALID'] (2,'RETRY','QA')~~ → (0,'QA','QA'), 재계량은 max_invalid_retries 전담. #213 결정 1.
