# 시연 실행 절차 — 9/29 (9/23 실물 확인 후 확정)

**명령어는 이 파일이 정본**이고, 값이 바뀌면 여기부터 고친다. 9/17~23 실물 5일 동안 채운다.

## 0. 순서

| 순서 | 창 | 띄우는 것 | 기다릴 것 |
|---|---|---|---|
| T0 | — | 로봇 전원 · 컴퓨트박스 · 랜선 · 비상정지 해제 · **툴 `tool_weight` / TCP `GripperDA_v1` 선택 확인** · 실물 첫 PC 는 `python3 -c 'import pymodbus'` — 없으면 `sudo apt install python3-pymodbus` (없으면 OnRobot 드라이버가 뜨자마자 죽고 `/onrobot/sendCommand` 가 안 보인다, 9/19) | 티치펜던트 Auto 모드 |
| T1 | 브링업 | `ros2 launch gmp_bringup cell.launch.py mode:=real host:=192.168.1.100` | `[skill_node] SELF_CHECK OK` 로그 (툴·TCP·충돌 감도 일치) |
| T2 | HMI | (T1 에 포함) HMI 창 | 상태 `IDLE`, 그리퍼 폭 표시 |
| T3 | 사람 | 원료통 A·B·C(판 바깥 아래)와 **각 원료통 아래 전용 스쿱 3개**, **빈 약통을 Pass Box 「빈통」 칸(`passbox_empty`, slots 1)** 에 넣었는지. 완성품은 로봇이 Pass Box 「완성품」 칸(`passbox_done`)에 놓고 QA 가 회수한다 (D-23·D-24). 판 위 매거진·트레이·스쿱랙은 없다 (9/18 폐지). 이후 용기는 사람이 만지지 않는다 (D-18) | — |
| T4 | HMI | 레시피 `demo_batch` 선택 → 주문 제출 | 상태 `RUNNING` |
| T5 | — | 자율 운전. **손대지 않는다** | 원료 3종 `OK`, `DONE` |
| T6 | 시연 | 일탈 시나리오: (a) 스쿱을 빼둔 채 시작 → `GRIP_FAIL` 자동 재시도 (b) 원료통 비움 → `MATERIAL_EMPTY` → 인터락 보충 → 재개 (c) 과다 투입 유도 → `OVERFILL` → HMI QA 판정 | 각각 `deviation` 이 뜨고 기록에 남는다 |
| T7 | — | 종료: HMI 에서 `SafePose` → 런치 Ctrl-C | |

규칙
- 시연 전날(9/29) 오전에 **9/23 영상**을 백업으로 준비한다. 로봇이 안 돌면 영상으로 대체한다.
- 인터락 `ENTER` 없이 셀 안에 손을 넣지 않는다. 충돌 감지가 멈추긴 하지만 그건 마지막 층이다.
- 시연 중 파라미터를 손으로 고치지 않는다. 고칠 일이 생기면 그것이 곧 이슈다.

## 1. 확인 게이트 (실물 첫날 9/17 오전 — 이것부터)

| 게이트 | 방법 | 통과 기준 | 실패 시 |
|---|---|---|---|
| G1 외력 분해능 | 터미널 1 `ros2 launch m0609_rg2_bringup new_bringup.launch.py mode:=real host:=192.168.1.100` (로봇+그리퍼 드라이버, cell.launch 아님) · 터미널 2 `python3 ros2_ws/src/gmp_dosing/calibration/measure_g1.py --actual-g 133 --goto-station material_3 --gripper --out records/g1_<날짜>.csv` — **weigh_held 가 실제로 재는 `material_N.posx` 자세**로 느리게 이동, 그리퍼는 스크립트가 열고 닫는다 (workbench 는 자세가 달라 tool_force JTS 편향이 안 옮겨간다 — 9/20 확인). 빈 그리퍼 영점 → 세트마다 물체를 **다시 잡고** 정지 → 6세트×30회×10표본을 tool_force·workpiece **동시에** 기록. 요약은 `python3 -m gmp_dosing.core.calib <csv> --method workpiece` | 계량 한 번(회차 평균)의 3σ 가 `min_resolvable_g` — **9/18 18 g (중복 표본), 9/19 12.6 g (독립 표본)**. 9/19 tool_force 확정, workpiece 탈락 (SOT D-07) | 도징 단위·레시피 yaml 만 바꾼다 (SOT D-08). 표본 43 % 중복이면 `--period` 를 calib 이 알려 주는 갱신 간격보다 길게 |
| G2 그리퍼 modbus | `SetGripper close width:=20 force:=20` → 폭 피드백 | 폭이 목표 근처에서 멈추고 `busy` 가 풀린다 | `gripper.backend:=dio` 로 전환 (Q-02·Q-03) |
| G3 스테이션 티칭 | `stations.yaml` 9곳 | `MoveToStation` 9곳 왕복 무충돌 | — |
| G4 힘제어 접촉 | 비드 통 위에서 `Scoop` | `contact_detected=true`, 담금 깊이 상한 안 | 강성·목표력 파라미터 조정 |


## HMI 관리자 및 통신 환경 (V4)

본운영은 `ROS_DOMAIN_ID=70`, 격리 시험은 `ROS_DOMAIN_ID=88`을 사용한다.
아래 계정 환경은 HMI를 기동할 터미널에서 먼저 설정한다. 비밀번호는 Git에 저장하지 않는다.

```bash
export ROS_DOMAIN_ID=70
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
export GMP_HMI_ADMIN_USER=admin
read -rsp '관리자 비밀번호(12자 이상): ' GMP_HMI_ADMIN_PASSWORD
printf '\n'
export GMP_HMI_ADMIN_PASSWORD
```

계정이 없는 상태에서는 조작할 수 없다. 운영은 기존 `cell.launch.py` 절차로 실행한다.
HMI를 별도 실행할 때도 위 환경을 설정하고 `ros2 launch gmp_hmi hmi.launch.py`를 사용한다.
이미 브링업에서 HMI가 실행 중이면 중복 기동하지 않는다.
운영 레시피는 설치된 `gmp_bringup/params/recipes`에서 읽는다.
`hmi_comm_test.launch.py`는 도메인 88에서 실행하며 40g 단위 레시피는 시험 전용이다.
실물 허용오차 충족 여부는 G1 측정 결과로 결정한다.
