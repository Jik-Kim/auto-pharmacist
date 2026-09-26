#!/usr/bin/env python3
"""RunBatch + safety popup manual gate + complete ROS/HTTP/SQLite verification.

Run after sourcing ROS Jazzy and the auto-pharmacist overlay.  The script uses
only /hmi_test and port 5002, creates a temporary database through the existing
hmi_comm_test launch file, and never connects to the physical robot.
"""
import getpass
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from urllib.request import ProxyHandler, build_opener


ROOT = Path(__file__).resolve().parents[4] if "ros2_ws" in Path(__file__).parts else Path.cwd()
TOOLS = ROOT / "ros2_ws" / "src" / "gmp_hmi" / "tools"
sys.path.insert(0, str(TOOLS))

try:
    from verify_ros_http import CheckFailed, RosHttpCheck
except ImportError as exc:
    raise SystemExit(
        "auto-pharmacist 루트에서 실행하거나 "
        "ros2_ws/src/gmp_hmi/tools에 파일을 놓으세요: " + str(exc)
    )


def port_is_free():
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", 5002))
            return True
        except OSError:
            return False


def wait_port_free(timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if port_is_free():
            return
        time.sleep(0.25)
    raise RuntimeError("5002 포트가 해제되지 않았습니다.")


def stop(process):
    if process is None or process.poll() is not None:
        return
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
            process.wait(timeout=10)
            return
        except ProcessLookupError:
            return
        except subprocess.TimeoutExpired:
            pass


def start_launch(env, duration):
    wait_port_free()
    command = [
        "ros2", "launch", "gmp_hmi", "hmi_comm_test.launch.py",
        "scenario:=normal",
        f"item_duration_s:={duration}",
        "test_initial_g:=[158.0,1000.0,1000.0]",
    ]
    process = subprocess.Popen(command, env=env, start_new_session=True)
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("시험 launch가 종료되었습니다. 위 로그를 확인하세요.")
        try:
            with opener.open("http://127.0.0.1:5002/auth/session", timeout=1) as response:
                json.load(response)
            return process
        except (OSError, ValueError):
            time.sleep(0.5)
    raise RuntimeError("60초 안에 HMI 시험 서버가 준비되지 않았습니다.")


def manual_runbatch_and_popup(password):
    check = RosHttpCheck()
    check.password = password
    check.wait("HTTP 기동", lambda: check.get("/auth/session"))
    check.login("admin", password)
    check.guard()
    check.graph()
    check.wait("시험 재고 수신", lambda: check.guard().get("inventory", {}).get("fresh"))
    check.wait("RunBatch 액션 서버 발견", lambda:
               check.guard().get("diagnostics", {}).get("actions", {}).get("run_batch"))
    print("PASS  1/8 ROS 노드·RunBatch 액션·서비스·토픽 타입", flush=True)

    catalog = {recipe["name"]: recipe for recipe in check.get("/recipes")}
    if set(catalog) != {"recipe-01", "recipe-02", "recipe-03"}:
        raise CheckFailed("레시피 3종 목록 불일치: " + str(sorted(catalog)))
    print("PASS  2/8 레시피 1·2·3 조회", flush=True)

    submission = check.post("/order", {"recipe": "recipe-01"})
    batch_id = submission.get("batch_id")
    if not batch_id:
        raise CheckFailed("RunBatch Goal 수락 응답에 batch_id가 없습니다.")
    check.wait("RUNNING", lambda: check.mode("RUNNING", batch_id))
    print("PASS  3/8 HMI 주문 → RunBatch Goal 수락 · " + batch_id, flush=True)

    feedback = check.wait("RunBatch Feedback", lambda:
        (lambda rb: rb if rb.get("status") == "RUNNING" and
         rb.get("batch_id") == batch_id and rb.get("step") else None)(
             check.guard().get("run_batch", {})))
    if feedback.get("item_index") is None or "last_result" not in feedback:
        raise CheckFailed("RunBatch Feedback의 CellState/last_result가 누락되었습니다.")
    print("PASS  4/8 RunBatch Feedback CellState·last_result 수신", flush=True)

    check.post("/interlock", {"request": 1, "reason": "SAFETY_POPUP_TEST"})
    paused = check.wait("PAUSED", lambda: check.mode("PAUSED", batch_id))
    if paused.get("station") != "test_safe":
        raise CheckFailed("안전 위치 test_safe 도착 전 PAUSED가 허가되었습니다.")
    print("PASS  5/8 Interlock ENTER → test_safe → PAUSED", flush=True)
    print("\n[화면 확인] 「안전 자세 일시 정지」 팝업이 떠야 합니다.", flush=True)
    input("팝업·배치·모바일 표시를 확인했으면 Enter: ")

    check.post("/interlock", {"request": 2, "reason": "SAFETY_POPUP_TEST"})
    check.wait("RUNNING 재개", lambda: check.mode("RUNNING", batch_id))
    print("PASS  6/8 Interlock EXIT → RunBatch 재개 · 팝업 해제", flush=True)

    check.wait("DONE", lambda: check.mode("DONE", batch_id), timeout=90)
    result = check.wait("RunBatch Result", lambda:
        (lambda rb: rb if rb.get("status") == "FINISHED" else None)(
            check.guard().get("run_batch", {})), timeout=20)
    if not result.get("success") or result.get("result") != "DONE" or result.get("items_done") != 3:
        raise CheckFailed("RunBatch Result 불일치: " + str(result))
    print("PASS  7/8 RunBatch Result success=true·items_done=3·DONE", flush=True)

    record = check.wait("SQLite 배치 기록", lambda: check.record(batch_id), timeout=20)
    if len(record.get("items", [])) != 3:
        raise CheckFailed("SQLite 원료별 결과 기록 누락")
    print("PASS  8/8 토픽 → record_node → SQLite 배치 기록", flush=True)


def main():
    if not port_is_free():
        print("5002 포트가 사용 중입니다. 기존 시험 서버를 Ctrl+C로 종료하세요.", file=sys.stderr)
        return 2

    password = os.environ.get("GMP_HMI_ADMIN_PASSWORD") or getpass.getpass(
        "시험 로그인 비밀번호(10자 이상): ")
    if len(password) < 10:
        print("비밀번호는 10자 이상이어야 합니다.", file=sys.stderr)
        return 2

    env = os.environ.copy()
    env.update(
        ROS_DOMAIN_ID="88",
        RMW_IMPLEMENTATION="rmw_fastrtps_cpp",
        GMP_HMI_ADMIN_USER="admin",
        GMP_HMI_ADMIN_PASSWORD=password,
    )

    first = second = None
    try:
        print("\n=== 1단계: RunBatch + 안전모드 팝업 실험 ===", flush=True)
        first = start_launch(env, 5.0)
        print("\n브라우저: http://127.0.0.1:5002", flush=True)
        print("로그인: admin / 방금 직접 입력한 비밀번호", flush=True)
        input("브라우저와 모바일에서 로그인했으면 Enter: ")
        manual_runbatch_and_popup(password)
        stop(first)
        first = None
        wait_port_free()

        print("\n=== 2단계: 전체 ROS·HTTP·SQLite 회귀 실험 ===", flush=True)
        second = start_launch(env, 2.0)
        verifier = TOOLS / "verify_ros_http.py"
        result = subprocess.run([sys.executable, str(verifier)], env=env, check=False)
        if result.returncode:
            raise RuntimeError("전체 회귀 실험이 실패했습니다.")
        print("\nALL PASS  RunBatch·안전 팝업·ROS 서비스/토픽·기록 검증 완료", flush=True)
        return 0
    except KeyboardInterrupt:
        print("\n시험을 사용자가 종료했습니다.", file=sys.stderr)
        return 130
    except (CheckFailed, RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        print("\nFAIL  " + str(exc), file=sys.stderr)
        return 1
    finally:
        stop(second)
        stop(first)


if __name__ == "__main__":
    raise SystemExit(main())
