# V4 인터페이스 대조 결과

## 확인한 Git 기준

- 저장소: `Jik-Kim/auto-pharmacist`
- 브랜치: `main`
- 커밋: `bcdcf0718b2b1cfaf038f329552f39c409075d9e`
- 커밋 시각: **2026-09-18 09:08:48 UTC / 18:08:48 KST**
- 커밋 내용: `magazine + output_tray를 passbox로, scale을 workbench로 개명`
- GitHub 연결로 `main`을 다시 조회하고 기준 커밋과 비교했다. 결과는 **identical, ahead 0 / behind 0**이었다.
- 이 보고서는 파일·계약 대조 결과다. 실제 C 공정 또는 실물 로봇 연동 시험 결과가 아니다.

## 원본 정의 13개 대조

Git의 해당 커밋에서 아래 13개 파일을 직접 읽고, 검증에 사용하는 로컬 원본과 비교했다. **파일 끝 개행 차이를 제외한 내용이 13/13 일치했다.** 공유 정의는 수정하지 않았다.

| 구분 | 원본 경로 |
|---|---|
| 메시지 9개 | `ros2_ws/src/gmp_interfaces/msg/{CellEvent,CellState,Deviation,DispenseResult,GripperState,Recipe,RecipeItem,ScoopCycle,WeightReading}.msg` |
| 서비스 3개 | `ros2_ws/src/gmp_interfaces/srv/{SubmitOrder,QaDecision,InterlockRequest}.srv` |
| 레시피 검증기 | `ros2_ws/src/gmp_process/gmp_process/core/recipe.py` |

## V4가 맞춘 항목

| 항목 | 적용 기준 |
|---|---|
| 레시피 | C의 `recipe.load()` 사용. `product`, `items[]` 형식 유지 |
| 원료 항목 | `material_id`, `target_g`, `tol_pct`만 사용. 제거된 `grade`, `scoop_id`를 전송하지 않음 |
| 원료·순서 | 대문자 `A/B/C`, 배열 순서 그대로 투입. `recipe-02`는 A·B만 포함하고 C 항목 생략 |
| 원료 위치 | `stations.yaml`의 A→`material_1`/`scoop_1`, B→`material_2`/`scoop_2`, C→`material_3`/`scoop_3` 매핑 유지. 레시피에는 로봇 좌표를 넣지 않음 |
| 최신 위치 이름 | 시험 공정·브라우저 데모의 계량 위치는 `workbench`, 완료품 위치는 `passbox_done`. 시험용 안전 위치 표시는 실제 티칭 좌표가 아님 |
| 주문 서비스 | `SubmitOrder.Request(recipe=...)`; 응답의 `accepted`, `batch_id`, `message` 사용 |
| QA 서비스 | `QaDecision.Request(deviation_id, decision, operator_id)` 사용. 요청에서 제거된 `batch_id`는 보내지 않으며 HMI에서 현재 배치와 일탈을 검사 |
| ENTER/EXIT | `InterlockRequest.Request(request, reason)` 사용. 상수는 실제 `.srv`의 응답 구역에 정의되어 있어 `InterlockRequest.Response.ENTER/EXIT`로 접근 |
| 계량 | `WeightReading.subject`, `samples`, 유효 여부와 원본 계량값 보존 |
| 분주·일탈 | `DispenseResult`의 OK/UNDER/OVER 및 `Deviation`의 0~11 종류·판정·판정자 매핑 유지 |
| 스쿱 기록 | `ScoopCycle`의 1회 시도, 누적 투입량, 세 계량값, 6축 통계, 독립 기준 유효성 등의 원본 필드 기록 |
| 토픽 | 기존 상대 토픽 이름과 QoS 유지. `/hmi_test` 시험 기능을 실제 `/cell` 계약으로 등록하지 않음 |

레시피 3종은 01=A40/B40/C40g, 02=A80/B40g, 03=A40/B40/C80g이며 원료별 허용 오차는 기존 5%다. 이는 시연 설정이며 실제 40g 분주와 ±2g 정밀도가 실물에서 검증됐다는 뜻은 아니다.

## 실제 C 연동에 남은 사항

### 1. 재고·높이·개별 보충의 공식 계약 없음

현재 `gmp_interfaces`와 `docs/interfaces.md`에는 원료별 잔량·높이 비율·보충 완료를 전달하는 공식 메시지/서비스가 없다. `MATERIAL_EMPTY` 일탈은 있으나 원료별 높이 비율과 만충 확인을 대신하지 않는다.

V4의 `test_inventory`, `test_height`, `test_refill_A/B/C`는 **`/hmi_test` 한정 시험 계약**이다. 실제 C에 연결됐다고 표시하지 않으며, 실제 모드의 새 주문은 재고·높이·보충 연동이 마련될 때까지 차단한다. C가 잔량과 부족 잠금의 권위 있는 상태를 관리하고 보충 수락을 응답하는 계약이 필요하다.

### 2. 폐기 판정과 물리적 완료를 구분해야 함

현재 `gmp_process/gmp_process/core/process_fsm.py`의 `_after_qa()`는 폐기 이송 전에 `mode='DONE'`, `state='DISCARDED'`를 설정한다. 이후 폐기 carry 결과 처리에서도 `state='DONE'`으로 바꾸지 않고 종료한다.

따라서 V4는 `DONE/DISCARDED`를 물리적 완료로 기록하지 않는다. **C가 용기 배출·폐기 이송을 끝낸 뒤 `mode=DONE`, `step=DONE`을 발행해야** 완료 기록과 다음 주문 판단을 확정할 수 있다. 최종 QA 판정에 따라 DB 결과는 DONE 또는 DISCARDED로 구분한다. 현재 C 상태 그대로라면 폐기 배치는 완료 확인 대기로 남는다.

### 3. 현재 C 실행 노드는 아직 골격

Git의 `gmp_process/gmp_process/nodes/process_node.py`에는 다음 구현 공백이 남아 있다.

- `_execute()`의 실제 스킬 호출이 TODO이며 미구현 경로는 `NotImplementedError`를 발생시킨다.
- `_srv_interlock()`의 ENTER→safe_pose 성공→granted 연동이 TODO다.
- `_srv_qa()`는 현재 `deviation_id` 일치 검사와 판정자 전달을 구현하지 않았다.
- `ScoopCycle` 메시지 정의는 있지만 현재 공정 노드에 해당 토픽 발행 연결이 없다.

이 항목들은 공유 인터페이스나 로봇 코드를 임의로 변경해 메우지 않았다. V4의 시험 통과는 **실제 HMI·기록 코드와 시험 공정의 동작 검증**이며 위 C 구현의 완료를 의미하지 않는다.

## 참조

- Git 기준: https://github.com/Jik-Kim/auto-pharmacist/commit/bcdcf0718b2b1cfaf038f329552f39c409075d9e
- 정의 원본: `ros2_ws/src/gmp_interfaces`
- 계약 문서: `docs/interfaces.md`
- 위치 매핑: `ros2_ws/src/gmp_bringup/params/stations.yaml`
- 레시피 원본 검증기: `ros2_ws/src/gmp_process/gmp_process/core/recipe.py`
- C 구현: `ros2_ws/src/gmp_process/gmp_process/core/process_fsm.py`, `ros2_ws/src/gmp_process/gmp_process/nodes/process_node.py`
