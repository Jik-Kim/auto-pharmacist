# stations.yaml 티칭펜던트 위치 시험

`stations_flow_test.drl`은 ROS 공정과 별개로 스테이션의 **접근점만** 한 점씩 점검하는 파일입니다.
그리퍼 명령, 파지/놓기, 힘제어, AT 작업점 하강은 넣지 않았습니다.

## 실행 전 확인

1. 로봇과 그리퍼 전원을 켜고, 작업반경에서 사람과 장애물을 모두 치웁니다.
2. 티칭펜던트에서 `tool_weight`, TCP `GripperDA_v1`, BASE 좌표계를 확인합니다.
3. 수동·저속 모드로 두고, 로봇을 현재 위치에서 직선 이동해도 간섭 없는 자세로 직접 옮깁니다.
4. USB 등 컨트롤러가 허용하는 방식으로 DRL 파일을 가져옵니다. `TEST_STEP`을 한 번호만 바꾼 뒤 실행합니다.
5. 한 점의 실제 TCP 자세와 주변 간섭을 확인한 뒤에만 다음 번호로 진행합니다.

`TEST_STEP=0`은 이동하지 않습니다. 첫 시험은 1번부터 하지 말고, 현재 자세에서 직선 경로가 명확히 안전한 점만 선택하십시오.

## 점검 순서

| 번호 | 접근점 | 공정상 위치 |
| --- | --- | --- |
| 1 | `passbox_empty` ABOVE | 빈 약통 칸 |
| 2 | `workbench` ABOVE | 용기 파지·계량 위치 |
| 3, 4 | `material_1`, `scoop_1` ABOVE | 원료 A·전용 스쿱 |
| 5, 6 | `material_2`, `scoop_2` ABOVE | 원료 B·전용 스쿱 |
| 7, 8 | `material_3`, `scoop_3` ABOVE | 원료 C·전용 스쿱 |
| 9, 10 | `pour_start`, `pour_end` | 붓기 시작·종료 자세 |
| 11 | `passbox_done` ABOVE | 완성품 칸 |
| 12 | `reject_bin` ABOVE | 폐기함 |

## 별도 티칭이 필요한 항목

- `safe.posx`는 자리표시자라 시험하지 않습니다.
- `material_1~3.return_start_posx`, `return_end_posx`는 아직 `null`입니다.
- `nudge_wait`는 `passbox_done`에서의 검증된 관절 이송 경로로만 접근해야 합니다. 단독 `movej` 시험은 하지 마십시오.
- 접근점이 확인된 뒤에 AT 작업점 하강·파지/놓기·원료 접촉을 시험합니다. 이 단계는 실제 지그·용기·스쿱 간섭을 보며 별도로 티칭해야 합니다.
