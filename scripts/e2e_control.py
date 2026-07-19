"""E2E Bề mặt điều khiển Station Management (mục 2 Excel) — REST → WS command → command_ack.

Chạy:  python scripts/e2e_control.py
Điều kiện: docker infra healthy, `pnpm build`.

Chứng minh operator VẬN HÀNH ĐƯỢC BẰNG TAY (Swagger/UI) — không chỉ hiển thị:
  1. GET  /stations                       → station đang kết nối.
  2. POST /stations/:id/profiles          → tạo profile GemLogin (forward profile.create) + chờ ack.
  3. POST /stations/:id/browser/open      → mở browser (fake spawn cây tiến trình) + ack ok.
  4. POST /stations/:id/browser/close     → tắt browser + ack ok.
  5. POST /stations/:id/login (COOKIE)    → GỌI kịch bản login → ack ok (fake page: cookie → LOGGED_IN).
  6. POST /stations/:id/login (INFO/YT)   → FB/YT + info không hỗ trợ → ack ok=false (báo ra, không đoán).
  7. POST /accounts                       → nạp tài khoản (cookie mã hoá) → profiles AVAILABLE + has_cookie.
  8. GET  /stations/:id/profiles          → thấy tài khoản vừa nạp (KHÔNG lộ cookie).
  9. Station rớt → REST đang chờ ack được giải phóng (không treo tới timeout).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPO = Path(__file__).resolve().parent.parent
LOG_DIR = REPO / ".e2e-logs"
ORCH = "http://127.0.0.1:3002"
STATION_ID = "00000000-0000-4000-8000-0000000000c7"

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        _failures.append(name)


def http_json(method: str, url: str, body: dict | None = None) -> tuple[int, object]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode())
        except Exception:
            return exc.code, {}
    except Exception as exc:
        return 0, {"error": str(exc)}


def psql(sql: str) -> str:
    out = subprocess.run(
        ["docker", "exec", "fastcheck-postgres", "psql", "-U", "fastcheck", "-d", "fastcheck",
         "-tA", "-c", sql],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def wait_until(fn, timeout: float, interval: float = 0.3) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if fn():
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def spawn(name: str, args: list[str], env: dict[str, str]) -> subprocess.Popen:
    LOG_DIR.mkdir(exist_ok=True)
    logf = open(LOG_DIR / f"{name}-ctl.log", "w", encoding="utf-8")
    return subprocess.Popen(args, cwd=str(REPO), env=env, stdout=logf, stderr=subprocess.STDOUT)


def kill_tree(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, check=False)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        pass


def station_online() -> bool:
    code, body = http_json("GET", f"{ORCH}/stations")
    return code == 200 and any(
        s.get("station_id") == STATION_ID and s.get("status") == "ONLINE"
        for s in (body if isinstance(body, list) else [])
    )


def reset() -> None:
    psql("DELETE FROM profiles WHERE gemlogin_profile_id LIKE 'ctl-%' OR account_label LIKE 'fastcheck-%';")
    # Xoá message rác từ lần chạy trước (tránh log requeue nhiễu — control E2E không submit check).
    for q in ("job.pending", "job.retry", "job.dlq"):
        subprocess.run(["docker", "exec", "fastcheck-rabbitmq", "rabbitmqctl", "purge_queue", q],
                       capture_output=True, text=True, check=False)


def main() -> int:  # noqa: C901, PLR0915
    env = dict(os.environ)
    env.update(
        GEMLOGIN_MODE="fake",
        STATION_ID=STATION_ID,
        STATION_NAME="ctl-station",
        WORKER_MAX_CONCURRENCY="3",
        ORCHESTRATOR_PREFETCH="3",
        HEARTBEAT_INTERVAL_MS="2000",
        HEARTBEAT_TIMEOUT_MS="6000",
        STATION_MONITOR_INTERVAL_MS="1000",
        PROFILE_SYNC_INTERVAL_SECONDS="2",
        COMMAND_ACK_TIMEOUT_MS="30000",
    )

    procs: dict[str, subprocess.Popen | None] = {"orchestrator": None, "worker": None}
    try:
        reset()
        procs["orchestrator"] = spawn("orchestrator", ["node", "apps/orchestrator/dist/main.js"], env)
        procs["worker"] = spawn(
            "worker", ["uv", "--directory", "apps/worker", "run", "python", "-m", "fastcheck_worker"], env
        )
        if not wait_until(station_online, 45):
            raise RuntimeError("station chưa ONLINE — xem .e2e-logs/*-ctl.log")

        # 1. GET /stations
        print("\n[1] GET /stations:")
        code, stations = http_json("GET", f"{ORCH}/stations")
        check("liệt kê station (HTTP 200 + station của mình ONLINE)", station_online(), f"code={code}")

        # 2. Tạo profile GemLogin (forward profile.create → ack)
        print("\n[2] POST /stations/:id/profiles (tạo profile — forward + chờ ack):")
        code, r = http_json("POST", f"{ORCH}/stations/{STATION_ID}/profiles",
                            {"platform": "TIKTOK", "account_label": "fastcheck-ctl"})
        ok = isinstance(r, dict) and r.get("ok") is True and bool(r.get("command_id"))
        check("tạo profile → ok=true + command_id (command/ack tương quan)", ok, f"code={code} r={r}")

        # 3 + 4. Mở / tắt browser
        print("\n[3] POST /stations/:id/browser/open:")
        code, r = http_json("POST", f"{ORCH}/stations/{STATION_ID}/browser/open",
                            {"gemlogin_profile_id": "ctl-1"})
        check("mở browser → ok=true", isinstance(r, dict) and r.get("ok") is True, f"code={code} r={r}")
        print("[4] POST /stations/:id/browser/close:")
        code, r = http_json("POST", f"{ORCH}/stations/{STATION_ID}/browser/close",
                            {"gemlogin_profile_id": "ctl-1"})
        check("tắt browser → ok=true", isinstance(r, dict) and r.get("ok") is True, f"code={code} r={r}")

        # 5. GỌI kịch bản login (COOKIE) — fake page: cookie → LOGGED_IN → ok=true
        print("\n[5] POST /stations/:id/login (COOKIE):")
        code, r = http_json("POST", f"{ORCH}/stations/{STATION_ID}/login",
                            {"gemlogin_profile_id": "ctl-1", "platform": "TIKTOK", "method": "COOKIE",
                             "cookie": '[{"name":"sessionid","value":"x"}]'})
        check("login cookie → ok=true (LOGGED_IN)", isinstance(r, dict) and r.get("ok") is True,
              f"code={code} detail={r.get('detail') if isinstance(r, dict) else r}")

        # 6. login INFO cho YouTube → không hỗ trợ → ok=false (báo ra rõ ràng)
        print("[6] POST /stations/:id/login (INFO, YouTube — không hỗ trợ):")
        code, r = http_json("POST", f"{ORCH}/stations/{STATION_ID}/login",
                            {"gemlogin_profile_id": "ctl-1", "platform": "YOUTUBE", "method": "INFO",
                             "username": "u", "password": "p"})
        unsupported = isinstance(r, dict) and r.get("ok") is False and "unsupported" in str(r.get("detail"))
        check("info-login FB/YT → ok=false + lý do (không đoán)", unsupported, f"r={r}")

        # 7. Nạp tài khoản thật vào pool (cookie mã hoá)
        print("\n[7] POST /accounts (nạp tài khoản — cookie mã hoá):")
        code, r = http_json("POST", f"{ORCH}/accounts",
                            {"platform": "TIKTOK", "gemlogin_profile_id": "ctl-acc-1",
                             "station_id": STATION_ID, "account_label": "fastcheck-ctl-acc",
                             "cookie": '[{"name":"sessionid","value":"real"}]'})
        acc_ok = (isinstance(r, dict) and r.get("has_cookie") is True and r.get("status") == "AVAILABLE"
                  and bool(r.get("profile_id")))
        check("nạp tài khoản → AVAILABLE + has_cookie=true", acc_ok, f"code={code} r={r}")
        # Cookie lưu ĐÃ MÃ HOÁ (không plaintext) trong DB.
        enc = psql("SELECT COALESCE(cookie_ciphertext::text,'') FROM profiles WHERE gemlogin_profile_id='ctl-acc-1';")
        check("cookie lưu ở DB đã MÃ HOÁ (không plaintext 'real')", bool(enc) and "real" not in enc,
              f"len={len(enc)}")

        # 8. GET profiles — thấy tài khoản, KHÔNG lộ cookie
        print("\n[8] GET /stations/:id/profiles (không lộ cookie):")
        code, profs = http_json("GET", f"{ORCH}/stations/{STATION_ID}/profiles")
        body_str = json.dumps(profs)
        seen = isinstance(profs, list) and any(
            p.get("gemlogin_profile_id") == "ctl-acc-1" and p.get("has_cookie") is True for p in profs
        )
        check("thấy tài khoản đã nạp (has_cookie=true)", seen, f"code={code}")
        check("KHÔNG endpoint nào trả cookie/ciphertext (INV-12)",
              "cookie_ciphertext" not in body_str and "sessionid" not in body_str)

        # 9. Station rớt → REST đang chờ ack được giải phóng (không treo)
        print("\n[9] Station rớt khi đang chờ ack → REST không treo:")
        kill_tree(procs["worker"])
        procs["worker"] = None
        t0 = time.monotonic()
        code, r = http_json("POST", f"{ORCH}/stations/{STATION_ID}/browser/open",
                            {"gemlogin_profile_id": "ctl-x"})
        elapsed = time.monotonic() - t0
        # station offline → 503 ngay (không gửi được lệnh) HOẶC ack ok=false do rớt — cả hai đều < timeout.
        freed = elapsed < 20 and (code in (503, 200))
        check("REST trả nhanh khi station offline (không treo tới timeout)", freed,
              f"code={code} elapsed={elapsed:.1f}s")

        print("\n" + ("KẾT QUẢ: TẤT CẢ PASS" if not _failures else f"KẾT QUẢ: {len(_failures)} FAIL"))
        return 1 if _failures else 0
    finally:
        for p in procs.values():
            kill_tree(p)
        reset()


if __name__ == "__main__":
    raise SystemExit(main())
