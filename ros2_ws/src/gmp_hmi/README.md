# gmp_hmi V4 — D HMI·기록

V3 재고 패치를 기준으로 두 화면(운전·모니터링 / 기록·통계), 디자인·인증·권한·QA·인터락·SQLite·검색·다운로드를 유지한다. V4는 원료별 1,000 g 보충, 높이 20% 미만 잠금, 레시피 3종을 추가한다. 상세 규칙은 `docs/test_inventory.md`, Git 대조는 `docs/interface_alignment.md`.

**완성된 재고·높이·보충 흐름은 `/hmi_test`와 브라우저 데모용이다. 실제 `/cell`의 주문은 C 공정으로 전달한다. 시험 재고·높이는 운영 주문 게이트에 적용하지 않는다.** 기존 상태·QA·인터락·기록은 유지한다. 실제 C/로봇 코드·공용 메시지·좌표·도징 설정은 변경하지 않는다.

시험 레시피(`config/test_recipes/v4`): `recipe-01` A40/B40/C40, `recipe-02` A80/B40, `recipe-03` A40/B40/C80. 공통 C 로더와 기존 5% 오차를 사용한다. 실물 정밀도는 미검증. 시험 목록에는 새 3종만 표시한다. 운영 레시피는 `gmp_bringup/params/recipes`를 단일 출처로 사용한다.

## 빌드

```bash
source /opt/ros/jazzy/setup.bash
cd ~/auto-pharmacist/ros2_ws
colcon build --symlink-install --packages-up-to gmp_hmi &&
source install/setup.bash &&
python3 -m pytest -q src/gmp_hmi/test
```

## 기본 시험 실행

```bash
source /opt/ros/jazzy/setup.bash
source ~/auto-pharmacist/ros2_ws/install/setup.bash
export ROS_DOMAIN_ID=88
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
export GMP_HMI_ADMIN_USER=admin
read -rsp '시험 관리자 비밀번호(10자 이상): ' GMP_HMI_ADMIN_PASSWORD
printf '\n'
export GMP_HMI_ADMIN_PASSWORD
ros2 launch gmp_hmi hmi_comm_test.launch.py
```

http://127.0.0.1:5002 — 각 원료 1,000 g으로 시작한다. 실행마다 새 시험 DB·계정을 만들며 운영 데이터는 유지한다. `/demo`는 브라우저 메모리 전용이고 ROS/SQLite 시험이 아니다.

## 자동 검사

부족을 빠르게 재현하려고 A만 80 g으로 시작한다. 기존 시험 launch를 종료하고 같은 터미널에서:

```bash
ros2 launch gmp_hmi hmi_comm_test.launch.py \
  test_initial_g:='[80.0,1000.0,1000.0]' item_duration_s:=2.0
```

다른 터미널에서 위와 같은 ROS 환경·계정·비밀번호를 설정한 뒤:

```bash
python3 ~/auto-pharmacist/ros2_ws/src/gmp_hmi/tools/verify_ros_http.py
```

검사 중 웹 주문을 별도로 누르지 않는다. 재검사는 새 launch에서 한다. 검사 후 기본 launch로 재시작하면 1,000 g 시연 화면이 된다.

높이 부족 수동 재현:

```bash
ros2 topic pub --once /hmi_test/test_height std_msgs/msg/String \
  "{data: '{\"material_id\":\"A\",\"height_pct\":19.9}'}"
```

팝업을 닫아도 차단 유지 → 진행 중이면 ENTER 허가 → A 개별 보충 확인 → PAUSED 유지 → EXIT를 확인한다. 실제 힘·높이·안전 허가는 실물에서 별도 검증해야 한다.

## 유지 규칙

DB 단일 기록자는 `record_node`. 계정·화면 설정 JSON은 공정 기록과 분리되며 설정 변경은 보충이 아니다. 로그인 계정 actor를 서버가 정하고 viewer/qa는 보충을 실행할 수 없다. 실운영 재고·높이·보충·재시작 복구는 TODO다. 실제 C 연동이 필요한 항목은 `docs/interface_alignment.md`에 기록한다.
