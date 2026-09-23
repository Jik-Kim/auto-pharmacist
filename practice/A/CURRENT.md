# A 스킬·로봇 현행 상태 (갱신: 2026-09-23, 문서 관리 팀장 초안 — A 가 보완)

**담당**: jonnykoh2008-ship-it

> 세션 시작 때 이 파일을 읽는다. 값을 쓰기 전에 근거 링크의 원본을 직접 연다.

## 지금 유효한 값
| 항목 | 값 | 근거 |
|---|---|---|
| 스쿠핑 | A/B/C `execution_mode=taught_fixed`, `fixed_path.verified=true`, `calibrated=false`(높이 보정만 차단), depth_fraction=1 | [stations.yaml](../../ros2_ws/src/gmp_bringup/params/stations.yaml), [SOT](../../docs/SOT.md) 9/23 |
| 원료 A 원료면 | `material_1.surface_z_base_mm = 90.0` (사용자 지정값, 센서값 아님) | `stations.yaml`, PR #236 |
| Pour 경로 | middle → ABOVE Z290 → start Z243 → end Z320 → ABOVE → Z+50 → middle | [stations.yaml](../../ros2_ws/src/gmp_bringup/params/stations.yaml), [skill_node](../../ros2_ws/src/gmp_skills/gmp_skills/nodes/skill_node.py) |
| 공구 설정 | B CURRENT 참조(1.36 kg · CoG 5.31/−34.68/8.28, 동결) | PR #235 |
| Scoop Action 종료 코드 | 내부 시간 초과 ABORTED, 클라이언트 취소만 CANCELED | PR #236 |
| 그리퍼 | DIO, DO1/2 개폐, 약통 DI1=1 / 스쿱 DI1=DI2=1 후 0.8s / 열림 DI1=0. 파지력 명령 없음, 지문 tolerance=0 | [common.yaml](../../ros2_ws/src/gmp_bringup/params/common.yaml) |

## 검증 기준선
- skills 모의 테스트 **474건 통과**. ROS 실물 이식은 미검증. [작업 일지](2026-09-23_DRL_고정경로_DIO.md)

## 열린 과제 (이슈 번호)
- #208 계량 고주파 σ(`measure_force` 원시 표본) + `skill_node` 가 `raw_hf_std` 전달 → #219 리베이스.
- #111 (a)(c) 외 남은 것 없음. #222 `min_fraction` 중복 선언.
- workbench 자세 Mx ≈ +0.9 Nm 원인, material_1/2 계량 σ·무효율 기전(B 인계분).
- common.yaml:34 공구 주석 정정(두 세대 전 값).

- C 인계: 고정 Scoop의 접촉 미측정(false/TAUGHT_FIXED) 처리와 첫 fraction 요청. 반환 후 재스쿱·넛지 경로는 미검증. [setup](../../docs/setup.md)

## 알려진 함정
- 공구 자동측정 결과를 임의로 펜던트에 다시 넣지 않는다(동결). cz 89 는 철회됐다.
- `python3 tools/make_*.py` glob 호출 금지 — 첫 스크립트만 실행된다(AGENTS).

## 철회 이력 (최근 것 위)
- 2026-09-23 ~~calibrated=false이면 모든 Scoop 실행 불가~~ → 고정 티칭 경로는 별도 verified로 실행한다. [SOT](../../docs/SOT.md) 9/23 사용자 승인.
- 2026-09-23 ~~G5 스쿠핑 보정 게이트: calibrated true 전환 후 데모~~ → 보류, calibrated=false 로 데모(빈 스쿱 Pour 검증까지). PR #236·#220.
