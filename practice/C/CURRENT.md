# C 공정 현행 상태 (갱신: 2026-09-25)

**담당**: Jik-Kim

> 세션 시작 때 이 파일을 읽는다. 값을 쓰기 전에 근거 링크의 원본을 직접 연다.

## 지금 유효한 값
| 항목 | 값 | 근거 |
|---|---|---|
| 계약 | **v1.8** — `DispenseResult.verdict` OK/UNDER/OVER/**INVALID=3** | PR #241(C 발행) · #240(D 소비), `docs/interfaces.md` |
| 계량 무효(WEIGH_INVALID) 정책 | `max_invalid_retries` 2(총 3회) — **yaml 값이 아니라 `DosingConfig` 기본값**(`gmp_dosing/core/dosing.py`)이고 `process_node` 는 넘기지 않는다; 카운터는 단계별·유효 시 초기화; 투입 전(TARE·SCOOP_TARE·WEIGH_SCOOP) → **CLEANUP → ERROR**, 투입 후(WEIGH_RESIDUAL·VERIFY) → QA; QA 승인 시 미측정을 기록한다. **어디를 모르는지는 다른 사건이지만 배치 결과에서는 합쳐진다** — `WEIGH_RESIDUAL` 무효는 `ItemRun.unmeasured` → `DispenseResult.verdict=INVALID`(그 원료의 투입량을 모름), `VERIFY` 무효는 `fsm.verify_unmeasured`(배치 최종 순량을 모름). **`RunBatch.result` 는 둘 중 하나만 있어도 `DONE_UNMEASURED`** 이고(9/23 조장 결정), 그때 `CellEvent(WARN, BATCH_UNMEASURED)` 가 같이 나간다 — `DONE_UNMEASURED` 는 `RunBatch.result` 에만 실려 DB 에 닿지 않기 때문이다. ⚠️ **이 이벤트가 최종 `CellState(DONE)` 보다 먼저 간다고 전제하지 말 것** — `_pub_state` 가 0.5 s 타이머로도 돌아 역전될 수 있고, D 가 UPDATE 로 흡수한다 | #213 결정 1~5, PR #225·#227·#241 |
| VERIFY | ① `\|net − Σtarget\| > Σ(target×tol)` → BATCH_OUT_OF_SPEC 만. ② 는 기록만 | SOT D-26, PR #209 |
| 원료 소진 | SCOOP_EMPTY 재시도 ×3, **4회째 MATERIAL_EMPTY** → REFILL 인터락. 보충 뒤 재소진도 MATERIAL_EMPTY. **증거가 둘이다** — 접촉(`contact_detected`, **고정 모드에서는 안 본다**)과 순중량(`scooped_g ≤ dosing.empty_scoop_g`, **모드와 무관하게 늘 본다**). 계량 무효는 빈 스쿱이 아니다 — `_invalid_or` 가 먼저 걸러 간다. 일탈 `detail` 에 **어느 증거였는지** 남는다(DB 문자열) | #111 A안 · #282, PR #233·#234·#287 |
| 첫 SCOOP 깊이 | `max(min_fraction, min(1, 남은 목표 ÷ scoop_nominal_g))` — 둘째 사이클부터 `decide()` 와 같은 식. **고정 모드면 1.0** | #221 C 몫, PR #224 · #289(A) |
| 고정 스쿱 모드 | **`dosing.fixed_scoop` 하나**가 네 곳을 움직인다 — `decide()` 의 깊이·보충 판정(**#274**, e301cc9·e62b8da), `_first_fraction()`·`_rescoop_fraction()`·노드 배선(**A #289**), **FSM 의 접촉 우회**(C #287). 무게 그물(`empty_scoop_g`)은 플래그와 무관하게 늘 돈다(#287). 켜면 깊이는 언제나 1.0 | SOT **D-34**(#284) · #282 |
| 무효 계량 통합 시험 | fake_skill_node 손잡이 없이 `_publish_result` 직접 호출 | PR #225 |
| 통합 시험 기준선 | gmp_process **201 passed / 6 skipped** (9/25 C 실측, `3c68261`). ⚠️ 6be0c83 이 시험 1건을 더했다 — ROS 환경에서 재측정 필요. **내 파트 것만 적는다** — 다른 파트 현황은 `practice/<파트>/CURRENT.md` (규칙 5) | ROS 소싱 필수. **숫자로 소싱 누락을 가리지 말 것** — ROS 없이 돌려도 150 passed / 2 skipped 가 나온다(9/25). `python3 -c "import rclpy"` 로 확인한다. `.msg` 바뀐 브랜치는 워크트리 안에 `gmp_interfaces` 빌드 먼저 |

## 열린 과제 (이슈 번호)
- #108 본래 주제: `ScoopCycle` 6축 wrench 채울 경로 — 전제(모멘트 = 파지 품질) 근거 부족(노션 9/22), 미정리.
- #221 C 몫 완료, B 몫 대기 — `scoop_nominal_g` 를 낮추면 교착 구간에 들어간다(`decide()` 하한 동반). **값은 `common.yaml` 참조**(여기 숫자를 적으면 상한다).
- **D-35 (9/25 사용자 결정) — 스쿱 기준값·레시피 목표를 #272 실측에 맞춘다.** 결정 전문은 SOT D-35(`8141c98`, 아직 main 미반영), 실측 수치는 `practice/B/CURRENT.md`·#272 (규칙 5 — 여기 숫자를 적지 않는다). **C 몫**: ① `params/recipes/recipe-01~03.yaml` 목표값(recipes 담당은 C — `interfaces.md` §4. B CURRENT 의 「A」는 오기) ② `process_node` 선언 기본값(`scoop_nominal_g`·`min_fraction`)을 운영값과 맞추기, `zero_drift` 기본값도 확인 ③ 시험에 박힌 85·170 을 cfg 에서 읽게. **머지 순서: #287 → C 레시피 PR = D 시험 사본 PR(동시)** — D `test_v4_recipes.py` 가 두 쪽을 대조한다.
  - **시연 가능 최소 조건 = #287 머지 + D-35 적용** (B, 9/25). #287 전에는 첫 스쿱에서 무한 보충 루프다.
  - ⛔ **`dosing.fixed_scoop` 를 끄지 말 것** (D-35 도 유지) — B 가 한 번 제안했다가 철회했다(9/24 팀장 지적). 세 원료가 모두 `taught_fixed` 이고 A 가 깊이 1.0 외를 거부하므로, 끄면 `decide()` 가 첫 보충에서 부분 깊이를 내 **배치가 ERROR 로 죽는다** — 지금보다 나쁘다.
  - #272 는 원료 **A 만** 쟀다. B·C 는 스쿱 폭이 달라 1회량이 다를 수 있다(B 최우선 과제). 측정이 D-35 값 ±5 g 밖이면 원료별로 다시 결정한다 — 그러면 C 레시피·시험의 고정값도 원료별로 갈라야 한다.
- #228·#242 (D): DONE_UNMEASURED 소비 4곳, 진행 스트립 INVALID 「완료」 표시 — C 는 대기.
- 계량 경로 전체를 태우는 무효 계량 통합 시험(후속). **막힘 해소** — `fake_skill_node` 에 무효 손잡이가 필요해 `test/t6-fault-injection` 과 같은 파일에서 충돌하던 것이, 양쪽 다 머지돼 지금은 가능하다.

## 알려진 함정
- **깊이를 내는 곳이 셋이다** — 첫 스쿱(`_first_fraction`) · 보충(`decide()`) · **반환 뒤 재스쿱(`_rescoop_fraction`)**. 깊이 규칙을 바꿀 때는 셋 다 본다. 내가 앞 둘만 고쳤다가 셋째에서 0.1 대 값이 그대로 나가는 것을 시험이 잡았다. 셋의 출처가 다르다 — 보충(`decide()`)은 #274, 첫·재스쿱은 A #289.
- **FSM 을 바꾸면 「C FSM 이 이렇게 한다」고 적은 문서가 다섯 곳이다** — `docs/process_flow.md`(번역·상태 표), `docs/architecture.md`(6단계 표), `docs/interfaces.md`(9/23 고정 경로 운용 주석), `docs/setup.md`·`docs/SOT.md`(「B/C 인계」 2번, 같은 문단 두 벌), 그리고 `tools/make_process_drawio.py` 라벨 → `docs/diagrams/process_flow.drawio` 재생성. #287 첫 판이 코드와 CURRENT 만 고치고 이걸 다 놓쳐 정합성 점검에 걸렸다(9/25 보완). `grep -rn "contact_detected\|TAUGHT_FIXED" docs tools` 로 훑는다.
- **`empty_scoop_g` 라는 이름이 두 뜻이다** — `common.yaml` `dosing.empty_scoop_g`(C, 2.0)는 **순중량 문턱**이고, `stations.yaml` `scooping.A.empty_scoop_g`·`gmp_dosing/config/scale_reference.yaml` `empty_scoop_g` 는 **빈 스쿱 자체의 무게**다(`gmp_skills/core/scooping.py` 가 씀). grep 결과를 섞어 읽지 말 것.
- **접촉과 깊이는 다른 문제다** — #289 가 깊이를 1.0 으로 고정한 **뒤에도** 고정 경로는 무한 보충 루프였다. 한쪽을 고쳤다고 다른 쪽이 따라오지 않는다.
- **`fixed_scoop` 는 이제 결과를 바꾼다** — 도입 당시(#274)에는 보충 깊이 1.0 과 보충 불가 판정뿐이라 투입량·QA 횟수가 그대로였지만, 그 뒤 **첫·재스쿱 깊이 1.0**(#289)과 **접촉 우회**(#287)가 같은 플래그에 붙었다. 옛 기록(「플래그는 결과를 안 바꾼다」)을 근거로 쓰지 말 것.
- **`gh pr edit` 이 이 저장소에서 조용히 실패한다** — Projects(classic) 폐지 GraphQL 오류로 제목·본문이 안 바뀌는데 **종료 코드만 보면 성공처럼 보인다**. `gh api -X PATCH repos/Jik-Kim/auto-pharmacist/pulls/<번호> -f title=... -F body=@<파일>` 로 우회하고 `gh pr view` 로 확인한다.
- **갈라진 브랜치는 PR 차이를 부풀린다** — 머지 베이스가 둘이면 GitHub 이 남의 파일까지 내 변경으로 보여준다(9/23 #287). `git merge-base --all` 로 확인하고 최신 main 을 합쳐 하나로 만든다.
- **`TIMEOUT` 은 두 사실을 덮는다** — ① 보정 시도 소진 ② 보충하면 상한 초과(고정 스쿱). 처분이 같아 한 kind 로 뒀고 **`detail` 로만 구분된다**. 기록에서 TIMEOUT 을 보고 「재시도를 다 썼다」로 단정하지 말 것. #111 `MATERIAL_EMPTY` 와 같은 모양인데 **v1.8 직후라 계약을 안 열기로 한 것**이다(9/23 팀장 동의) — 다시 열 일이 생기면 그때 가른다.
- **잔량은 사이클마다 잃지 않는다** — 중간 사이클의 스쿱 잔량은 다음 스쿱에 섞여 회수되고, **마지막 사이클 것 하나만** 잃는다. `투입 = 스쿱수 × 1회량 − 잔량`(× 스쿱수 아님). 9/23 에 이걸 틀려 고정 스쿱 임계를 잘못 계산했다 — 스쿱이 많을수록 임계가 **내려간다** (85 g 1스쿱 78.5 g · 170 g 2스쿱 77.5 g, 운영 기준은 높은 쪽 78.5).
- 빈 verdict ≠ 미측정. 첫 사이클 TIMEOUT 뒤 전량 반환은 `actual_g` 0 이 참값 → UNDER 가 맞고 INVALID 는 거짓(#241 시험 2건이 고정).
- 교착 구간: `decide()` 하한 × 반환 가드 → 최소채취 > 2×허용오차 일 때 (허용오차, 최소채취−허용오차) 구간에서 스쿱↔반환 반복. 조건 자체는 `gmp_dosing/test/test_dosing.py` 가 운영 레시피로 지킨다(B 소관) — 여기서 겹쳐 재지 않는다.
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
