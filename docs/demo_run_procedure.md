# 시연 실행 절차 — 9/30 (9/29~30 실물 확인 반영)

**명령어는 이 파일이 정본**이고, 값이 바뀌면 여기부터 고친다. 9/17~23 실물 5일 동안 채운다.

## 0. 순서

| 순서 | 창 | 띄우는 것 | 기다릴 것 |
|---|---|---|---|
| T0 | — | **기기 구성(9/23 팀 합의): 로봇 PC 1대(브링업·노드 4개·HMI 서버 전부) + QA 기기 = 휴대폰(브라우저)**. 다른 PC 브라우저는 추가 시연. 휴대폰은 「인터넷」이 아니라 **로봇 PC 와 같은 네트워크**에 있어야 한다(BRD 3.7.1 「동일 네트워크」) — Flask :5000 은 로봇 PC 안에서만 뜬다. 로봇 PC 는 유선(로봇 LAN 192.168.1.0/24, 게이트웨이 없음 — 9/20 수정) + Wi-Fi(휴대폰과 같은 AP 또는 휴대폰 핫스팟) 두 인터페이스로 붙이고, 휴대폰에서 `http://<로봇 PC Wi-Fi IP>:5000` 접속을 **G6 에서 미리** 확인한다 · 로봇 전원 · 컴퓨트박스 · 랜선 · 비상정지 해제 · **툴 `tool_weight` / TCP `GripperDA_v1` 선택 확인**(**9/30 13시경부터 `tool_weight` 1.41 kg · CoG [13.07, −32.10, −6.79] mm** — SOT D-10. ~~9/23~9/30 13시 1.36 kg · CoG [5.31, −34.68, 8.28] mm 동결(PR #235)~~. 값이 다르면 바꾸지 말고 A·B 에게 알린다 — 배치 중간에 바꾸면 영점이 계단으로 뛴다(변경 때 같은 자세 빈 용기 Fz +0.68 N)) · ~~⚠️ 실물 계량은 재보정 전 「미검증」 — `scale.gain 0.8859`~~ → **계량 보정은 경로별**(SOT D-37) — 용기 `scale.container` gain **0.873**(9/30 케이블 정리 뒤, ~~1.0975~~)·offset 229 g, 스쿱 `scale.scoop` gain 0.983·offset 103.5 g. 운영값(D-38, 9/30 11시 「10 s 시절」): 안정화 `settle_s` 10 s, 스쿱 무효 기준 `max_std_g` 10·`max_hf_std_g` 9.5 g, 원료별 빈 스쿱 편향 A 13·B 10·C 0 g, 빈 스쿱은 두 번 잰다(`tare_agree_g` 8 — 어긋나면 세 번, 원료당 약 20~40 s 추가). 퍼낸 뒤가 빈 스쿱보다 15 g 넘게 가벼우면 계량 불일치로 반환 → 빈 스쿱 다시 재기 → 재스쿱(`scoop_negative_limit_g` 15, D-42 ⑤). 빈 그리퍼 영점 이동 한계 **0.5 N**(9/30 시연 설정, D-38 — 한 배치 JTS 드리프트 −0.384 N 으로 QA 정지가 났다). **레시피 1 은 9/30 시연 설정으로 허용오차 ±50 %**(34.5~103.5 g, D-36) — 레시피 2·3 은 ±15 %. **그리퍼 케이블이 공구를 당기지 않게 정리됐는지 본다** — 9/30 오전 최종 무게 +26 % 의 원인이었다. 스쿱 순량은 배치마다 ±8~15 g 흔들리므로 QA 대기가 나올 수 있다 · **스쿠핑 경로 확인** — `stations.yaml` `scooping.{A,B,C}` 가 `execution_mode: taught_fixed`·`fixed_path.verified: true` 인지(D-33·D-34). ~~`calibrated` 가 `true` 여야 자동 스쿱 실행~~ — 고정 경로는 높이 보정을 쓰지 않으므로 `calibrated: false` 는 그대로 둔다 · 실물 첫 PC 는 `python3 -c 'import pymodbus'` — 없으면 `sudo apt install python3-pymodbus` (없으면 OnRobot 드라이버가 뜨자마자 죽고 `/onrobot/sendCommand` 가 안 보인다, 9/19) | 티치펜던트 Auto 모드 |
| T1 | 브링업 | **시연용 기록 DB 새로 시작** — 이전 런치가 모두 꺼진 상태에서 `mkdir -p ~/auto-pharmacist/records && B=$(mktemp -d ~/auto-pharmacist/records/backup_$(date +%m%d_%H%M%S)_XXXX) && mv -n ~/auto-pharmacist/records/cell.db* "$B"/ 2>/dev/null` (백업 폴더는 매번 새로 만들어 같은 분에 다시 실행해도 이전 백업을 덮어쓰지 않는다. WAL `cell.db-wal`·`-shm` 까지 함께 옮긴다. 안 옮기면 리허설·시험 배치가 KPI 카드·배치 이력에 섞인다. 새 DB 는 `record_node` 가 기동 시 만든다) → `ros2 launch gmp_bringup cell.launch.py mode:=real host:=192.168.1.100 vel_scale:=1.0` (**시연은 `vel_scale:=1.0`** — 런치 기본값 0.3 을 쓰지 않는다. 스쿱 1회량 실측(9/29, `gmp_dosing/calibration/scoop_sigma_0929_mat{A,B,C}.csv`, SOT D-36)이 1.0 에서 잰 값이고, 속도가 바뀌면 채취량이 바뀐다. 실물 첫 기동 점검만 0.2) | `[skill_node] SELF_CHECK OK` 로그 (툴·TCP·충돌 감도 일치) |
| T2 | HMI | (T1 에 포함) HMI 서버 기동 → **휴대폰 브라우저**로 `http://<로봇 PC Wi-Fi IP>:5000` 접속, 로그인 | 휴대폰 화면에 상태 `IDLE`, 그리퍼 폭 표시 |
| T3 | 사람 | 원료통 A·B·C(판 바깥 아래)와 **각 원료통 아래 전용 스쿱 3개**, **빈 약통을 Pass Box 「빈통」 칸(`passbox_empty`, slots 1)** 에 넣었는지. 완성품은 로봇이 Pass Box 「완성품」 칸(`passbox_done`)에 놓고 QA 가 회수한다 (D-23·D-24). 판 위 매거진·트레이·스쿱랙은 없다 (9/18 폐지). 이후 용기는 사람이 만지지 않는다 (D-18) | — |
| T4 | HMI(휴대폰) | 레시피 `recipe-01`(「레시피 1」) 선택 → 주문 제출 — `demo_batch.yaml` 은 운영 목록에서 삭제됐고 현재 운영 레시피는 `recipe-01`~`03`(9/22 #217) | 상태 `RUNNING` |
| T5 | — | 자율 운전. **공정 중에는 손대지 않는다** (건드리면 NUDGE 정지, 한 번 더 건드리면 재개 — D-21). **배치 중 HMI 진입 요청(ENTER)은 보충 대기 때만** — 용기 이송 중 ENTER 는 재개되지 않고 배치가 중단으로 끝난다(D-41). HMI 상단 **「비상정지」 버튼은 소프트웨어 정지**다(D-43, 계약 v1.11) — 물리 비상정지를 대신하지 않는다. 원료 3종이 끝나면 완성품을 Pass Box 「완성품」 칸에 놓고 `nudge_wait` 로 물러나 **PAUSED 로 선다(`NUDGE_WAIT`)** | 원료 3종 `OK` → 상태 `NUDGE_WAIT` |
| T5' | 사람 | **완성품을 Pass Box 에서 회수하고 로봇을 한 번 건드린다** (D-23 세트 경계). 이 NUDGE 없이는 `DONE` 이 되지 않고 다음 주문도 받지 않는다 | `DONE`, 상태 `IDLE` |
| T6 | 시연 | 일탈 시나리오: (a) 스쿱을 빼둔 채 시작 → `GRIP_FAIL` 자동 재시도 ×3, **4 회째 FORCED → `ERROR`**(자동 복구 아님) (b) 원료통 비움 → (빈 스쿱 무게가 크게 어긋나 순중량이 −15 g 아래로 읽히면 먼저 계량 불일치 반환 → 빈 스쿱 다시 재기가 낄 수 있다 — 반복되면 반환 한도 `TIMEOUT` → QA, D-42 ⑤) → `SCOOP_EMPTY` ×3 재시도 뒤 **4 회째는 kind 가 `MATERIAL_EMPTY`** 로 바뀌며 action=REFILL → 인터락 보충 → 재개 (#111 A안, 9/23 조장 결정). 보충 뒤에도 또 비면 계속 `MATERIAL_EMPTY` 다. **시연 설정(고정 스쿱)에서 빈 스쿱의 증거는 접촉이 아니라 무게다** — 고정 경로는 접촉을 재지 않으므로, 퍼낸 순중량이 `dosing.empty_scoop_g` 이하일 때 빈 스쿱으로 본다(D-34, #282). 일탈 `detail` 에 어느 증거였는지가 남는다. `ScoopCycle.outcome` 은 둘 다 `SCOOP_EMPTY` 로 남는다 — 스쿱 시도의 결과는 같은 사실이고 달라진 것은 일탈 기록이다 (c) 초과 스쿱 유도(원료를 수북이) → `RETURN_MATERIAL` 로 원료통에 반환 후 재스쿱(깊이 보정 모드는 더 얕게, 고정 모드는 다시 1.0) — **일탈이 뜨지 않는 정상 경로**이고 `ScoopCycle.outcome=RETURNED` 로만 남는다. 고정 경로는 반환 끝에서 원료 계량 자세로 이어 같은 원료를 다시 뜬다(#64, SOT D-39 — 9/30 실물 확인). 하한 미달·3회 무효 스쿱도 붓기 전에 반환 → 재스쿱한다(D-42). 반환 끝에서 실패해 안전 자세로 갈 때는 먼저 계량 자세로 빠진다(D-43 — 새 이동이라 첫 실행을 지켜본다). `TIMEOUT` 은 깊이 보정이 수렴하지 않아 `max_returns`(3) 를 넘을 때만 나며 가상에서는 거의 재현되지 않는다(9/22 재현: 공칭 9 배를 줘도 반환 1 회) (d) 배치 끝 VERIFY 규격 이탈 → `BATCH_OUT_OF_SPEC` → **휴대폰 HMI 에서 QA 판정**(셀 밖 원격 승인 장면, D-16·BR-05). `OVERFILL` 은 v1.3 뒤 정상 경로에서 나오지 않는다(초과는 붓기 전에 반환). QA 가 **폐기**하면 용기를 폐기함에 두고 **안전 자세를 거쳐** 넛지 대기로 간다(D-40) — 평소 경로와 달라 보여도 설계다. **레시피 1 은 9/30 ±50 % 라 (d)(e) 가 사실상 나지 않는다 — (d)(e) 를 보이려면 레시피 2·3(±15 %)으로 돌린다.** **(e) 고정 스쿱 미달** — 누적 투입량이 하한(목표 69 g 이면 **58.65 g**) 미만인데 한 스쿱(약 69 g)을 더하면 상한(**79.35 g**)을 넘으면 맞출 방법이 없다. **9/30 D-42 부터** 그런 스쿱은 붓기 전에 반환 → 재스쿱한다(반환 최대 3회). 한도를 넘으면 `TIMEOUT` → QA, **승인하면 같은 원료를 한 스쿱 더 퍼 그대로 붓고** 배치 끝 VERIFY ① 이 규격을 본다(벗어나면 `BATCH_OUT_OF_SPEC` 로 QA 한 번 더). ~~첫 계량에서 바로 ① TIMEOUT~~ ⚠️ **실물 시연에서 플래그를 끄면 안 된다** — 세 원료가 모두 고정 티칭 경로(`taught_fixed`)라, 끄면 FSM 이 접촉 미측정(false)을 빈 스쿱으로 읽어 보충 루프(#282)로 가고, 부분 깊이 요청은 A 가 거부한다. 「끄면 반환 3회 뒤 ① — 가는 길만 다르고 투입량·QA 횟수는 같다」는 **깊이 제어가 되는 가상 경로에서만** 성립한다. 멈춘 이유는 일탈 `detail` 에 남는다(「보충 불가 — 최소 채취 … 허용 상한 … 초과」) | (a)(b)(d)(e) 는 `deviation` 이 뜨고 기록에 남는다, (c) 는 `ScoopCycle` 기록으로만 확인 |
| T7 | — | 종료: 안전 자세 → 런치 Ctrl-C. HMI 「안전 자세로 이동」 버튼은 **안전 복구 뒤 안내 창에만** 있다(D-43, 배치 중 거부). 평소 종료는 `ros2 service call /cell/safe_pose gmp_interfaces/srv/SafePose "{reason: END}"` | 로봇이 안전 자세 |

규칙
- 시연 전날(9/29) 오전에 **9/23 영상**을 백업으로 준비한다. 로봇이 안 돌면 영상으로 대체한다.
- 인터락 `ENTER` 없이 셀 안에 손을 넣지 않는다. 충돌 감지가 멈추긴 하지만 그건 마지막 층이다.
- 시연 중 파라미터를 손으로 고치지 않는다. 고칠 일이 생기면 그것이 곧 이슈다.

## 1. 확인 게이트 (실물 첫날 9/17 오전 — 이것부터)

| 게이트 | 방법 | 통과 기준 | 실패 시 |
|---|---|---|---|
| G1 외력 분해능 | 터미널 1 `ros2 launch m0609_rg2_bringup new_bringup.launch.py mode:=real host:=192.168.1.100` (로봇+그리퍼 드라이버, cell.launch 아님) · 터미널 2 `python3 ros2_ws/src/gmp_dosing/calibration/measure_g1.py --actual-g 133 --goto-station material_3 --gripper --out records/g1_<날짜>.csv` — **weigh_held 가 실제로 재는 `material_N.posx` 자세**로 느리게 이동, 그리퍼는 스크립트가 열고 닫는다 (workbench 는 자세가 달라 tool_force JTS 편향이 안 옮겨간다 — 9/20 확인). 빈 그리퍼 영점 → 세트마다 물체를 **다시 잡고** 정지 → 6세트×30회×10표본을 tool_force·workpiece **동시에** 기록. 요약은 `python3 -m gmp_dosing.core.calib <csv> --method workpiece` | 계량 한 번(회차 평균)의 3σ 이력 — **9/18 18 g (중복 표본), 9/19 12.6 g (독립 표본), 9/21 재작업 4.01 g (material_3 자세, samples 20)**. 이 값이 채우던 `scale.min_resolvable_g` 는 9/22 VERIFY ② 폐지(SOT D-26, #209)로 **제거됐다** — 지금 3σ 는 유효성 게이트 `max_std_g`·`max_hf_std_g` 값의 근거로만 쓴다. 9/19 tool_force 확정, workpiece 탈락 (SOT D-07). 9/21 값(gain 1.03·offset 195.0·`max_std_g` 제안 8.0)은 **material_3 자세 전용**이라 운영값은 아직 미갱신 — 값 확정은 #186·#208 뒤 (SOT D-08·D-26). `measure_g1.py` 는 9/21 부터 그리퍼를 무조건 열지 않고 Enter 확인 뒤 연다 | 도징 단위·레시피 yaml 만 바꾼다 (SOT D-08). 표본 43 % 중복이면 `--period` 를 calib 이 알려 주는 갱신 간격보다 길게 |
| G2 그리퍼 modbus | `SetGripper close width:=20 force:=20` → 폭 피드백 | 폭이 목표 근처에서 멈추고 `busy` 가 풀린다 | `gripper.backend:=dio` 로 전환 (Q-02·Q-03) |
| G3 스테이션 티칭 | `stations.yaml` 9곳 | `MoveToStation` 9곳 왕복 무충돌 | — |
| G4 힘제어 접촉 | 비드 통 위에서 `Scoop` | `contact_detected=true`, 담금 깊이 상한 안 | 강성·목표력 파라미터 조정 |
| G6 휴대폰 HMI 리허설 (9/23 팀 합의) | 로봇 PC 를 유선(로봇 LAN)+Wi-Fi 로 붙인 상태에서 휴대폰 브라우저로 `http://<로봇 PC Wi-Fi IP>:5000` 접속 → 로그인 → 레시피 선택·주문 → 상태·계량 그래프·진행 스트립 → 일탈 카드에서 승인/폐기 → 인터락 → 안전 복구 버튼까지 **휴대폰 화면 폭(≤ 760 px, `hmi.css` 모바일 분기)** 에서 한 번씩 눌러 본다. 인터넷 유무는 무관, 같은 네트워크 여부가 관건. 다른 PC 브라우저 동시 접속도 1회 | 휴대폰에서 전 기능 조작 가능, QA 판정 → 로봇 재개 2 s 이내(BRD 3.6) |
| G5 스쿠핑 보정 (보류, 9/22 #216) | 힘 측정 불안정과 파지부 스쿱 상대 회전 문제로 최초 접촉 정지·높이 측정·`calibrated: true` 전환은 수행하지 않는다. 관련 설정·파라미터와 단위 테스트만 유지하고 전체 노드 통합·공정 플로우 검증을 우선한다 | `stations.yaml:scooping.A.calibrated=false` 유지, 접촉 정지·자동 높이 보정 경로가 실행되지 않음 | 통합·플로우 검증 뒤 힘 측정과 파지 회전 재현성을 해결하고 G5를 재개 |


## HMI 관리자 및 통신 환경 (V4)

본운영은 `ROS_DOMAIN_ID=70`, 격리 시험은 `ROS_DOMAIN_ID=88`을 사용한다.
**시연 기기(9/23 팀 합의)**: 노드는 전부 로봇 PC 1대(`docs/architecture.md` 「배치」). HMI 조작은 **휴대폰 브라우저**가 기본이고 다른 PC 는 추가 시연이다. 휴대폰은 로봇 PC 와 같은 네트워크(같은 Wi-Fi AP 또는 휴대폰 핫스팟에 로봇 PC 접속)여야 하며, 로봇 LAN(유선 192.168.1.0/24)에는 게이트웨이를 두지 않는다(두면 인터넷·Wi-Fi 경로가 로봇 LAN 으로 빨려 들어간다, 9/20). 접속 주소는 로봇 PC 의 **Wi-Fi 인터페이스 IP**(`ip -4 addr show` 로 확인) 이며 시연 전 G6 에서 확인한다. 외부 인터넷을 통한 접속(포트 포워딩·터널)은 범위 밖이다.
아래 계정 환경은 HMI를 기동할 터미널에서 먼저 설정한다. 비밀번호는 Git에 저장하지 않는다.

```bash
export ROS_DOMAIN_ID=70
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
export GMP_HMI_ADMIN_USER=admin
read -rsp '관리자 비밀번호(10자 이상): ' GMP_HMI_ADMIN_PASSWORD
printf '\n'
export GMP_HMI_ADMIN_PASSWORD
```

계정이 없는 상태에서는 조작할 수 없다. 운영은 기존 `cell.launch.py` 절차로 실행한다.
HMI를 별도 실행할 때도 위 환경을 설정하고 `ros2 launch gmp_hmi hmi.launch.py`를 사용한다.
이미 브링업에서 HMI가 실행 중이면 중복 기동하지 않는다.
운영 레시피는 설치된 `gmp_bringup/params/recipes`에서 읽는다.
`hmi_comm_test.launch.py`는 도메인 88에서 별도 시험용 레시피 사본(`gmp_hmi/config/test_recipes/v4`)을 쓴다 — 운영 레시피(`recipe-01`~`03`)와 **값이 같아야 한다** — 파일만 다르고 `test_v4_recipes.py` 가 사본↔운영 대조로 이를 강제한다. 현행 목표·허용오차는 `params/recipes/*.yaml` 참조(SOT D-35). ~~9/22 #217 로 40 g/80 g 단위가 등록됨~~ → D-33(9/23 저녁)으로 대체(9/25 문서 관리 대조).
실물 허용오차 충족 여부는 G1 측정 결과로 결정한다.
