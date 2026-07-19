"""E2E Phase 4 — Test 2 (station chết + orchestrator restart) & Test 3 (reconnect), INV-15.

Chạy:  python scripts/e2e_phase4.py
Điều kiện: docker infra healthy, `pnpm build`.

  2a. Station chết: job đang RUNNING → KILL worker → orchestrator OFFLINE + thu hồi (check_jobs về PENDING,
      cột dispatch clear, profile về AVAILABLE, log INV-15). Khởi động lại worker → job hoàn tất.
  2b. Orchestrator restart: seed 1 job RUNNING (đủ cột dispatch) + profile IN_USE, KHÔNG worker → khởi
      động orchestrator → startup sweep thu hồi nhờ cột dispatch (job PENDING, clear, profile AVAILABLE).
  3.  Reconnect: job đang RUNNING → BOUNCE orchestrator → worker TỰ reconnect + đăng ký lại (ONLINE) →
      job đang dở được thu hồi + chạy lại → hoàn tất (xử lý đúng).

Test 1 (idempotency lệnh) và Test 4 (process hygiene) là pytest tất định:
  uv --directory apps/worker run pytest tests/test_command_idempotency.py tests/test_process_hygiene.py
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
STATION_ID = "00000000-0000-4000-8000-000000000001"
SLOW_DELAY = 6.0

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        _failures.append(name)


# ── fixture server: /slow.html trả LIVE sau SLOW_DELAY giây ────────────────────
_LIVE = (FIXTURES / "live.html").read_bytes()


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/slow.html":
            time.sleep(SLOW_DELAY)
        f = FIXTURES / path.lstrip("/")
        body = _LIVE if path == "/slow.html" else (f.read_bytes() if f.is_file() else b"not found")
        code = 200 if (path == "/slow.html" or f.is_file()) else 404
        try:
            self.send_response(code)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # worker bị kill giữa chừng — client đóng socket, bỏ qua

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
        return exc.code, {}
    except Exception:
        return 0, {}


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


def job_field(tid: str, field: str) -> str:
    return psql(f"SELECT COALESCE({field}::text,'') FROM check_jobs WHERE trace_id='{tid}';")


def spawn(name: str, args: list[str], env: dict[str, str]) -> subprocess.Popen:
    LOG_DIR.mkdir(exist_ok=True)
    logf = open(LOG_DIR / f"{name}-p4.log", "w", encoding="utf-8")
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


def read_log(name: str) -> str:
    p = LOG_DIR / f"{name}-p4.log"
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""


def flush_cache() -> None:
    keys = subprocess.run(
        ["docker", "exec", "fastcheck-redis", "redis-cli", "KEYS", "fastcheck:result:*"],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    if keys:
        subprocess.run(["docker", "exec", "fastcheck-redis", "redis-cli", "DEL", *keys],
                       capture_output=True, text=True, check=False)


def purge_queues() -> None:
    # Xoá message rác từ lần chạy trước (job đã bị DELETE) — tránh dispatch message mồ côi.
    for q in ("job.pending", "job.retry", "job.dlq"):
        subprocess.run(["docker", "exec", "fastcheck-rabbitmq", "rabbitmqctl", "purge_queue", q],
                       capture_output=True, text=True, check=False)


def reset() -> None:
    psql("DELETE FROM check_logs WHERE target_url LIKE '%@fc/video/%p4%';")
    psql("DELETE FROM check_jobs WHERE target_url LIKE '%@fc/video/%p4%';")
    psql("DELETE FROM profiles WHERE account_label LIKE 'seed-p4-%';")
    for i in range(3):
        psql("INSERT INTO profiles (platform, account_label, status, health_score) "
             f"VALUES ('TIKTOK','seed-p4-{i}','AVAILABLE',100);")
    flush_cache()
    purge_queues()
    # Phase 5: xoá trạng thái circuit breaker để không lẫn giữa các lần chạy.
    cb_keys = subprocess.run(
        ["docker", "exec", "fastcheck-redis", "redis-cli", "KEYS", "cb:*"],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    if cb_keys:
        subprocess.run(["docker", "exec", "fastcheck-redis", "redis-cli", "DEL", *cb_keys],
                       capture_output=True, text=True, check=False)


def station_online() -> bool:
    return any(s.get("status") == "ONLINE"
              for s in http_json("GET", f"{ORCH}/health")[1].get("stations", []))


def main() -> int:  # noqa: C901, PLR0915
    httpd, fixture_base = start_fixture_server()
    print(f"fixture server: {fixture_base}")

    env = dict(os.environ)
    env.update(
        FIXTURE_BASE_URL=fixture_base,
        GEMLOGIN_MODE="fake",
        STATION_ID=STATION_ID,
        WORKER_MAX_CONCURRENCY="3",
        ORCHESTRATOR_PREFETCH="3",
        # QUAN TRỌNG: interval < timeout, nếu không station khoẻ bị đánh OFFLINE nhầm giữa hai nhịp ping.
        HEARTBEAT_INTERVAL_MS="2000",
        HEARTBEAT_TIMEOUT_MS="6000",
        STATION_MONITOR_INTERVAL_MS="1000",
        JOB_TIMEOUT_SECONDS="30",
        PROFILE_SYNC_INTERVAL_SECONDS="3600",
    )

    procs: dict[str, subprocess.Popen | None] = {"api": None, "orchestrator": None, "worker": None}

    def start(name: str, args: list[str]) -> None:
        procs[name] = spawn(name, args, env)

    def start_worker() -> None:
        start("worker", ["uv", "--directory", "apps/worker", "run", "python", "-m", "fastcheck_worker"])

    def start_orch() -> None:
        start("orchestrator", ["node", "apps/orchestrator/dist/main.js"])

    try:
        reset()
        # ── Part 1: api + orch + worker ────────────────────────────────────────
        start("api", ["node", "apps/api/dist/main.js"])
        start_orch()
        start_worker()
        if not wait_until(lambda: http_json("GET", f"{API}/health")[0] == 200, 40) or not wait_until(
            station_online, 40
        ):
            raise RuntimeError("services chưa sẵn sàng — xem .e2e-logs/*-p4.log")

        # ── Test 2a: station chết → thu hồi ─────────────────────────────────────
        print("\n[2a] Station chết → thu hồi job (INV-15):")
        _, r = http_json("POST", f"{API}/check", {"url": "https://www.tiktok.com/@fc/video/slow-p4a"})
        tid = r.get("trace_id", "")
        running = wait_until(lambda: job_field(tid, "status") == "RUNNING", 15)
        check("job vào RUNNING (đang chạy trên station)", running, f"status={job_field(tid,'status')}")
        prof = job_field(tid, "assigned_profile_id")
        check("có assigned_profile_id + assigned_station_id", bool(prof) and bool(job_field(tid, "assigned_station_id")))

        kill_tree(procs["worker"])  # station chết (kill worker → mất heartbeat + đóng WS)
        procs["worker"] = None

        recovered = wait_until(
            lambda: job_field(tid, "status") == "PENDING"
            and job_field(tid, "assigned_station_id") == ""
            and job_field(tid, "dispatched_at") == "",
            20,
        )
        check("thu hồi: job về PENDING + clear cột dispatch (INV-15)", recovered,
              f"status={job_field(tid,'status')} station={job_field(tid,'assigned_station_id')!r}")
        prof_status = psql(f"SELECT status FROM profiles WHERE id='{prof}';") if prof else ""
        check("profile trả về pool (AVAILABLE)", prof_status == "AVAILABLE", f"profile={prof_status}")
        check("có log thu hồi INV-15", "INV-15" in read_log("orchestrator") or "thu hồi" in read_log("orchestrator"))

        # Khởi động lại worker → job đang dở hoàn tất.
        start_worker()
        wait_until(station_online, 40)
        done = wait_until(lambda: job_field(tid, "status") == "DONE", 40)
        check("worker trở lại → job hoàn tất (DONE)", done, f"status={job_field(tid,'status')}")

        # ── Test 3: reconnect (bounce orchestrator) ─────────────────────────────
        print("\n[3] Reconnect: bounce orchestrator → worker tự reconnect + đăng ký lại:")
        _, r3 = http_json("POST", f"{API}/check", {"url": "https://www.tiktok.com/@fc/video/slow-p4r"})
        tid3 = r3.get("trace_id", "")
        wait_until(lambda: job_field(tid3, "status") == "RUNNING", 15)
        # Bounce orchestrator giữa lúc job đang chạy.
        kill_tree(procs["orchestrator"])
        procs["orchestrator"] = None
        time.sleep(1.0)
        start_orch()
        reconnected = wait_until(station_online, 45)  # worker TỰ reconnect + register lại
        check("worker tự reconnect + đăng ký lại (ONLINE)", reconnected)
        done3 = wait_until(lambda: job_field(tid3, "status") == "DONE", 60)
        check("job đang dở được xử lý đúng sau reconnect (DONE)", done3, f"status={job_field(tid3,'status')}")

        # Dừng Part 1.
        for name in ("worker", "orchestrator", "api"):
            kill_tree(procs[name])
            procs[name] = None
        time.sleep(1.5)

        # ── Test 2b: orchestrator restart thu hồi nhờ cột dispatch (KHÔNG worker) ─
        print("\n[2b] Orchestrator restart → startup sweep thu hồi (cột dispatch):")
        reset()
        # Seed 1 job RUNNING + profile IN_USE gán station (mô phỏng trạng thái giữa chừng trước khi restart).
        pid_seed = psql("INSERT INTO profiles (platform, account_label, status, assigned_station_id, "
                        "lease_expires_at) VALUES ('TIKTOK','seed-p4-orphan','IN_USE',"
                        f"'{STATION_ID}', now()+interval '5 min') RETURNING id;").splitlines()[0].strip()
        # Station phải tồn tại cho FK (đã đăng ký ở Part 1; đảm bảo có + OFFLINE).
        psql("INSERT INTO stations (id, name, status, max_concurrency) VALUES "
             f"('{STATION_ID}','p4-station','OFFLINE',3) ON CONFLICT (id) DO UPDATE SET status='OFFLINE';")
        tid2b = "00000000-0000-4000-8000-0000000b2b00"
        psql("INSERT INTO check_jobs (trace_id, target_url, url_hash, platform, status, retry_count, "
             "assigned_station_id, assigned_profile_id, dispatched_at) VALUES "
             f"('{tid2b}','https://www.tiktok.com/@fc/video/slow-p4b','hash-p4b-{int(time.time())}',"
             f"'TIKTOK','RUNNING',0,'{STATION_ID}','{pid_seed}', now());")

        start("api", ["node", "apps/api/dist/main.js"])
        start_orch()  # KHÔNG start worker → job thu hồi sẽ nằm PENDING (quan sát được)
        wait_until(lambda: http_json("GET", f"{ORCH}/health")[0] == 200, 40)
        swept = wait_until(
            lambda: job_field(tid2b, "status") == "PENDING"
            and job_field(tid2b, "assigned_station_id") == "",
            25,
        )
        check("startup sweep: job RUNNING mồ côi → PENDING + clear (INV-15)", swept,
              f"status={job_field(tid2b,'status')} station={job_field(tid2b,'assigned_station_id')!r}")
        prof2b = psql(f"SELECT status FROM profiles WHERE id='{pid_seed}';")
        check("profile mồ côi trả về AVAILABLE", prof2b == "AVAILABLE", f"profile={prof2b}")
        check("có log startup sweep", "startup sweep" in read_log("orchestrator"))

    finally:
        for proc in procs.values():
            kill_tree(proc)
        httpd.shutdown()
        psql("DELETE FROM profiles WHERE account_label LIKE 'seed-p4-%';")
        psql("UPDATE profiles SET status='AVAILABLE', cooldown_until=NULL, lease_expires_at=NULL, "
             "assigned_station_id=NULL WHERE platform='TIKTOK';")
        time.sleep(1.0)

    print("\n" + "=" * 60)
    if _failures:
        print(f"KẾT QUẢ: {len(_failures)} FAIL → {_failures}")
        return 1
    print("KẾT QUẢ: TẤT CẢ PASS (2a, 2b, 3)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
