# HMI 완료 여부 재검토 (2026-09-20)

대조: origin/main 7e25232, PR #41 f196a92. 원격 fetch 후 main 변동 없음.
근거: docs/SOT.md, docs/todo.md D 항목, docs/interfaces.md, 실제 HMI 코드.
9/21부터 할 일·이슈 정본은 GitHub Issues이며 todo.md는 동결된 과거 스냅샷이다. 아래는 작성 시점의 구현과 남은 일을 분리한 결과다.

| 항목 | 코드 확인 결과 |
|---|---|
| 인증·역할·QA·인터락·기록/통계·내보내기 | 구현 및 단위/시험 공정 검사 존재 |
| RunBatch 주문·상태/결과 | PR #41 구현. 실제 C/A 통합은 별도 |
| 안전복구 단일 버튼·D→C→A·이벤트 상관관계 | PR #41 구현. 팀 검토 및 실제 연동 필요 |
| v1.3 RETURN_MATERIAL·outcome 5/6 표시 | 구현 |
| 잔량·보충·높이 | 시험 공정은 구현. 운영 재고 계약/연동은 미완료 |
| 배치 중단 | 미구현. C cancel 정책·콜백과 함께 구현 필요 |
| 재기동 이어하기 | 미완료. active_batch 조회는 존재하지만 상태·원료 인덱스·tare 체크포인트 저장/조회는 없음. 안전정지 ERROR 배치 자동 재개와 구분 필요 |
| 회수 확인 | 감사 기록 구현. C 카운터 초기화는 미연동 |
| NUDGE | 표시 코드 존재. 실제 C 상태/이벤트 검증 필요 |
| 계량 목표선·허용오차 밴드 | 미구현. 현 WeightReading에는 batch_id/phase/목표가 없고 그래프는 여러 배치의 최근100개를 유지. 선택 레시피를 기존 계량값에 바로 덧씌우면 잘못된 판정선이 됨. HMI 주문 시 확정 레시피와 배치 문맥 보존부터 추가해야 함 |
| tools/report.py | 이번 후속 변경에서 읽기 전용 CLI 추가. /kpi와 같은 CellDB.kpis 사용 |
| 종료 중복 오류 | 이번 후속 변경에서 try_shutdown·executor 종료 후 자원 해제·정상 외부 종료 처리 추가 |

따라서 “다른 역할 연동 외에는 전부 완료”는 잘못된 보고다. 목표선/밴드 및
재기동용 D 기록 API도 남아 있으며, 영상/PPT는 소프트웨어 구현과 별도 일정이다.

## 보고 도구 실행

```bash
python3 ros2_ws/src/gmp_hmi/tools/report.py --db ~/auto-pharmacist/records/cell.db
```

`--json`, `--start`, `--end`, `--result`, `--query` 지원. 시각은 DB와 같은 ROS 초다.
운전시간/MTBI 산정은 현재 /kpi 정의를 그대로 따르며 새로운 정의를 만들지 않는다.

## 종료 검증 범위

개발환경에는 ROS가 없어 test_shutdown.py에서 실제 main 함수를 실행하되 ROS 객체를
스텁으로 대체한다. 정상 종료와 선행 context 종료, 정리 순서를 확인한다.
사용자 Jazzy 환경에서 재빌드 후 서버 기동→Ctrl+C→재기동하여 최종 확인해야 한다.
이 검사는 실제 DDS/SIGINT 또는 로봇 정지 검증을 대신하지 않는다.
