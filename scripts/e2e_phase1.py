"""E2E Phase 1 (Test C, D, F): docker up + api + orchestrator + worker, GEMLOGIN_MODE=fake.

Chạy:  python scripts/e2e_phase1.py
Điều kiện: `docker compose up -d` (Postgres/Redis/RabbitMQ healthy), đã `pnpm build` + seed profile TikTok.

Kịch bản:
  C. POST /check một URL fixture TikTok → job PENDING→RUNNING→DONE; check_logs có 1 dòng với CẢ
     url_status LẪN profile_health; check_jobs.result đúng; cache được set (LIVE).
  D. POST lại cùng URL trong TTL → cache hit, <500ms, KHÔNG tạo job mới; INCONCLUSIVE không vào cache.
  F. Một request → cùng trace_id ở log api, orchestrator, worker, và trong check_logs.
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
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # console Windows mặc định cp1252 — ép UTF-8

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "apps" / "worker" / "tests" / "fixtures"
LOG_DIR = REPO / ".e2e-logs"
API = "http://127.0.0.1:3001"
ORCH = "http://127.0.0.1:3002"
STATUS_OVERRIDES = {"/dead_404.html": 404}

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        _failures.append(name)


# ── fixture static server (HTTP status thật: dead_404 → 404) ──────────────────
class _Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a: object, **k: object) -> None:
        super().__init__(*a, directory=str(FIXTURES), **k)  # type: ignore[arg-type]

    def send_response(self, code: int, message: str | None = None) -> None:
        override = STATUS_OVERRIDES.get(self.path.split("?")[0])
        if override is not None and code == 200:
            code = override
        super().send_response(code, message)

    def log_message(self, *a: object) -> None:
        return


def start_fixture_server() -> tuple[HTTPServer, str]:
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    host, port = httpd.server_address[0], httpd.server_address[1]
    return httpd, f"http://{host}:{port}"


# ── HTTP helpers ──────────────────────────────────────────────────────────────
def http_json(method: str, url: str, body: dict | None = None) -> tuple[int, dict, float]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode())
            return resp.status, payload, (time.monotonic() - start) * 1000
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}"), (time.monotonic() - start) * 1000


def psql(sql: str) -> str:
    out = subprocess.run(
        ["docker", "exec", "fastcheck-postgres", "psql", "-U", "fastcheck", "-d", "fastcheck",
         "-tA", "-c", sql],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def redis_get(key: str) -> str:
    out = subprocess.run(
        ["docker", "exec", "fastcheck-redis", "redis-cli", "GET", key],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def wait_until(fn, timeout: float, interval: float = 0.5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if fn():
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def poll_job(trace_id: str, want_status: str, timeout: float = 60.0) -> dict:
    result: dict = {}

    def done() -> bool:
        nonlocal result
        code, payload, _ = http_json("GET", f"{API}/check/{trace_id}")
        result = payload
        return code == 200 and payload.get("status") == want_status

    wait_until(done, timeout, 0.4)
    return result


# ── orchestration ──────────────────────────────────────────────────────────────
def spawn(name: str, args: list[str], env: dict[str, str]) -> subprocess.Popen:
    LOG_DIR.mkdir(exist_ok=True)
    logf = open(LOG_DIR / f"{name}.log", "w", encoding="utf-8")
    return subprocess.Popen(args, cwd=str(REPO), env=env, stdout=logf, stderr=subprocess.STDOUT)


def kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       capture_output=True, check=False)
    else:
        proc.terminate()


def main() -> int:
    httpd, fixture_base = start_fixture_server()
    print(f"fixture server: {fixture_base}")

    env = dict(os.environ)
    env["FIXTURE_BASE_URL"] = fixture_base
    env["GEMLOGIN_MODE"] = "fake"

    # Reset trạng thái để assert sạch: trả profile về AVAILABLE, xoá job/log + cache của lần chạy trước.
    psql("UPDATE profiles SET status='AVAILABLE', lease_expires_at=NULL, assigned_station_id=NULL, "
         "cooldown_until=NULL, consecutive_fails=0, health_score=100 "
         "WHERE account_label='seed-tiktok-e2e';")
    psql("DELETE FROM check_logs WHERE target_url LIKE '%tiktok.com/@fc%';")
    psql("DELETE FROM check_jobs WHERE target_url LIKE '%tiktok.com/@fc%';")
    _keys = subprocess.run(
        ["docker", "exec", "fastcheck-redis", "redis-cli", "KEYS", "fastcheck:result:*"],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    if _keys:
        subprocess.run(["docker", "exec", "fastcheck-redis", "redis-cli", "DEL", *_keys],
                       capture_output=True, text=True, check=False)

    procs: dict[str, subprocess.Popen] = {}
    try:
        procs["api"] = spawn("api", ["node", "apps/api/dist/main.js"], env)
        procs["orchestrator"] = spawn("orchestrator", ["node", "apps/orchestrator/dist/main.js"], env)
        procs["worker"] = spawn(
            "worker",
            ["uv", "--directory", "apps/worker", "run", "python", "-m", "fastcheck_worker"],
            env,
        )

        print("chờ services sẵn sàng…")
        api_ok = wait_until(lambda: http_json("GET", f"{API}/health")[0] == 200, 40)
        check("API /health sẵn sàng", api_ok)

        def station_online() -> bool:
            code, payload, _ = http_json("GET", f"{ORCH}/health")
            return code == 200 and any(s.get("status") == "ONLINE" for s in payload.get("stations", []))

        station_ok = wait_until(station_online, 40)
        check("Worker đăng ký station (ONLINE)", station_ok)
        if not (api_ok and station_ok):
            raise RuntimeError("services chưa sẵn sàng — xem .e2e-logs/")

        # ── Test C: end-to-end LIVE ─────────────────────────────────────────────
        print("\n[C] End-to-end (LIVE):")
        url = "https://www.tiktok.com/@fc/video/live"
        code, resp, _ = http_json("POST", f"{API}/check", {"url": url})
        trace_id = resp.get("trace_id", "")
        url_hash = resp.get("url_hash", "")
        check("POST /check → 202 + trace_id", code == 202 and bool(trace_id), f"trace_id={trace_id}")

        job = poll_job(trace_id, "DONE", 60)
        check("check_jobs PENDING→RUNNING→DONE", job.get("status") == "DONE", f"status={job.get('status')}")
        check("check_jobs.result = LIVE", job.get("result") == "LIVE", f"result={job.get('result')}")

        row = psql(
            "SELECT url_status||'|'||profile_health||'|'||coalesce(response_time_ms::text,'null') "
            f"FROM check_logs WHERE trace_id='{trace_id}';"
        )
        n_logs = psql(f"SELECT count(*) FROM check_logs WHERE trace_id='{trace_id}';")
        check("check_logs có đúng 1 dòng", n_logs == "1", f"count={n_logs}")
        check("check_logs có CẢ url_status LẪN profile_health", row.startswith("LIVE|OK|"), f"row={row}")

        cache_val = redis_get(f"fastcheck:result:{url_hash}")
        check("cache được set (LIVE)", '"status":"LIVE"' in cache_val or '"LIVE"' in cache_val,
              f"redis={cache_val or '(nil)'}")

        # ── Test D: cache hit + dedupe + INCONCLUSIVE không cache ────────────────
        print("\n[D] Cache hit + dedupe:")
        jobs_before = psql("SELECT count(*) FROM check_jobs WHERE url_hash="
                           f"'{url_hash}';")
        code2, resp2, ms2 = http_json("POST", f"{API}/check", {"url": url})
        check("POST lại → cache hit (cached=true)", code2 == 200 and resp2.get("cached") is True,
              f"code={code2} cached={resp2.get('cached')}")
        check("cache hit < 500ms", ms2 < 500, f"{ms2:.0f}ms")
        jobs_after = psql(f"SELECT count(*) FROM check_jobs WHERE url_hash='{url_hash}';")
        check("KHÔNG tạo job mới khi cache hit", jobs_before == jobs_after,
              f"before={jobs_before} after={jobs_after}")

        # INCONCLUSIVE không vào cache (INV-1). Từ Phase 3: login_wall → profile_health=CHALLENGED
        # → auto-switch (KHÔNG chốt DONE-INCONCLUSIVE như Phase 1). Cách xử lý auto-switch/DLQ được
        # kiểm kỹ ở scripts/e2e_phase3.py. Ở đây chỉ giữ bất biến: INCONCLUSIVE không bao giờ vào cache.
        url_lw = "https://www.tiktok.com/@fc/video/loginwall"
        _, resp_lw, _ = http_json("POST", f"{API}/check", {"url": url_lw})
        time.sleep(3.0)  # để worker check + orchestrator xử lý ít nhất một lần
        _, job_lw, _ = http_json("GET", f"{API}/check/{resp_lw.get('trace_id', '')}")
        cache_lw = redis_get(f"fastcheck:result:{resp_lw.get('url_hash','')}")
        check("login_wall KHÔNG bị chốt DONE (Phase 3 auto-switch, không 'làm tròn' DEAD)",
              job_lw.get("status") != "DONE", f"status={job_lw.get('status')}")
        check("INCONCLUSIVE/CHALLENGED KHÔNG vào cache (INV-1)", cache_lw == "",
              f"redis={cache_lw or '(nil)'}")

        # ── Test F: trace_id xuyên suốt ─────────────────────────────────────────
        print("\n[F] trace_id xuyên suốt:")
        time.sleep(1.0)  # cho log flush
        logs = {n: (LOG_DIR / f"{n}.log").read_text(encoding="utf-8", errors="replace")
                for n in ("api", "orchestrator", "worker")}
        for svc, text in logs.items():
            check(f"trace_id có trong log {svc}", trace_id in text)
        in_logs_db = psql(f"SELECT count(*) FROM check_logs WHERE trace_id='{trace_id}';")
        check("trace_id có trong check_logs", in_logs_db == "1")

    finally:
        for name, proc in procs.items():
            kill_tree(proc)
        httpd.shutdown()
        time.sleep(1.0)

    print("\n" + ("=" * 60))
    if _failures:
        print(f"KẾT QUẢ: {len(_failures)} FAIL → {_failures}")
        return 1
    print("KẾT QUẢ: TẤT CẢ PASS (C, D, F)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
