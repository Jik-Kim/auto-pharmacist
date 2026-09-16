# gmp_hmi — 웹 HMI 와 배치 기록 DB [D HMI·기록]

평가 **「입출력 데이터 이해도」** 의 산출물. 주문 입력 절차, 계량값·일탈·기록 조회, **셀 밖에서의 QA 원격 승인**(R23) 이 여기서 보인다.

| 파일 | 하는 일 |
|---|---|
| `nodes/hmi_web_node.py` | Flask(메인 스레드) + rclpy(executor 스레드). `/status` 폴링, `/order` `/qa` `/interlock` → ROS 서비스, `/history` `/batch/<id>` `/kpi` `/audit` ← DB **읽기만** |
| `templates/index.html` | 대시보드 한 장 — 주문·상태·그리퍼·계량 그래프·분주 표·일탈 판정·이력·감사 추적. 외부 CDN 없음(현장 오프라인 대비) |
| `core/db.py` · `config/schema.sql` | SQLite 접근 계층 (ROS 비의존). 테이블 batches · items · weights · deviations · events(append-only) · audit |
| `nodes/record_node.py` | **단일 기록자.** 구독 5종 → DB. 배치 종료 시 `records/<batch_id>.json` 내보내기 |

## 규칙

- DB 에 쓰는 노드는 `record_node` 하나다. HMI 는 읽기만 — 쓰는 쪽이 둘이면 누가 썼는지 못 가린다.
- `events` 는 append-only. UPDATE/DELETE 메서드를 만들지 않는다 (감사 추적).
- 사람의 조작(주문·QA·인터락)은 `CellEvent(code='HMI_*', text='<actor> <detail>')` 로 발행 → `audit` 테이블. **`actor` 없이 누르면 unknown 으로 남는다** — 시연에서는 ID 를 넣는다.
- 의존: `sudo apt install python3-flask` (`docs/setup.md`).

## 접속

로봇 PC 에서 `http://localhost:5000`, **셀 밖 QA 는 같은 네트워크의 다른 기기에서 `http://<로봇PC IP>:5000`**. 시연 T6(c) 는 이 화면에서 승인/폐기를 누르는 장면이다.
