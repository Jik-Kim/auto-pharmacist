# 영역별 책임

| 패키지 | 담당 | 핵심 파일 | 한 줄 책임 |
|---|---|---|---|
| `gmp_interfaces` | 조장 고희태 (리뷰: 영향받는 담당 전원) | `msg/*.msg`, `srv/*.srv`, `action/*.action` | 노드 간 계약. 바꾸면 `docs/interfaces.md` 동시 갱신 |
| `gmp_skills` | **A 고희태** [스킬] | `nodes/skill_node.py`, `adapters/dsr_arm.py`, `adapters/rg2_gripper.py`, `core/stations.py` | **로봇을 만지는 유일한 노드.** 스테이션 이동·파지·스쿱·붓기·계량 스킬, 워커 스레드, 안전 자세 |
| `gmp_dosing` | **B 김민준** [도징] | `core/scale.py`, `core/dosing.py`, `test/` | 힘·작업물무게 → 그램(영점·보정), 이중 폐루프 도징 정책. **ROS 비의존 — 로봇 없이 pytest 로 완성한다** |
| `gmp_process` | **C 김병직** [공정] | `nodes/process_node.py`, `core/process_fsm.py`, `core/recipe.py`, `core/deviation.py` | 레시피 실행 상태기계, 일탈 분기, 인터락, `RunBatch`/`SubmitOrder`/`QaDecision`/`InterlockRequest` 서버 |
| `gmp_hmi` | **D 서동권** [HMI·기록] | `nodes/hmi_web_node.py`, `templates/index.html`, `nodes/record_node.py`, `core/db.py`, `config/schema.sql` | 웹 HMI(주문·상태·계량 그래프·**원격 QA 판정**·인터락·이력·감사 추적), 배치 기록 SQLite — **평가 「입출력 데이터 이해도」의 산출물** |
| `gmp_bringup` | 조장 고희태 | `launch/cell.launch.py`, `params/*.yaml`, `params/recipes/` | real/virtual 실행, 파라미터 단일 출처, 스테이션 좌표(티칭값은 A 가 적는다) |

`core/` 는 ROS 비의존이므로 단위 테스트 대상이다. `nodes/`·`adapters/` 는 가상 모드·실물 테스트 대상이다.

## 하드웨어·현장 (패키지 밖)

| 일 | 담당 | 비고 |
|---|---|---|
| 스테이션 물리 배치 (판 820×650, `PROJECT_RULES.md` 3-1), 원료통·스쿱·용기·트레이 고정 | C + 전원 | 9/17 오전. 판이 밀리면 전부 틀어진다 — 테이프로 위치를 표시 |
| 티칭 (`stations.yaml`) | A | 9/17. 판 좌표계(D-15) 를 쓰면 3점만 다시 찍으면 된다 |
| 툴·TCP 등록 확인 (`tool_weight` 1.320 kg, `GripperDA_v1`) | A | 완료 (R7) — 기동 자가진단이 다시 확인한다 |
| 구매물 (스쿱·비드·용기·트레이·저울) | 팀 | 9/16 주문 (R20) |
| 영상·PPT·BRD·SDD | 조장 + D (전원 검토) | 9/24~28 휴강 |

## 리뷰

| 변경 | 리뷰어 |
|---|---|
| 계약 (`gmp_interfaces`, `docs/interfaces.md`) | 영향받는 담당 전원, 최소 2명 |
| `gmp_skills` | C (공정이 스킬을 부르는 쪽) |
| `gmp_dosing` | A (측정값을 만드는 쪽) |
| `gmp_process` | D (상태를 표시하는 쪽) |
| `gmp_hmi` | B |

**PM 은 없다.** 조장은 일정·통합·막힐 때의 판단을 맡고 A~D 중 한 영역을 겸한다. 리뷰는 위 교차 표대로 서로 본다 (`AGENTS.md` 교차검수). 부담당 짝: **A↔C, B↔D** — 리뷰어 부재 시 대신 보고, 통합일에 서로의 패키지를 띄울 수 있어야 한다. 실명 확정 9/16: A 고희태(조장) · B 김민준 · C 김병직 · D 서동권.
