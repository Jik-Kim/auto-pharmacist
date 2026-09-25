#!/usr/bin/env bash
# 스쿱 1회량 측정 (#272) — 붓기 방식. 저울은 workbench 용기 자리에 둔다.
#   MAT=A ./scoop_run.sh setup       스쿱 잡고 원료통 계량 자세까지 (1회)
#   MAT=A ./scoop_run.sh run 15      Scoop -> Pour -> 저울값 입력 -> 복귀 를 15회
#   MAT=A ./scoop_run.sh park        스쿱 반납하고 safe 로
# MAT 은 A|B|C (기본 A). 원료마다 스쿱 폭이 15.5/18.0/28.0 mm 로 달라 1회량이 다르므로
# 원료별로 따로 재고, 기록지도 원료별로 따로 둔다 (scoop_sigma_<날짜>_mat<MAT>.csv).
# 실패하면 그 자리에서 멈춘다. 다음 단계로 안 넘어간다.
# set -u 는 쓰지 않는다 — /opt/ros/jazzy/setup.bash 가 AMENT_TRACE_SETUP_FILES 같은
# 미설정 변수를 참조해서 source 하는 순간 죽는다 (9/23 실물에서 당함).
set -o pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd ~/auto-pharmacist || { echo "auto-pharmacist 경로 없음"; exit 1; }
if ! source tools/env.sh; then echo "!! env.sh source 실패"; exit 1; fi
if ! command -v ros2 >/dev/null; then echo "!! ros2 명령이 없다 — env.sh 가 제대로 안 먹었다"; exit 1; fi

MAT="${MAT:-A}"
case "$MAT" in
  A) SCOOP=scoop_1; MATST=material_1 ;;
  B) SCOOP=scoop_2; MATST=material_2 ;;
  C) SCOOP=scoop_3; MATST=material_3 ;;
  *) echo "MAT 은 A|B|C 중 하나여야 한다 (지금: $MAT)"; exit 2 ;;
esac
V="${VEL:-}"                      # VEL=0.3 ./scoop_run.sh trial 처럼 덮어쓸 수 있다
vs() { [ -n "$V" ] && echo ", vel_scale: $V" || echo ""; }

act() {   # act <이름> <액션> <타입> <goal>
  echo "── $1"
  local out
  out=$(ros2 action send_goal "$2" "$3" "$4" 2>&1)
  if grep -q 'status: SUCCEEDED' <<<"$out"; then
    grep -E 'success|message|reading|net_g|gross_g' <<<"$out" | head -4
    return 0
  fi
  echo "$out" | tail -12
  echo "!! $1 실패 — 여기서 멈춥니다"
  return 1
}

srv() {   # srv <이름> <서비스> <타입> <요청> <성공검사>
  echo "── $1"
  local out
  out=$(ros2 service call "$2" "$3" "$4" 2>&1)
  echo "$out" | tail -2
  grep -q "$5" <<<"$out" || { echo "!! $1 실패 — 여기서 멈춥니다"; return 1; }
  return 0
}

move() { act "이동 $1 ($2)" /cell/move_to_station gmp_interfaces/action/MoveToStation \
              "{station_id: $1, approach: $2$(vs)}"; }

case "${1:-}" in
setup)
  echo "### 준비 — 로봇이 크게 움직입니다. 비상정지 확인하고 Enter (취소는 Ctrl-C)"; read -r _
  move safe 1                                     || exit 1
  move $SCOOP 0                                  || exit 1
  VEL_AT="${VEL_AT:-0.3}"
  act "이동 $SCOOP (AT, $VEL_AT)" /cell/move_to_station gmp_interfaces/action/MoveToStation \
      "{station_id: $SCOOP, approach: 1, vel_scale: $VEL_AT}" || exit 1
  srv "스쿱 파지" /cell/set_gripper gmp_interfaces/srv/SetGripper \
      "{close: true, width_mm: 0.0, force_n: 20.0, timeout_s: 15.0}" 'grip_inferred=True' || exit 1
  act "인출 + 원료통 이동" /cell/weigh_held gmp_interfaces/action/WeighHeld "{tare_g: 0.0}" || exit 1
  echo; echo "### 준비 끝. 저울에 빈 용기 올리고 tare 치세요."
  echo "### 그다음부터 회차마다:  ./scoop_run.sh trial"
  ;;
