# A 스킬·로봇 현행 상태 (갱신: 2026-09-29)

**담당**: jonnykoh2008-ship-it

> 세션 시작 때 이 파일을 읽는다. 값을 쓰기 전에 근거 링크의 원본을 직접 연다.

## 지금 유효한 값
| 항목 | 값 | 근거 |
|---|---|---|
| 스쿠핑 실행 | A/B/C `execution_mode=taught_fixed`, `fixed_path.verified=true`. `calibrated=false`는 **높이 보정 경로만 차단**하며, 고정 티칭 경로는 실행한다. 현재 공정 요청은 `depth_fraction=1` | [stations.yaml](../../ros2_ws/src/gmp_bringup/params/stations.yaml), [SOT](../../docs/SOT.md), [PR #277](https://github.com/Jik-Kim/auto-pharmacist/pull/277) |
| 원료 A 원료면 | `material_1.surface_z_base_mm`는 yaml에만 남은 미사용 설정이다. 현재 실행 코드가 읽지 않으며 고정 티칭 경로의 높이도 만들지 않는다 | [stations.yaml](../../ros2_ws/src/gmp_bringup/params/stations.yaml), [PR #236](https://github.com/Jik-Kim/auto-pharmacist/pull/236) |
| Pour 경로 | middle → ABOVE Z290 → start Z243 → end Z320 → ABOVE → Z+50 → middle | [stations.yaml](../../ros2_ws/src/gmp_bringup/params/stations.yaml), [scooping.py](../../ros2_ws/src/gmp_skills/gmp_skills/execution/scooping.py) |
| 용기 스테이션 | `workbench/passbox_empty/passbox_done/reject_bin` AT Z=130, 로컬 ABOVE는 Z=180, EXIT는 Z=330이다. 특히 workbench에서 빈 용기를 집을 때는 `middle_posx → empty_approach_posj` 뒤 TCP Z≈330에서 `empty_descent_mm=200`만큼 직선 하강한다 | [stations.yaml](../../ros2_ws/src/gmp_bringup/params/stations.yaml), [`_move_taught_station`](../../ros2_ws/src/gmp_skills/gmp_skills/execution/motion.py) |
| 용기 이송의 `ABOVE` | `approach=ABOVE`라는 요청 이름이 실제 진입 높이를 뜻하지는 않는다. workbench 빈 용기 진입은 EXIT Z≈330이고, 로컬 AT→ABOVE만 Z=180이다 | [`_move_taught_station`](../../ros2_ws/src/gmp_skills/gmp_skills/execution/motion.py), [`_leave_taught_station`](../../ros2_ws/src/gmp_skills/gmp_skills/execution/motion.py) |
| 관절 이송 | `transfer_joint_vel_deg_s=60`, `transfer_joint_acc_deg_s2=100`; 실행 시 `vel_scale` 적용 | [common.yaml](../../ros2_ws/src/gmp_bringup/params/common.yaml), [DRL·DIO 일지](2026-09-23_DRL_고정경로_DIO.md) |
| DRL 이동 속도 | 관절 60°/s·100°/s², 병진 250 mm/s·1000 mm/s², 회전 80.625°/s·322.5°/s². `vel_scale`을 속도·가속도에 적용하며 1.0이면 DRL 기준값. ROS 실물 재검증 필요 | [common.yaml](../../ros2_ws/src/gmp_bringup/params/common.yaml), [dsr_arm.py](../../ros2_ws/src/gmp_skills/gmp_skills/adapters/dsr_arm.py) |
| 그리퍼 | 기본 백엔드 DIO. 현 구성(마찰테이프 포함)의 A/B/C 폭 지문은 `16.5/17.5/18.5 mm`. DO1/2 개폐, 약통 `DI1=1`, 스쿱 `DI1=DI2=1` 뒤 0.8초 안정, 열림 `DI1=0`. 어댑터는 DIO 폭을 `-1`로 반환하고, `process_node`가 `fingerprint_tolerance_mm=0`의 지문을 FSM에 넘겨 현재 판정은 비활성화한다 | [stations.yaml](../../ros2_ws/src/gmp_bringup/params/stations.yaml), [common.yaml](../../ros2_ws/src/gmp_bringup/params/common.yaml), [SOT](../../docs/SOT.md), [process_node.py](../../ros2_ws/src/gmp_process/gmp_process/nodes/process_node.py), [rg2_gripper.py](../../ros2_ws/src/gmp_skills/gmp_skills/adapters/rg2_gripper.py) |
| 안전 자가진단 | 실물 기동·복구 때 등록 툴 `tool_weight`, TCP `GripperDA_v1`, 충돌 감도 50%를 조회해 모두 일치해야 통과한다. 자동 변경하지 않는다 | [common.yaml](../../ros2_ws/src/gmp_bringup/params/common.yaml), [dsr_arm.py](../../ros2_ws/src/gmp_skills/gmp_skills/adapters/dsr_arm.py) |
| 공구 설정 | B CURRENT의 동결 공구 설정을 참조한다. A 문서에는 수치를 복사해 유지하지 않는다 | [B CURRENT](../B/CURRENT.md), [PR #235](https://github.com/Jik-Kim/auto-pharmacist/pull/235) |
| 스쿱 최소 깊이 | `dosing.min_fraction`과 `scooping.A.min_fraction`은 두 yaml에서 같은 값이어야 하며, 일치는 [도징 시험](../../ros2_ws/src/gmp_dosing/test/test_dosing.py)이 강제한다 | [common.yaml](../../ros2_ws/src/gmp_bringup/params/common.yaml), [stations.yaml](../../ros2_ws/src/gmp_bringup/params/stations.yaml) |
| Scoop Action 종료 코드 | 내부 시간 초과는 ABORTED, 실제 클라이언트 취소만 CANCELED | [skill_node.py](../../ros2_ws/src/gmp_skills/gmp_skills/nodes/skill_node.py), [PR #236](https://github.com/Jik-Kim/auto-pharmacist/pull/236) |
| 계량 표본 품질 | `measure_force`/`measure_workpiece` 원시 표본열을 운영 계량 경로에서 받아 `fit_oscillation()` 잔차 σ와 고주파 σ를 각각 `max_std_g`/`max_hf_std_g` 게이트에 전달 | [skill_node.py](../../ros2_ws/src/gmp_skills/gmp_skills/nodes/skill_node.py), [dsr_arm.py](../../ros2_ws/src/gmp_skills/gmp_skills/adapters/dsr_arm.py), [#208](https://github.com/Jik-Kim/auto-pharmacist/issues/208) |

## 주문 시작 준비 (9/29 통합 반영)
- A의 숨은 안전 자세 MOVEJ와 `cartesian_ready` 제거. 이동 호출부에 명령을 직접 표시한다.
- 첫 주문: SafePose 성공 → 빈 통 파지. 후속 주문: 기존 넛지 대기 완료 → 같은 준비 순서.
- SafePose 요청을 시작하면 이전 스테이션의 `motion_anchor`를 즉시 무효화한다. 성공·실패 어느 경우에도 SafePose 전 출발 이력으로 티칭 이송하지 않는다.
- 철회(9/29): ~~C 연결 대기·미연결~~ → SafePose(BATCH_START) → RestoreGrip(empty) → 자가진단 → 빈 통 파지로 연결됐다. 주문 시작 SafePose는 정지 게이트를 거친다. [process_node.py](../../ros2_ws/src/gmp_process/gmp_process/nodes/process_node.py), [SOT](../../docs/SOT.md).

## 코드 탐색
- ROS 입출력: [skill_node.py](../../ros2_ws/src/gmp_skills/gmp_skills/nodes/skill_node.py).
- 실제 실행: [runtime.py](../../ros2_ws/src/gmp_skills/gmp_skills/execution/runtime.py)의 `handlers` → `motion/safety/scooping/weighing` 실행 객체. 공유 상태는 `execution/context.py` 한 곳에 둔다.
- 실행 진입점·ROS 계약·단일 워커 유지. [구조와 호출 순서](../../ros2_ws/src/gmp_skills/README.md).

## 검증 기준선
- 9/29 #293(계량 고주파 게이트) 위로 병합: #293 이 `skill_node` 에 넣은 원시 표본 사인 적합·`raw_hf_std`·`max_hf_std_g` 전달·빈 스쿱 기준선(적합 전 원본값) 저장을 `execution/weighing.py` 의 `_measure_weight_reading` 으로 옮겼다. 모의시험 **495건 통과**(이 PR 493 + #293 2건). 실물 구동 미수행.
- 9/28 명시적 이동 정리: **493건 통과**. 숨은 초기 MOVEJ 제거, SafePose→빈 통 접근 모의 순서 검증. C 주문 시작 연결은 미적용, 실물 구동 미수행.
- 9/28 실행 객체 분리: 회귀·구성 테스트 **490건 통과**, `colcon build --symlink-install --packages-select gmp_skills` 성공, 설치 후 기존 진입점·Job import 확인. 공정 그림 재생성 diff 없음. 실물 구동 미수행. [작업 일지](2026-09-28_스킬_호출흐름_주석.md)
- 9/26 #208 계량 품질 배선: `gmp_skills` 모의 시험 **490건 통과**. `gmp_process` 회귀까지 합쳐 634 passed / 2 skipped / 1 xfailed. 실물 Fz 표본의 적합 결과와 임계값 재검증은 남아 있다. [#208](https://github.com/Jik-Kim/auto-pharmacist/issues/208)
- 9/28 호출 흐름 및 함수 74개 역할 주석 추가: 실행 AST 동일·구문 검사 통과. 동작/현행값 변경 없음. [작업 일지](2026-09-28_스킬_호출흐름_주석.md)
- 9/28 `gmp_skills` 코드 주석을 입문자 관점에서 정리: 관절 분기·펜던트 이동·이동 경로·파지/안전/계량 상태의 검사 대상을 명시. 실행 동작·현행값 변경 없음. [작업 일지](2026-09-28_스킬_호출흐름_주석.md)
- 9/23 DRL 속도 정합화: `gmp_skills` 모의 테스트 **488건 통과**. 배율 1.0/0.2에서 관절·병진·회전 명령값, 초기 속도 설정, B 계측용 기존 생성자 호출 호환성을 확인했다. 공정 그림 재생성 diff 없음. 새 속도의 ROS 실물 검증은 미수행.
- `gmp_skills` 모의 테스트 **488건 통과**. Python 구문·YAML/XML 파싱·diff 공백 검사와 공정 다이어그램 재생성도 통과했다. [PR #290](https://github.com/Jik-Kim/auto-pharmacist/pull/290), [DRL·DIO 일지](2026-09-23_DRL_고정경로_DIO.md)
- 사용자 제공 DRL의 핵심 플로우는 실물 검증됐지만, **ROS로 이식한 경로의 통합 기동·실물 재검증은 수행하지 않았다.** [PR #277](https://github.com/Jik-Kim/auto-pharmacist/pull/277)

## 열린 과제 (이슈 번호)
- [#208](https://github.com/Jik-Kim/auto-pharmacist/issues/208) 계량 고주파 게이트 배선 — 원시 표본과 `raw_hf_std`를 `skill_node`에서 전달하는 A 작업은 9/26 완료(배선·설정·회귀시험, PR #293). 남은 일은 B [PR #219](https://github.com/Jik-Kim/auto-pharmacist/pull/219) 병합과 실물 표본 재검증.
- workbench 자세 모멘트와 material_1/2 계량 산포·무효율의 원인은 B 측정 분석을 참조한다. A는 실물 동선·파지 조건만 인계받는다. [B CURRENT](../B/CURRENT.md), [#187](https://github.com/Jik-Kim/auto-pharmacist/issues/187)
- `common.yaml`의 `robot.tool_name` 옆 공구 질량·CoG 주석은 두 세대 전 값이다. 변경 근거와 동결값은 B CURRENT·PR #235를 따른다.
- C 인계: 고정 Scoop의 접촉 미측정(`false/TAUGHT_FIXED`) 처리만 남았다([#287](https://github.com/Jik-Kim/auto-pharmacist/pull/287)). 첫 fraction 요청은 PR #289로 해소됐다. 반환 후 재스쿱은 미검증. `passbox_done → nudge_wait`는 아래 9/29 빈 그리퍼 시험 범위만 확인. [setup](../../docs/setup.md)

## 알려진 함정
- 공구 자동측정 결과를 임의로 펜던트에 다시 넣지 않는다(동결). `cz 89` 해석은 철회됐으며 근거는 [PR #235](https://github.com/Jik-Kim/auto-pharmacist/pull/235)다.
- `calibrated=false`를 Scoop 전체 비활성으로 읽지 않는다. `execution_mode=taught_fixed`와 `fixed_path.verified=true`가 고정 경로 실행 조건이다.
- workbench 빈 용기 진입은 EXIT Z≈330에서 하강하며, 로컬 AT→ABOVE만 Z=180이다. 둘을 같은 좌표로 가정하면 충돌 검토를 잘못한다.
- DIO에서는 폭이 `-1`이고 파지력 명령도 적용되지 않는다. `process_node`의 지문 허용치는 0이라 `WRONG_TOOL` 폭 지문이 동작한다고 보고하면 안 된다.
- DRL 원본 실물 검증과 ROS 이식본 실물 검증은 다르다. 현재 495건은 모의시험 기준선이다.
- `python3 tools/make_*.py` glob 호출 금지 — 첫 스크립트만 실행된다(AGENTS).

## 철회 이력 (최근 것 위)
- 2026-09-23 ~~용기 스테이션은 `solution_space=3`으로 ABOVE까지 `amovejx`~~ → DRL 티칭 `approach_posj` 진입과 직선 AT 접근·Z=330 이탈로 교체. [stations.yaml](../../ros2_ws/src/gmp_bringup/params/stations.yaml), [PR #277](https://github.com/Jik-Kim/auto-pharmacist/pull/277).
- 2026-09-23 ~~calibrated=false이면 모든 Scoop 실행 불가~~ → 고정 티칭 경로는 별도 verified로 실행한다. [SOT](../../docs/SOT.md) 9/23 사용자 승인.
- 2026-09-23 ~~G5 스쿠핑 보정 게이트: calibrated true 전환 후 데모~~ → PR #236 당시 높이 보정을 보류하고 빈 스쿱 Pour까지만 검증했으며, 이후 PR #277에서 높이 보정과 분리된 고정 티칭 경로를 도입했다. [PR #236](https://github.com/Jik-Kim/auto-pharmacist/pull/236), [PR #277](https://github.com/Jik-Kim/auto-pharmacist/pull/277).

## 넛지 이송 기본값 (2026-09-29)

- 사용자 경로 검증 완료 확인에 따라 `enabled: true`. 좌표와 출발·파지·도착 검사는 유지한다. [설정 원본](../../ros2_ws/src/gmp_bringup/params/stations.yaml), [일지](2026-09-29_넛지_이송_활성화.md).

- 당시 기록(아래 리뷰 반영으로 대체): 넛지 경로 `start_from: exit`: 놓기 후 후퇴 위치(Z=330)를 출발 기준으로 사용한다. 기존 ABOVE(Z=180) 비교를 제거하고 EXIT 자세·관절각·파지·이력 검사는 유지한다. 좌표 변경 없음, 변경 후 실물 시험 미수행. [원본](../../ros2_ws/src/gmp_bringup/params/stations.yaml), [일지](2026-09-29_넛지_이송_활성화.md).

- 철회(아래 #319 리뷰 반영): 9/29 후속 승인 당시 위 EXIT 관절각 대조 유지 결정은 철회한다. MoveToStation의 고정 출발 TCP·과거 관절각 대조를 제거하고 마지막 TCP 이력·파지·EXIT 및 목표 도달 검사를 유지한다. [결정](../../docs/SOT.md), [코드](../../ros2_ws/src/gmp_skills/gmp_skills/execution/motion.py). 변경 후 실물 미검증.
- 해당 변경 후 gmp_skills 모의 테스트 501건 통과. [검증 일지](2026-09-29_넛지_이송_활성화.md).

## #319 리뷰 반영 (2026-09-29)

- 위 「티칭/저장 관절각 대조 모두 제거」 결론은 철회한다. 티칭값 제거는 유지하되 실제 마지막 도착 관절각 비교를 복원한다.
- 2026-09-29 11:11:34~11:13:22 KST, 커밋 `2836c62`에서 빈 그리퍼·MoveToStation 배율 0.2로 단독 실물 1회 성공했다. passbox_done 접근→열기→EXIT 후퇴→nudge_wait AT→실제 NUDGE(|F|=37.05 N)→SafePose 완료. 근거: PR #319 본문 및 로봇 PC `/tmp/nudge-ros-logs/python3_119211_1790647891077.log` (첫 이동 ROS 시각 1790647894.705, 완료 1790648002.387). 용기 운반·실제 용기 놓기, 반복 신뢰성·정지 응답은 미검증이다. 이번 anchor 관절각 검사 복원 후 실물 재시험은 미수행이다.
- `reject_bin → nudge_wait` 미지원은 #99 후속. [SOT](../../docs/SOT.md), [시험 안내](../../tools/manual_place_nudge.md).
- 리뷰 수정 최종 검증: 다른 세션 변경을 제외한 임시 사본에서 gmp_skills **512 passed**, Python 문법·diff 검사 통과, 공정 생성기 산출물 변경 없음. [검증 일지](2026-09-29_넛지_이송_활성화.md).

## 인터락 재개 시 파지 상태 복구 (2026-09-29, C 승인)

`ENTER → SafePose 완료 → EXIT → RestoreGrip 성공 → 재개 승인` 순서입니다. 새 `/cell/restore_grip` 서비스는 센서와 중단 전 이력으로 파지 상태만 복구하며 이동·개폐 명령을 보내지 않습니다. 복구 실패·시간 초과·새 안전 정지 시 재개하지 않습니다. 실행 중 배치는 루프가 EXIT를 소비하며, 실행 루프 없는 수동 시험은 EXIT 성공 시 대기를 해제합니다. 스쿠핑·붓기·파지 등 불확실한 중단은 자동 복구 대상에서 제외합니다. 계량 기준선은 복구하지 않습니다. 계약은 [interfaces.md](../../docs/interfaces.md) v1.10을 따릅니다. 새 서비스 사용 전 gmp_interfaces·gmp_skills·gmp_process 재빌드와 bringup 재시작이 필요합니다. 실물 검증은 아직 하지 않았습니다.

- C 후속 검토 반영: RestoreGrip에 선택적 기대 파지/원료 요청과 scoop_extracted 응답을 추가했습니다. 기대값이 있으면 센서·저장 이력 결과와 일치해야 상태를 반영합니다. 기존 C 기본 요청은 유지합니다. carry 단계별 재개·기대값 전달·중복 EXIT 멱등 응답·복구 전용 시간 제한은 C 후속 연결 대상입니다. safe에서 cup 상태로 passbox_done ABOVE 진입은 현재 A 코드상 허용되며 실물 경로 검증과는 별개입니다.

## 빈 A 스쿱 정착 비교 (2026-09-29)

속도1.0·동일 파지·safe→material_1 경로에서 정착1초는0/3,10초는1/3 유효. 사인 적합 가드 미채택을 확인했지만10초만으로 해결되지 않았습니다. 파라미터 원본·B 공식은 변경 없음. 정착1초 원복·스쿱 반납·안전 자세 복귀 완료. [측정 근거](2026-09-29_빈스쿱_정착시간_비교.md).


## 원료 반환 후 스쿱 수납 (2026-09-29 사용자 승인)

ReturnMaterial 성공 후 해당 스쿱 AT 요청은 반환 끝 관절각을 확인하고
`material_N.posx → scoop_N ABOVE → return_entry_posx → 하강 → scoop_N.posx`로 이동한다.
그리퍼 열기 성공과 빈 그리퍼 후퇴 완료까지 확인한 뒤 다음 Scoop 차단을 해제한다.
반환 실패·취소, 중간 이동 실패, 열기 실패에서는 차단을 유지한다.
ReturnMaterial 자체는 여전히 반환 끝에서 종료하며, 수납 없이 같은 스쿱으로 바로 재스쿠핑하는
자동 공정 경로는 계속 차단된다. 실물 경로는 아직 시험하지 않았다.

2026-09-29 변경: 사용자 제공 `m0609_tw_return_material.drl`의 원료 1 방식을
원료 1·2·3에 적용했다. `return_start_posx → return_end_posx` 직선 이동 후
BASE 기준 진폭 X=14/Y=15 mm, 주기 X=0.3/Y=0.5초, 가속 0.5초, 3회 턴다.
기존 `return_end_posj`는 털기 종료와 수납 진입 시 관절각 확인에 사용한다.
코드와 설정에 반영했으며 세 원료의 실물 반환·간섭은 아직 재검증하지 않았다.

## 9/29 현장 통합 현행값

- 계량 경로별 gain: 용기 1.0975, 스쿱 0.983. settle_s=10.0, samples=20, period_s=0.82. 1초 설정 기록은 사용자 지시 누락으로 철회하며 실제 공정도 10초를 사용한다. [common.yaml](../../ros2_ws/src/gmp_bringup/params/common.yaml)
- 사인 적합 가드는 apply_ratio=0.50 유지. 도징 알고리즘 원본은 [scale.py](../../ros2_ws/src/gmp_dosing/gmp_dosing/core/scale.py)를 따른다.
- 붓기 후 잔량 계량 제거·추정 투입량 누적·최종 용기 검증 유지: [공정 문서](../../docs/process_flow.md).
- 반환 후 수납은 구현·모의 검증 완료, 실물 미검증. 실행 중 노드에는 재시작 후 적용된다. 자동 CLEANUP 연결 제약과 검증 결과는 [일지](2026-09-29_현장통합_반환수납.md) 참조.

## 운영 속도 정정 (2026-09-29 사용자 지시)

운영 배율은 1.0이다. common.yaml·cell.launch.py 및 수동 호출 도구의 기본 배율을 1.0으로 맞춘다. 스쿠핑 전후 계량 안정화는 모두 10초다. skill_node는 robot.vel_scale을 기동 시 실행 설정에 복사하므로 파라미터 값만 바꿔서는 기존 스쿠핑·인출 속도가 갱신되지 않는다. 재시작이 필요하다. 기존 0.3으로 실행한 부분 공정은 A 빈 스쿱 계량 도중 중단 요청했다.

## 9/29 반환 수납 실물 결과

속도 1.0·스쿠핑 전후 안정화 10초로 A/B/C 각각 계량·원료 반환·스쿱 수납 1회 성공했다. 순량 A 5.254 g, B 32.685 g, C 50.734 g. A 후계량은 첫 무효 뒤 두 번째 유효값을 채택했다. 저울 대조 정확도·RunBatch 전체·자동 CLEANUP은 미검증이다. 앞선 반환 수납 「실물 미검증」 기록은 이 한정된 성공 범위로 갱신한다. [측정 및 실행 일지](2026-09-29_현장통합_반환수납.md).

## 레시피 1 목표·허용오차 변경 (2026-09-29 사용자 승인)

운영 원본 `ros2_ws/src/gmp_bringup/params/recipes/recipe-01.yaml`과 HMI 시험 사본을 A/B/C 각각 target_g=69.0, tol_pct=15.0으로 맞췄다. 원료별 허용폭은 ±10.35 g, 합격 범위는 58.65~79.35 g이다. 합계 목표는 207 g, 최종 합계 허용폭은 ±31.05 g이다. 레시피 2·3과 원료별 스쿱 1회량 보정 설정은 유지한다. 직전 부분 공정 A 5.254 g·B 32.685 g·C 50.734 g은 새 범위에서도 모두 미달이다. 직접 RunBatch를 요청할 때는 Goal에도 각 target_g=69.0·tol_pct=15.0을 넣어야 한다. 이미 접수한 주문은 바뀌지 않는다.

## DRL 반환 수납 경로 정정 (2026-09-29)

사용자 제공 m0609_tw_return_material.drl의 return_material_N → scoop_N_return 전체를 대조했다. 앞서 추가한 scoop ABOVE 경유는 DRL에 없어 철회한다. 반환 끝 → material_N.posx → return_entry_posx → Z -100 → Y -150 → 열기 → Z +100 순서로 수정한다. 속도 1.0, 계량 안정화 10초를 유지한다. A/B/C 공통 반환 털기는 기존 승인 사항이다(DRL 원본의 털기는 A에만 있음). 수정한 수납 경로는 실물 재검증 전이다.

## 계량 후 수동 확인 대기

`tools/manual_weigh_return.py`는 운영 스킬을 속도 1.0·안정화 10초로 호출한다. 측정 결과(무효 포함)와 로그의 원시 표본을 records CSV에 매 계량마다 저장하고 fsync한다. 스쿠핑 후 유효 계량 뒤에는 Enter 전까지 반환·수납을 호출하지 않는다. 실제 저울 확인·원상 복귀 후 재개하며 process_node 동시 실행은 거부한다.

## 최신 저울 대조 결과 (9/29)

속도 1.0·안정화 10초·반환 전 Enter 대기로 A/B/C 반환·수납 완료. 로봇/저울 순량은 A 55.008/65 g, B 61.807/85 g, C 63.294/63 g이며 각각 -15.37%/-27.29%/+0.47% 차이다. 7회 원시 계량 140표본과 무효 포함 결과·저울값 CSV를 보존했다. 단일 회차여서 보정값은 변경하지 않았다. [일지](2026-09-29_현장통합_반환수납.md).
