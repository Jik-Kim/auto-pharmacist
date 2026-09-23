# A 스킬·로봇 현행 상태 (갱신: 2026-09-23)

**담당**: jonnykoh2008-ship-it

> 세션 시작 때 이 파일을 읽는다. 값을 쓰기 전에 근거 링크의 원본을 직접 연다.

## 지금 유효한 값
| 항목 | 값 | 근거 |
|---|---|---|
| 스쿠핑 실행 | A/B/C `execution_mode=taught_fixed`, `fixed_path.verified=true`. `calibrated=false`는 **높이 보정 경로만 차단**하며, 고정 티칭 경로는 실행한다. 현재 공정 요청은 `depth_fraction=1` | [stations.yaml](../../ros2_ws/src/gmp_bringup/params/stations.yaml), [SOT](../../docs/SOT.md), [PR #277](https://github.com/Jik-Kim/auto-pharmacist/pull/277) |
| 원료 A 원료면 | `material_1.surface_z_base_mm = 90.0` (사용자 지정값, 센서값 아님). 고정 티칭 경로의 높이를 만드는 값은 아니다 | [stations.yaml](../../ros2_ws/src/gmp_bringup/params/stations.yaml), [PR #236](https://github.com/Jik-Kim/auto-pharmacist/pull/236) |
| Pour 경로 | middle → ABOVE Z290 → start Z243 → end Z320 → ABOVE → Z+50 → middle | [stations.yaml](../../ros2_ws/src/gmp_bringup/params/stations.yaml), [skill_node](../../ros2_ws/src/gmp_skills/gmp_skills/nodes/skill_node.py) |
| 용기 스테이션 | `workbench/passbox_empty/passbox_done/reject_bin` AT Z=130, 진입 관절각의 TCP가 접근점 Z=180, 이탈점 Z=330. 다른 스테이션에서 진입할 때 `approach_posj`로 이동한 뒤 AT까지 직선 하강한다 | [stations.yaml](../../ros2_ws/src/gmp_bringup/params/stations.yaml), [`_move_taught_station`](../../ros2_ws/src/gmp_skills/gmp_skills/nodes/skill_node.py) |
| 용기 이송의 `ABOVE` | 목적지 최초 `ABOVE`는 티칭 접근점(Z=180), 같은 스테이션에서 AT→`ABOVE` 또는 다음 스테이션으로 출발할 때는 안전 이탈점(Z=330)이다. 외부 이름 하나가 진입점과 이탈점을 함께 표현한다 | [`_move_taught_station`](../../ros2_ws/src/gmp_skills/gmp_skills/nodes/skill_node.py), [`_leave_taught_station`](../../ros2_ws/src/gmp_skills/gmp_skills/nodes/skill_node.py) |
| 관절 이송 | `transfer_joint_vel_deg_s=60`, `transfer_joint_acc_deg_s2=100`; 실행 시 `vel_scale` 적용 | [common.yaml](../../ros2_ws/src/gmp_bringup/params/common.yaml), [DRL·DIO 일지](2026-09-23_DRL_고정경로_DIO.md) |
| 그리퍼 | 기본 백엔드 DIO. DO1/2 개폐, 약통 `DI1=1`, 스쿱 `DI1=DI2=1` 뒤 0.8초 안정, 열림 `DI1=0`. 폭·파지력은 측정/설정하지 않으며 폭 지문은 `fingerprint_tolerance_mm=0`으로 비활성 | [common.yaml](../../ros2_ws/src/gmp_bringup/params/common.yaml), [rg2_gripper.py](../../ros2_ws/src/gmp_skills/gmp_skills/adapters/rg2_gripper.py) |
| 안전 자가진단 | 실물 기동·복구 때 등록 툴 `tool_weight`, TCP `GripperDA_v1`, 충돌 감도 50%를 조회해 모두 일치해야 통과한다. 자동 변경하지 않는다 | [common.yaml](../../ros2_ws/src/gmp_bringup/params/common.yaml), [dsr_arm.py](../../ros2_ws/src/gmp_skills/gmp_skills/adapters/dsr_arm.py) |
| 공구 설정 | B CURRENT 참조: 1.36 kg · CoG `[5.31, -34.68, 8.28]` mm 동결 | [B CURRENT](../B/CURRENT.md), [PR #235](https://github.com/Jik-Kim/auto-pharmacist/pull/235) |
| 스쿱 최소 깊이 | `dosing.min_fraction=0.10`, `scooping.A.min_fraction=0.10`. 중복 선언은 남아 있으므로 항상 함께 바꿔야 한다 | [common.yaml](../../ros2_ws/src/gmp_bringup/params/common.yaml), [stations.yaml](../../ros2_ws/src/gmp_bringup/params/stations.yaml), [#222](https://github.com/Jik-Kim/auto-pharmacist/issues/222) |
| Scoop Action 종료 코드 | 내부 시간 초과는 ABORTED, 실제 클라이언트 취소만 CANCELED | [skill_node.py](../../ros2_ws/src/gmp_skills/gmp_skills/nodes/skill_node.py), [PR #236](https://github.com/Jik-Kim/auto-pharmacist/pull/236) |

## 검증 기준선
- `gmp_skills` 모의 테스트 **474건 통과**. Python 구문·YAML/XML 파싱·diff 공백 검사와 공정 다이어그램 재생성도 통과했다. [DRL·DIO 일지](2026-09-23_DRL_고정경로_DIO.md)
- 사용자 제공 DRL의 핵심 플로우는 실물 검증됐지만, **ROS로 이식한 경로의 통합 기동·실물 재검증은 수행하지 않았다.** [PR #277](https://github.com/Jik-Kim/auto-pharmacist/pull/277)

## 열린 과제 (이슈 번호)
- [#208](https://github.com/Jik-Kim/auto-pharmacist/issues/208) 계량 고주파 σ(`measure_force` 원시 표본) + `skill_node`의 `raw_hf_std` 전달 → [#219](https://github.com/Jik-Kim/auto-pharmacist/issues/219) 리베이스.
- [#222](https://github.com/Jik-Kim/auto-pharmacist/issues/222) `min_fraction` 중복 선언 해소. 현재 두 원본은 모두 0.10으로 맞춰져 있다.
- workbench 자세 Mx ≈ +0.9 Nm 원인, material_1/2 계량 σ·무효율 기전(B 인계분).
- `common.yaml`의 `robot.tool_name` 옆 공구 질량·CoG 주석은 두 세대 전 값이므로 B CURRENT 동결값으로 정정 필요.
- 문서 정합성: 실행 원본 `stations.yaml`은 용기 스테이션 `exit_mm=200`(Z=330)인데 SOT·setup 일부는 아직 `exit_mm=150`(Z=280)이다. [stations.yaml](../../ros2_ws/src/gmp_bringup/params/stations.yaml), [SOT](../../docs/SOT.md), [setup](../../docs/setup.md)
- C 인계: 고정 Scoop의 접촉 미측정(`false/TAUGHT_FIXED`) 처리와 첫 fraction 요청 확인. 반환 후 재스쿱 및 `passbox_done → nudge_wait` 경로는 미검증. [setup](../../docs/setup.md)

## 알려진 함정
- 공구 자동측정 결과를 임의로 펜던트에 다시 넣지 않는다(동결). cz 89 는 철회됐다.
- `calibrated=false`를 Scoop 전체 비활성으로 읽지 않는다. `execution_mode=taught_fixed`와 `fixed_path.verified=true`가 고정 경로 실행 조건이다.
- 용기 경로의 `ABOVE`는 진입 시 Z=180, 로컬 상승·이탈 시 Z=330으로 해석된다. 좌표 하나라고 가정하면 충돌 검토를 잘못한다.
- DIO에서는 폭이 `None`이고 파지력 명령도 적용되지 않는다. `WRONG_TOOL` 폭 지문이 동작한다고 보고하면 안 된다.
- DRL 원본 실물 검증과 ROS 이식본 실물 검증은 다르다. 현재 474건은 모의시험 기준선이다.
- `python3 tools/make_*.py` glob 호출 금지 — 첫 스크립트만 실행된다(AGENTS).

## 철회 이력 (최근 것 위)
- 2026-09-23 ~~용기 스테이션은 `solution_space=3`으로 ABOVE까지 `amovejx`~~ → DRL 티칭 `approach_posj` 진입과 직선 AT 접근·Z=330 이탈로 교체. [stations.yaml](../../ros2_ws/src/gmp_bringup/params/stations.yaml), [PR #277](https://github.com/Jik-Kim/auto-pharmacist/pull/277).
- 2026-09-23 ~~calibrated=false이면 모든 Scoop 실행 불가~~ → 고정 티칭 경로는 별도 verified로 실행한다. [SOT](../../docs/SOT.md) 9/23 사용자 승인.
- 2026-09-23 ~~G5 스쿠핑 보정 게이트: calibrated true 전환 후 데모~~ → PR #236 당시 높이 보정을 보류하고 빈 스쿱 Pour까지만 검증했으며, 이후 PR #277에서 높이 보정과 분리된 고정 티칭 경로를 도입했다. [PR #236](https://github.com/Jik-Kim/auto-pharmacist/pull/236), [PR #277](https://github.com/Jik-Kim/auto-pharmacist/pull/277).