trial|run)
  N="${2:-1}"
  CSV="${CSV:-$HERE/records/scoop_sigma_$(date +%m%d)_mat$MAT.csv}"
  mkdir -p "$(dirname "$CSV")"
  if [ ! -f "$CSV" ]; then
    VS=$(ros2 param get /cell/skill_node robot.vel_scale 2>/dev/null | grep -o '[0-9.]*$')
    { echo "# 스쿱 1회량 (#272) — 원료 $MAT · 붓기 방식 · $(date +%Y-%m-%d)"
      echo "#   vel_scale ${VS:-?}  (ros2 param get /cell/skill_node robot.vel_scale)"
      echo "#   스쿱 $SCOOP · 원료통 $MATST · execution_mode taught_fixed · depth_fraction 1.0"
      echo "#   계량: 외부 저울. 로봇 계량값은 쓰지 않는다 (scale.gain 미검증)"
      echo "#   ⚠️ vel_scale 을 바꾸면 채취량이 바뀐다. 다른 값으로 잰 회차를 섞지 말 것."
      echo "#"
      echo "# 용기_g: "
      echo "#   ↑ 빈 시료통을 한 번 정확히 재서 적는다. 비어 있으면 분석이 거부한다."
      echo "#"
      echo "회차,총무게_g,원료면,비고"
      for i in $(seq 1 15); do echo "$i,,,"; done
    } > "$CSV"
    echo "### 기록지 새로 만듦: $CSV"
  fi
  python3 "$HERE/record.py" "$CSV" check || exit 1
  for ((k=1; k<=N; k++)); do
    NO=$(python3 "$HERE/record.py" "$CSV" next)
    [ "$NO" = "0" ] && { echo "### 기록지가 다 찼습니다."; break; }
    echo; echo "=================== 회차 $NO  ($k/$N) ==================="
    act "Scoop" /cell/scoop gmp_interfaces/action/Scoop \
        "{material_id: '$MAT', attempt: 1, depth_fraction: 1.0}" || exit 1
    act "Pour" /cell/pour gmp_interfaces/action/Pour "{fraction: 1.0}" || exit 1
    while :; do
      echo
      read -r -p ">>> 저울값(총무게 g)  [s=이 회차 버림, q=중단]: " W
      case "$W" in
        q|Q) echo "중단합니다."; exit 0 ;;
        s|S) echo "회차 $NO 버림 — 기록하지 않습니다."; break ;;
        *) read -r -p ">>> 원료면 [가득/중간/바닥, Enter=생략]: " SF
           if python3 "$HERE/record.py" "$CSV" put "$W" "$SF" ""; then break; fi
           echo "다시 입력하세요." ;;
      esac
    done
    echo ">>> 시료통 비우고 제자리에 두세요."
    if [ "${FAST:-0}" = "1" ]; then
      # WeighHeld 는 안 쓰는 무게를 17초 동안 잰다 (samples 20 x 0.82s + settle).
      # 복귀만 필요하면 material_1 AT 로 바로 간다 — Scoop 의 시작 조건이 그 자세다.
      move "$MATST" 1 || exit 1
    else
      act "원료통 복귀" /cell/weigh_held gmp_interfaces/action/WeighHeld "{tare_g: 0.0}" || exit 1
    fi
  done
  echo; echo "### 끝. 분석:"
  echo "python3 $HERE/../scoop_sigma.py $CSV"
  ;;
park)
  move $SCOOP 0                                  || exit 1
  act "이동 $SCOOP (AT, 0.3)" /cell/move_to_station gmp_interfaces/action/MoveToStation \
      "{station_id: $SCOOP, approach: 1, vel_scale: 0.3}" || exit 1
  srv "스쿱 놓기" /cell/set_gripper gmp_interfaces/srv/SetGripper \
      "{close: false, width_mm: 0.0, force_n: 0.0, timeout_s: 15.0}" 'success=True' || exit 1
  move $SCOOP 0                                  || exit 1
  move safe 1                                     || exit 1
  echo "### 반납 완료"
  ;;
*)
  echo "사용법: MAT=A|B|C  $0 setup | $0 run <회차수> | $0 park"; exit 2 ;;
esac
