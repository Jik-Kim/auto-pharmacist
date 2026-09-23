# 시연 실행 절차 — 9/29 (9/23 실물 확인 후 확정)

**명령어는 이 파일이 정본**이고, 값이 바뀌면 여기부터 고친다. 9/17~23 실물 5일 동안 채운다.

## 0. 순서

| 순서 | 창 | 띄우는 것 | 기다릴 것 |
|---|---|---|---|
| T0 | — | 로봇 전원 · 컴퓨트박스 · 랜선 · 비상정지 해제 · **툴 `tool_weight` / TCP `GripperDA_v1` 선택 확인**(9/22: `tool_weight` 의 cz 는 89.0 이어야 한다 — 공구 자동측정을 다시 돌리면 2.32 로 덮이므로 A 정식 반영 전에는 돌리지 않는다) · **스쿠핑 보정 확인** — `stations.yaml` `scooping.A.calibrated` 가 `true` 여야 자동 스쿱이 실행된다(#216, G5). `false` 면 첫 `SCOOP` 이 이동 전 거부되어 배치가 진행되지 않는다 · 실물 첫 PC 는 `python3 -c 'import pymodbus'` — 없으면 `sudo apt install python3-pymodbus` (없으면 OnRobot 드라이버가 뜨자마자 죽고 `/onrobot/sendCommand` 가 안 보인다, 9/19) | 티치펜던트 Auto 모드 |
| T1 | 브링업 | `ros2 launch gmp_bringup cell.launch.py mode:=real host:=192.168.1.100` | `[skill_node] SELF_CHECK OK` 로그 (툴·TCP·충돌 감도 일치) |
| T2 | HMI | (T1 에 포함) HMI 창 | 상태 `IDLE`, 그리퍼 폭 표시 |
| T3 | 사람 | 원료통 A·B·C(판 바깥 아래)와 **각 원료통 아래 전용 스쿱 3개**, **빈 약통을 Pass Box 「빈통」 칸(`passbox_empty`, slots 1)** 에 넣었는지. 완성품은 로봇이 Pass Box 「완성품」 칸(`passbox_done`)에 놓고 QA 가 회수한다 (D-23·D-24). 판 위 매거진·트레이·스쿱랙은 없다 (9/18 폐지). 이후 용기는 사람이 만지지 않는다 (D-18) | — |
| T4 | HMI | 레시피 `recipe-01`(「레시피 1」) 선택 → 주문 제출 — `demo_batch.yaml` 은 운영 목록에서 삭제됐고 현재 운영 레시피는 `recipe-01`~`03`(9/22 #217) | 상태 `RUNNING` |
| T5 | — | 자율 운전. **공정 중에는 손대지 않는다** (건드리면 NUDGE 정지, 한 번 더 건드리면 재개 — D-21). 원료 3종이 끝나면 완성품을 Pass Box 「완성품」 칸에 놓고 `nudge_wait` 로 물러나 **PAUSED 로 선다(`NUDGE_WAIT`)** | 원료 3종 `OK` → 상태 `NUDGE_WAIT` |
| T5' | 사람 | **완성품을 Pass Box 에서 회수하고 로봇을 한 번 건드린다** (D-23 세트 경계). 이 NUDGE 없이는 `DONE` 이 되지 않고 다음 주문도 받지 않는다 | `DONE`, 상태 `IDLE` |
| T6 | 시연 | 일탈 시나리오: (a) 스쿱을 빼둔 채 시작 → `GRIP_FAIL` 자동 재시도 ×3, **4 회째 FORCED → `ERROR`**(자동 복구 아님) (b) 원료통 비움 → `SCOOP_EMPTY` ×3 재시도 뒤 **4 회째는 kind 가 `MATERIAL_EMPTY`** 로 바뀌며 action=REFILL → 인터락 보충 → 재개 (#111 A안, 9/23 조장 결정). 보충 뒤에도 접촉이 없으면 계속 `MATERIAL_EMPTY` 다. `ScoopCycle.outcome` 은 둘 다 `SCOOP_EMPTY` 로 남는다 — 스쿱 시도의 결과는 같은 사실이고 달라진 것은 일탈 기록이다 (c) 초과 스쿱 유도(원료를 수북이) → `RETURN_MATERIAL` 로 원료통에 반환 후 더 얕게 재스쿱 — **일탈이 뜨지 않는 정상 경로**이고 `ScoopCycle.outcome=RETURNED` 로만 남는다. 실물은 반환→재스쿱 연결 경로 구현 전까지 여기서 `ERROR` 로 끝난다(#64). `TIMEOUT` 은 깊이 보정이 수렴하지 않아 `max_returns`(3) 를 넘을 때만 나며 가상에서는 거의 재현되지 않는다(9/22 재현: 공칭 9 배를 줘도 반환 1 회) (d) 배치 끝 VERIFY 규격 이탈 → `BATCH_OUT_OF_SPEC` → HMI QA 판정. `OVERFILL` 은 v1.3 뒤 정상 경로에서 나오지 않는다(초과는 붓기 전에 반환) | (a)(b)(d) 는 `deviation` 이 뜨고 기록에 남는다, (c) 는 `ScoopCycle` 기록으로만 확인 |
| T7 | — | 종료: HMI 에서 `SafePose` → 런치 Ctrl-C | |

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
| G5 스쿠핑 보정 (9/22 #216) | ① `skill.launch.py height_measure_only:=true` 로 원료 A 접촉 높이만 측정(스쿠핑 없음, `success=false`+`HEIGHT_MEASUREMENT_ONLY` 메시지가 정상) → WORLD/BASE 변환·스쿱 끝 오프셋(`tip_offset_world_mm`)·기준 표면(`reference_surface_world_z_mm`) 실측으로 확정 ② `stations.yaml` `scooping.A` 갱신 후 `calibrated: true` ③ 원료 A `depth_fraction` 1.0·0.5 로 실물 스쿠핑, 스쿱 끝이 바닥 하한(`material_bottom_world_z_mm`+`clearance_mm`)을 안 넘고 계량 자세로 복귀 | 채취량이 기준 순량(65 g)×fraction 의 ±20 % 안, 바닥 침범 없음, 털기 뒤 자세 확인 통과 | 오프셋·기준 표면 재측정. B/C 원료는 경로 자체가 없어 A 확정 뒤 별도 티칭 |


## HMI 관리자 및 통신 환경 (V4)

본운영은 `ROS_DOMAIN_ID=70`, 격리 시험은 `ROS_DOMAIN_ID=88`을 사용한다.
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
`hmi_comm_test.launch.py`는 도메인 88에서 별도 시험용 레시피 사본(`gmp_hmi/config/test_recipes/v4`)을 쓴다 — 운영 레시피(`recipe-01`~`03`, 9/22 #217 로 40 g/80 g 단위가 등록됨)와 파일이 다를 뿐 "40 g 단위는 시험 전용" 은 아니다.
실물 허용오차 충족 여부는 G1 측정 결과로 결정한다.
