"""E2E Phase 3 (Test 3 auto-switch + Test 4 pool cạn): docker up + api + orchestrator + worker.

Chạy:  python scripts/e2e_phase3.py
Điều kiện: docker infra healthy, `pnpm build`.

  3a. Recovery: URL 'flaky' (captcha lần đầu → BLOCKED → auto-switch → LIVE). Job cuối DONE=LIVE,
      retry_count>=1, check_logs có dòng BLOCKED rồi dòng LIVE (đổi profile).
  3b. DLQ: URL 'captcha' luôn BLOCKED → vượt max_retries → check_jobs DEAD_LETTER + ALERT log;
      KHÔNG lặp switch vô hạn (retry_count dừng ở max).
  4.  Pool cạn: ép mọi profile TikTok COOLDOWN → job KHÔNG dispatch được, có log 'pool cạn' + requeue
      (không tight-loop); phục hồi 1 profile → job hoàn tất.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "apps" / "worker" / "tests" / "fixtures"
LOG_DIR = REPO / ".e2e-logs"
API = "http://127.0.0.1:3001"
ORCH = "http://127.0.0.1:3002"

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        _failures.append(name)


# ── fixture server: dead_404 → 404; flaky.html → captcha `threshold` lần đầu rồi live ──────
class _State:
    def __init__(self) -> None:
        self.flaky_hits = 0
        self.flaky_threshold = 1  # số lần đầu trả captcha (BLOCKED)


_state = _State()
_CAPTCHA = (FIXTURES / "captcha.html").read_bytes()
_LIVE = (FIXTURES / "live.html").read_bytes()


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/flaky.html":
            _state.flaky_hits += 1
            body = _CAPTCHA if _state.flaky_hits <= _state.flaky_threshold else _LIVE
            self._send(200, body)
            return
        if path == "/dead_404.html":
            self._send(404, (FIXTURES / "dead_404.html").read_bytes())
            return
        f = FIXTURES / path.lstrip("/")
        if f.is_file():
            self._send(200, f.read_bytes())
        else:
            self._send(404, b"not found")

    def _send(self, code: int, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a: object) -> None:
        return


def start_fixture_server() -> tuple[ThreadingHTTPServer, str]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    host, port = httpd.server_address[0], httpd.server_address[1]
    return httpd, f"http://{host}:{port}"


# ── helpers ─────────────────────────────────────────────────────────────────
def http_json(method: str, url: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")


def psql(sql: str) -> str:
    out = subprocess.run(
        ["docker", "exec", "fastcheck-postgres", "psql", "-U", "fastcheck", "-d", "fastcheck",
         "-tA", "-c", sql],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def wait_until(fn, timeout: float, interval: float = 0.4) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if fn():
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def poll_status(trace_id: str, want: str, timeout: float = 40.0) -> dict:
    result: dict = {}

    def done() -> bool:
        nonlocal result
        code, payload = http_json("GET", f"{API}/check/{trace_id}")
        result = payload
        return code == 200 and payload.get("status") == want

    wait_until(done, timeout)
    return result


def spawn(name: str, args: list[str], env: dict[str, str]) -> subprocess.Popen:
    LOG_DIR.mkdir(exist_ok=True)
    logf = open(LOG_DIR / f"{name}-p3.log", "w", encoding="utf-8")
    return subprocess.Popen(args, cwd=str(REPO), env=env, stdout=logf, stderr=subprocess.STDOUT)


def kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, check=False)
    else:
        proc.terminate()


def flush_result_cache() -> None:
    """Xoá cache kết quả để mỗi lần chạy đều là cache MISS (chạy lại không dính LIVE đã cache lần trước)."""
    keys = subprocess.run(
        ["docker", "exec", "fastcheck-redis", "redis-cli", "KEYS", "fastcheck:result:*"],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    if keys:
        subprocess.run(
            ["docker", "exec", "fastcheck-redis", "redis-cli", "DEL", *keys],
            capture_output=True, text=True, check=False,
        )


def clear_circuit() -> None:
    """Xoá trạng thái circuit breaker (Phase 5) giữa các kịch bản — chuỗi BLOCKED của test trước không
    được làm mở circuit khiến POST của test sau bị 503."""
    keys = subprocess.run(
        ["docker", "exec", "fastcheck-redis", "redis-cli", "KEYS", "cb:*"],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    if keys:
        subprocess.run(["docker", "exec", "fastcheck-redis", "redis-cli", "DEL", *keys],
                       capture_output=True, text=True, check=False)


def reset_tiktok_pool() -> None:
    psql("DELETE FROM check_logs WHERE target_url LIKE '%tiktok.com/@fc%';")
    psql("DELETE FROM check_jobs WHERE target_url LIKE '%tiktok.com/@fc%';")
    psql("UPDATE profiles SET status='AVAILABLE', cooldown_until=NULL, lease_expires_at=NULL, "
         "assigned_station_id=NULL, consecutive_fails=0, health_score=100 WHERE platform='TIKTOK';")
    flush_result_cache()
    clear_circuit()


def seed_pool(n: int) -> None:
    psql("DELETE FROM profiles WHERE account_label LIKE 'seed-p3-%';")
    for i in range(n):
        psql("INSERT INTO profiles (platform, account_label, status, health_score) "
             f"VALUES ('TIKTOK','seed-p3-{i}','AVAILABLE',100);")


def read_log(name: str) -> str:
    p = LOG_DIR / f"{name}-p3.log"
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""


def main() -> int:
    httpd, fixture_base = start_fixture_server()
    print(f"fixture server: {fixture_base}")

    env = dict(os.environ)
    env.update(
        FIXTURE_BASE_URL=fixture_base,
        GEMLOGIN_MODE="fake",
        ORCHESTRATOR_MAX_RETRIES="2",
        RETRY_BACKOFF_BASE_MS="500",
        PROFILE_COOLDOWN_SECONDS="2",
        PROFILE_DEAD_THRESHOLD="10",
        PROFILE_HEALTH_PENALTY="10",
        PROFILE_POOL_LOW_WATERMARK="1",
        LEASE_REAP_INTERVAL_MS="3000",
    )

    seed_pool(3)
    reset_tiktok_pool()

    procs: dict[str, subprocess.Popen] = {}
    try:
        procs["api"] = spawn("api", ["node", "apps/api/dist/main.js"], env)
        procs["orchestrator"] = spawn("orchestrator", ["node", "apps/orchestrator/dist/main.js"], env)
        procs["worker"] = spawn(
            "worker", ["uv", "--directory", "apps/worker", "run", "python", "-m", "fastcheck_worker"], env
        )

        print("chờ services…")
        ok = wait_until(lambda: http_json("GET", f"{API}/health")[0] == 200, 40)
        station = wait_until(
            lambda: any(s.get("status") == "ONLINE"
                        for s in http_json("GET", f"{ORCH}/health")[1].get("stations", [])),
            40,
        )
        check("services + station sẵn sàng", ok and station)
        if not (ok and station):
            raise RuntimeError("services chưa sẵn sàng — xem .e2e-logs/*-p3.log")

        # ── Test 3a: auto-switch phục hồi ───────────────────────────────────────
        print("\n[3a] Auto-switch phục hồi (flaky: BLOCKED → LIVE):")
        reset_tiktok_pool()
        _state.flaky_hits = 0
        _state.flaky_threshold = 1
        _, r = http_json("POST", f"{API}/check", {"url": "https://www.tiktok.com/@fc/video/flaky"})
        tid = r.get("trace_id", "")
        job = poll_status(tid, "DONE", 40)
        check("job phục hồi → DONE", job.get("status") == "DONE", f"status={job.get('status')}")
        check("kết quả cuối = LIVE (sau khi đổi profile)", job.get("result") == "LIVE",
              f"result={job.get('result')}")
        check("retry_count >= 1 (đã auto-switch)", int(job.get("retry_count", 0)) >= 1,
              f"retry_count={job.get('retry_count')}")
        healths = psql(f"SELECT string_agg(profile_health::text,',' ORDER BY checked_at) FROM check_logs WHERE trace_id='{tid}';")
        check("check_logs: có BLOCKED rồi tới OK (đổi profile)",
              "BLOCKED" in healths and healths.endswith("OK"), f"healths={healths}")

        # ── Test 3b: DLQ khi vượt max_retries ──────────────────────────────────
        print("\n[3b] Vượt max_retries → DLQ (captcha luôn BLOCKED):")
        reset_tiktok_pool()
        _, r2 = http_json("POST", f"{API}/check", {"url": "https://www.tiktok.com/@fc/video/captcha"})
        tid2 = r2.get("trace_id", "")
        job2 = poll_status(tid2, "DEAD_LETTER", 40)
        check("job vào DEAD_LETTER", job2.get("status") == "DEAD_LETTER", f"status={job2.get('status')}")
        check("retry_count dừng ở max (2) — KHÔNG switch vô hạn",
              int(job2.get("retry_count", 0)) == 2, f"retry_count={job2.get('retry_count')}")
        time.sleep(0.5)
        orch_log = read_log("orchestrator")
        check("có ALERT log DLQ", "ALERT" in orch_log and "DLQ" in orch_log)
        dlq_depth = psql("SELECT count(*) FROM check_logs WHERE trace_id='%s';" % tid2)
        check("check_logs ghi mọi lần thử (>=3 dòng BLOCKED)", int(dlq_depth) >= 3, f"rows={dlq_depth}")

        # ── Test 4: pool cạn — không switch vô hạn ──────────────────────────────
        print("\n[4] Pool cạn (mọi profile TikTok COOLDOWN):")
        reset_tiktok_pool()
        psql("UPDATE profiles SET status='COOLDOWN', cooldown_until=now()+interval '1 day' WHERE platform='TIKTOK';")
        _, r3 = http_json("POST", f"{API}/check", {"url": "https://www.tiktok.com/@fc/video/live"})
        tid3 = r3.get("trace_id", "")
        time.sleep(4.0)  # để consumer thử claim vài lần (requeue có trễ, không tight-loop)
        _, j3 = http_json("GET", f"{API}/check/{tid3}")
        check("pool cạn → job KHÔNG hoàn tất (vẫn chờ, không sai)", j3.get("status") in ("PENDING", "RUNNING"),
              f"status={j3.get('status')}")
        orch_log = read_log("orchestrator")
        check("có log 'pool cạn profile' (nhánh xử lý)", "pool cạn profile" in orch_log)
        # Phục hồi: mở lại 1 profile → job hoàn tất (không kẹt vĩnh viễn).
        psql("UPDATE profiles SET status='AVAILABLE', cooldown_until=NULL WHERE platform='TIKTOK' "
             "AND account_label='seed-p3-0';")
        job3 = poll_status(tid3, "DONE", 30)
        check("phục hồi profile → job hoàn tất", job3.get("status") == "DONE", f"status={job3.get('status')}")

    finally:
        for proc in procs.values():
            kill_tree(proc)
        httpd.shutdown()
        psql("DELETE FROM profiles WHERE account_label LIKE 'seed-p3-%';")
        psql("UPDATE profiles SET status='AVAILABLE', cooldown_until=NULL, lease_expires_at=NULL, "
             "consecutive_fails=0 WHERE platform='TIKTOK';")
        time.sleep(1.0)

    print("\n" + "=" * 60)
    if _failures:
        print(f"KẾT QUẢ: {len(_failures)} FAIL → {_failures}")
        return 1
    print("KẾT QUẢ: TẤT CẢ PASS (3a, 3b, 4)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
