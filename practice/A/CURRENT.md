# A 스킬·로봇 현행 상태 (갱신: 2026-09-23)

**담당**: jonnykoh2008-ship-it

> 세션 시작 때 이 파일을 읽는다. 값을 쓰기 전에 근거 링크의 원본을 직접 연다.

## 지금 유효한 값
| 항목 | 값 | 근거 |
|---|---|---|
| 스쿠핑 실행 | A/B/C `execution_mode=taught_fixed`, `fixed_path.verified=true`. `calibrated=false`는 **높이 보정 경로만 차단**하며, 고정 티칭 경로는 실행한다. 현재 공정 요청은 `depth_fraction=1` | [stations.yaml](../../ros2_ws/src/gmp_bringup/params/stations.yaml), [SOT](../../docs/SOT.md), [PR #277](https://github.com/Jik-Kim/auto-pharmacist/pull/277) |
| 원료 A 원료면 | `material_1.surface_z_base_mm`는 yaml에만 남은 미사용 설정이다. 현재 실행 코드가 읽지 않으며 고정 티칭 경로의 높이도 만들지 않는다 | [stations.yaml](../../ros2_ws/src/gmp_bringup/params/stations.yaml), [PR #236](https://github.com/Jik-Kim/auto-pharmacist/pull/236) |
| Pour 경로 | middle → ABOVE Z290 → start Z243 → end Z320 → ABOVE → Z+50 → middle | [stations.yaml](../../ros2_ws/src/gmp_bringup/params/stations.yaml), [skill_node](../../ros2_ws/src/gmp_skills/gmp_skills/nodes/skill_node.py) |
| 용기 스테이션 | `workbench/passbox_empty/passbox_done/reject_bin` AT Z=130, 로컬 ABOVE는 Z=180, EXIT는 Z=330이다. 특히 workbench에서 빈 용기를 집을 때는 `middle_posx → empty_approach_posj` 뒤 TCP Z≈330에서 `empty_descent_mm=200`만큼 직선 하강한다 | [stations.yaml](../../ros2_ws/src/gmp_bringup/params/stations.yaml), [`_move_taught_station`](../../ros2_ws/src/gmp_skills/gmp_skills/nodes/skill_node.py) |
| 용기 이송의 `ABOVE` | `approach=ABOVE`라는 요청 이름이 실제 진입 높이를 뜻하지는 않는다. workbench 빈 용기 진입은 EXIT Z≈330이고, 로컬 AT→ABOVE만 Z=180이다 | [`_move_taught_station`](../../ros2_ws/src/gmp_skills/gmp_skills/nodes/skill_node.py), [`_leave_taught_station`](../../ros2_ws/src/gmp_skills/gmp_skills/nodes/skill_node.py) |
| 관절 이송 | `transfer_joint_vel_deg_s=60`, `transfer_joint_acc_deg_s2=100`; 실행 시 `vel_scale` 적용 | [common.yaml](../../ros2_ws/src/gmp_bringup/params/common.yaml), [DRL·DIO 일지](2026-09-23_DRL_고정경로_DIO.md) |
| DRL 이동 속도 | 관절 60°/s·100°/s², 병진 250 mm/s·1000 mm/s², 회전 80.625°/s·322.5°/s². `vel_scale`을 속도·가속도에 적용하며 1.0이면 DRL 기준값. ROS 실물 재검증 필요 | [common.yaml](../../ros2_ws/src/gmp_bringup/params/common.yaml), [dsr_arm.py](../../ros2_ws/src/gmp_skills/gmp_skills/adapters/dsr_arm.py) |
| 그리퍼 | 기본 백엔드 DIO. DO1/2 개폐, 약통 `DI1=1`, 스쿱 `DI1=DI2=1` 뒤 0.8초 안정, 열림 `DI1=0`. 어댑터는 DIO 폭을 `-1`로 반환하고, `process_node`가 `fingerprint_tolerance_mm=0`의 지문을 FSM에 넘겨 현재 비활성화한다 | [common.yaml](../../ros2_ws/src/gmp_bringup/params/common.yaml), [process_node.py](../../ros2_ws/src/gmp_process/gmp_process/nodes/process_node.py), [rg2_gripper.py](../../ros2_ws/src/gmp_skills/gmp_skills/adapters/rg2_gripper.py) |
| 안전 자가진단 | 실물 기동·복구 때 등록 툴 `tool_weight`, TCP `GripperDA_v1`, 충돌 감도 50%를 조회해 모두 일치해야 통과한다. 자동 변경하지 않는다 | [common.yaml](../../ros2_ws/src/gmp_bringup/params/common.yaml), [dsr_arm.py](../../ros2_ws/src/gmp_skills/gmp_skills/adapters/dsr_arm.py) |
| 공구 설정 | B CURRENT 참조: 1.36 kg · CoG `[5.31, -34.68, 8.28]` mm 동결 | [B CURRENT](../B/CURRENT.md), [PR #235](https://github.com/Jik-Kim/auto-pharmacist/pull/235) |
| 스쿱 최소 깊이 | `dosing.min_fraction=0.10`, `scooping.A.min_fraction=0.10`. 중복 선언은 남아 있으므로 항상 함께 바꿔야 한다 | [common.yaml](../../ros2_ws/src/gmp_bringup/params/common.yaml), [stations.yaml](../../ros2_ws/src/gmp_bringup/params/stations.yaml), [#222](https://github.com/Jik-Kim/auto-pharmacist/issues/222) |
| Scoop Action 종료 코드 | 내부 시간 초과는 ABORTED, 실제 클라이언트 취소만 CANCELED | [skill_node.py](../../ros2_ws/src/gmp_skills/gmp_skills/nodes/skill_node.py), [PR #236](https://github.com/Jik-Kim/auto-pharmacist/pull/236) |
| 계량 표본 품질 | `measure_force`/`measure_workpiece` 원시 표본열을 운영 계량 경로에서 받아 `fit_oscillation()` 잔차 σ와 고주파 σ를 각각 `max_std_g`/`max_hf_std_g` 게이트에 전달 | [skill_node.py](../../ros2_ws/src/gmp_skills/gmp_skills/nodes/skill_node.py), [dsr_arm.py](../../ros2_ws/src/gmp_skills/gmp_skills/adapters/dsr_arm.py), [#208](https://github.com/Jik-Kim/auto-pharmacist/issues/208) |

## 검증 기준선
- 9/26 #208 계량 품질 배선: `gmp_skills` 모의 시험 **490건 통과**. `gmp_process` 회귀까지 합쳐 634 passed / 2 skipped / 1 xfailed. 실물 Fz 표본의 적합 결과와 임계값 재검증은 남아 있다. [#208](https://github.com/Jik-Kim/auto-pharmacist/issues/208)
- 9/23 DRL 속도 정합화: `gmp_skills` 모의 테스트 **488건 통과**. 배율 1.0/0.2에서 관절·병진·회전 명령값, 초기 속도 설정, B 계측용 기존 생성자 호출 호환성을 확인했다. 공정 그림 재생성 diff 없음. 새 속도의 ROS 실물 검증은 미수행.
- `gmp_skills` 모의 테스트 **488건 통과**. Python 구문·YAML/XML 파싱·diff 공백 검사와 공정 다이어그램 재생성도 통과했다. [PR #290](https://github.com/Jik-Kim/auto-pharmacist/pull/290), [DRL·DIO 일지](2026-09-23_DRL_고정경로_DIO.md)
- 사용자 제공 DRL의 핵심 플로우는 실물 검증됐지만, **ROS로 이식한 경로의 통합 기동·실물 재검증은 수행하지 않았다.** [PR #277](https://github.com/Jik-Kim/auto-pharmacist/pull/277)

## 열린 과제 (이슈 번호)
- [#208](https://github.com/Jik-Kim/auto-pharmacist/issues/208) A 배선·설정·회귀시험은 9/26 완료. 남은 일은 B [#219](https://github.com/Jik-Kim/auto-pharmacist/issues/219) 병합과 실물 표본 재검증.
- [#222](https://github.com/Jik-Kim/auto-pharmacist/issues/222) `min_fraction` 중복 선언 해소. 현재 두 원본은 모두 0.10으로 맞춰져 있다.
- workbench 자세 Mx ≈ +0.9 Nm 원인, material_1/2 계량 σ·무효율 기전(B 인계분).
- `common.yaml`의 `robot.tool_name` 옆 공구 질량·CoG 주석은 두 세대 전 값이므로 B CURRENT 동결값으로 정정 필요.
- C 인계: 고정 Scoop의 접촉 미측정(`false/TAUGHT_FIXED`) 처리와 첫 fraction 요청 확인. 반환 후 재스쿱 및 `passbox_done → nudge_wait` 경로는 미검증. [setup](../../docs/setup.md)

## 알려진 함정
- 공구 자동측정 결과를 임의로 펜던트에 다시 넣지 않는다(동결). cz 89 는 철회됐다.
- `calibrated=false`를 Scoop 전체 비활성으로 읽지 않는다. `execution_mode=taught_fixed`와 `fixed_path.verified=true`가 고정 경로 실행 조건이다.
- workbench 빈 용기 진입은 EXIT Z≈330에서 하강하며, 로컬 AT→ABOVE만 Z=180이다. 둘을 같은 좌표로 가정하면 충돌 검토를 잘못한다.
- DIO에서는 폭이 `-1`이고 파지력 명령도 적용되지 않는다. `process_node`의 지문 허용치는 0이라 `WRONG_TOOL` 폭 지문이 동작한다고 보고하면 안 된다.
- DRL 원본 실물 검증과 ROS 이식본 실물 검증은 다르다. 현재 490건은 모의시험 기준선이다.
- `python3 tools/make_*.py` glob 호출 금지 — 첫 스크립트만 실행된다(AGENTS).

## 철회 이력 (최근 것 위)
- 2026-09-23 ~~용기 스테이션은 `solution_space=3`으로 ABOVE까지 `amovejx`~~ → DRL 티칭 `approach_posj` 진입과 직선 AT 접근·Z=330 이탈로 교체. [stations.yaml](../../ros2_ws/src/gmp_bringup/params/stations.yaml), [PR #277](https://github.com/Jik-Kim/auto-pharmacist/pull/277).
- 2026-09-23 ~~calibrated=false이면 모든 Scoop 실행 불가~~ → 고정 티칭 경로는 별도 verified로 실행한다. [SOT](../../docs/SOT.md) 9/23 사용자 승인.
- 2026-09-23 ~~G5 스쿠핑 보정 게이트: calibrated true 전환 후 데모~~ → PR #236 당시 높이 보정을 보류하고 빈 스쿱 Pour까지만 검증했으며, 이후 PR #277에서 높이 보정과 분리된 고정 티칭 경로를 도입했다. [PR #236](https://github.com/Jik-Kim/auto-pharmacist/pull/236), [PR #277](https://github.com/Jik-Kim/auto-pharmacist/pull/277).
