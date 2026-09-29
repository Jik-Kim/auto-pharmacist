# 놓기 → 넛지 → 안전 자세 수동 시험

`manual_place_nudge.py`를 실행하면 실제 로봇 명령을 보냅니다. pytest 단위 테스트가 아닙니다.
기존 FSM·bringup·스킬 구현은 변경하지 않고 기존 ROS Action/Service만 호출합니다.

## 시작 조건

- 로봇 bringup과 `skill_node`를 실행합니다. 같은 네임스페이스의 `process_node`는 종료해야 합니다.
  전체 `cell.launch.py`의 자동 공정과 동시에 실행하지 않습니다. 다른 수동 명령 클라이언트도 사용하지 않습니다.
- 용기를 이미 파지하고 있어야 합니다. `skill_node`에도 정상적인 용기 파지·출발 위치 이력이 있어야 합니다.
  펜던트로 잡거나 노드 재시작만으로는 파지 이력이 복원되지 않습니다. 이 시험은 용기를 새로 집지 않습니다.
- 검증된 `passbox_done → nudge_wait` 경로가 활성화된 설정을 `skill_node`가 로드해야 합니다.
- 실물 넛지가 활성화되어 있어야 합니다 (`safety.nudge_enabled=true`, `scale.simulated=false`).

## 실행

저장소 루트에서 ROS 및 프로젝트 오버레이 환경을 불러온 뒤 실행합니다.

```bash
source tools/env.sh
python3 tools/manual_place_nudge.py --vel-scale 0.2
```

순서: `passbox_done ABOVE → AT → 그리퍼 열기 → ABOVE → nudge_wait AT → 새 NUDGE 대기 → SafePose`.
넛지 위치 도착 이전 이벤트는 시간값으로 제외하며, 사람 입력에는 시간 제한을 두지 않습니다.
`SafePose` 속도는 실행 중인 skill_node 설정을 따릅니다. `--vel-scale`은 MoveToStation에만 적용됩니다.
오류·취소 시 후속 동작은 실행하지 않습니다. 진행 중 Action에는 취소를 요청합니다.
이미 접수된 그리퍼·SafePose 서비스는 취소 API가 없으므로 Ctrl+C로 물리 정지를 보장하지 않습니다.
이 스크립트는 배치 주문이나 완료 기록을 생성하지 않습니다.

## 수정 위치

- 순서 변경: `manual_place_nudge.py`의 `PlaceNudge.run()`.
- 이동 배율·응답 제한: 실행 인자 `--vel-scale`, `--timeout`.
- 위치·이송 경로: `ros2_ws/src/gmp_bringup/params/stations.yaml` (변경 후 skill_node 재시작).
- 넛지 힘·시간 기준: `ros2_ws/src/gmp_bringup/params/common.yaml`의 `safety.nudge_*`.

사용자 요청에 따라 작성 후 실행·테스트는 하지 않았습니다.
