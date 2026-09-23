# C 공정 현행 상태 (갱신: 2026-09-23)

**담당**: Jik-Kim

> 세션 시작 때 이 파일을 읽는다. 값을 쓰기 전에 근거 링크의 원본을 직접 연다.

## 지금 유효한 값
| 항목 | 값 | 근거 |
|---|---|---|
| 계약 | **v1.8** — `DispenseResult.verdict` OK/UNDER/OVER/**INVALID=3** | PR #241(C 발행) · #240(D 소비), `docs/interfaces.md` |
| 계량 무효(WEIGH_INVALID) 정책 | `max_invalid_retries: 2`(총 3회); 카운터는 단계별·유효 시 초기화; 투입 전(TARE·SCOOP_TARE·WEIGH_SCOOP) → **CLEANUP → ERROR**, 투입 후(WEIGH_RESIDUAL·VERIFY) → QA; QA 승인 시 미측정을 기록한다. **원료 미측정과 최종 계량 미측정은 다른 사건이다** — `WEIGH_RESIDUAL` 무효는 `ItemRun.unmeasured` → `DispenseResult.verdict=INVALID`(그 원료의 투입량을 모름), `VERIFY` 무효는 `fsm.verify_unmeasured` → `RunBatch.result='DONE_UNMEASURED'`(배치 최종 순량을 모름, `process_node.py:921`). 한 배치에서 따로 일어난다 | #213 결정 1~5, PR #225·#227·#241 |
| VERIFY | ① `\|net − Σtarget\| > Σ(target×tol)` → BATCH_OUT_OF_SPEC 만. ② 는 기록만 | SOT D-26, PR #209 |
| 원료 소진 | SCOOP_EMPTY 재시도 ×3, **4회째 MATERIAL_EMPTY** → REFILL 인터락. 보충 뒤 재소진도 MATERIAL_EMPTY | #111 A안, PR #233·#234 |
| 첫 SCOOP 깊이 | `max(min_fraction, min(1, 남은 목표 ÷ scoop_nominal_g))` — 둘째 사이클부터 `decide()` 와 같은 식 | #221 C 몫, PR #224 |
| 무효 계량 통합 시험 | fake_skill_node 손잡이 없이 `_publish_result` 직접 호출 | PR #225 |
| 통합 시험 기준선 | gmp_process **177 passed / 7 skipped** (9/23, main `1b63b26` = #241 머지 후; C 실측) | ROS 소싱 필수 — 133 이면 소싱 누락. `.msg` 바뀐 브랜치는 워크트리 안에 `gmp_interfaces` 빌드 먼저 |

## 열린 과제 (이슈 번호)
- #108 본래 주제: `ScoopCycle` 6축 wrench 채울 경로 — 전제(모멘트 = 파지 품질) 근거 부족(노션 9/22), 미정리.
- #221 C 몫 완료, B 몫 대기(scoop_nominal_g 65 는 교착 구간 진입 → decide() 하한 동반).
- #228·#242 (D): DONE_UNMEASURED 소비 4곳, 진행 스트립 INVALID 「완료」 표시 — C 는 대기.
- 계량 경로 전체를 태우는 무효 계량 통합 시험(후속). **막힘 해소** — `fake_skill_node` 에 무효 손잡이가 필요해 `test/t6-fault-injection` 과 같은 파일에서 충돌하던 것이, 양쪽 다 머지돼 지금은 가능하다.

## 알려진 함정
- 빈 verdict ≠ 미측정. 첫 사이클 TIMEOUT 뒤 전량 반환은 `actual_g` 0 이 참값 → UNDER 가 맞고 INVALID 는 거짓(#241 시험 2건이 고정).
- 교착 구간: `decide()` 하한 × 반환 가드 → 최소채취 > 2×허용오차 일 때 (허용오차, 최소채취−허용오차) 구간에서 스쿱↔반환 반복. 65 g 나노미널이면 데모 C 100 g 이 경계.
- 설정 dataclass 는 키워드 인자만(AGENTS). `.msg` 바꾼 브랜치는 **워크트리 안에서** `colcon build --packages-select gmp_interfaces --cmake-force-configure` 뒤 시험. 공유 install 갈아끼우기 금지.
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
