# A 스킬·로봇 현행 상태 (갱신: 2026-09-23, 문서 관리 팀장 초안 — A 가 보완)

**담당**: jonnykoh2008-ship-it

> 세션 시작 때 이 파일을 읽는다. 값을 쓰기 전에 근거 링크의 원본을 직접 연다.

## 지금 유효한 값
| 항목 | 값 | 근거 |
|---|---|---|
| 스쿠핑 보정 `scooping.A.calibrated` | **false 유지** — 최초 접촉 정지·원료 높이 측정은 운영에서 비활성화(힘 측정 불신·스쿱 상대 회전). 설정·단위 시험만 유지 | PR #236, SOT 「원료 높이 측정 보류」, demo G5 「보류」 |
| 원료 A 원료면 | `material_1.surface_z_base_mm = 90.0` (사용자 지정값, 센서값 아님) | `stations.yaml`, PR #236 |
| Pour 경로 | 시작점 위 `workbench.approach_mm` 50 → ABOVE Z 288 → 시작점(Z 238) → 종료(Z 320) → 0.5 s → 시작점 | PR #236, SOT 「병 마운트 5mm」 |
| 공구 설정 | B CURRENT 참조(1.36 kg · CoG 5.31/−34.68/8.28, 동결) | PR #235 |
| Scoop Action 종료 코드 | 내부 시간 초과 ABORTED, 클라이언트 취소만 CANCELED | PR #236 |

## 열린 과제 (이슈 번호)
- #208 계량 고주파 σ(`measure_force` 원시 표본) + `skill_node` 가 `raw_hf_std` 전달 → #219 리베이스.
- #111 (a)(c) 외 남은 것 없음. #222 `min_fraction` 중복 선언.
- workbench 자세 Mx ≈ +0.9 Nm 원인, material_1/2 계량 σ·무효율 기전(B 인계분).
- common.yaml:34 공구 주석 정정(두 세대 전 값).

## 알려진 함정
- 공구 자동측정 결과를 임의로 펜던트에 다시 넣지 않는다(동결). cz 89 는 철회됐다.
- `python3 tools/make_*.py` glob 호출 금지 — 첫 스크립트만 실행된다(AGENTS).

## 철회 이력 (최근 것 위)
- 2026-09-23 ~~G5 스쿠핑 보정 게이트: calibrated true 전환 후 데모~~ → 보류, calibrated=false 로 데모(빈 스쿱 Pour 검증까지). PR #236·#220.
