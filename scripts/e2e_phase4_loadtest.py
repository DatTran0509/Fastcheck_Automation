"""E2E Phase 4 — Test 5: load test 50 concurrent (GEMLOGIN_MODE=fake), đo pipeline.

Chạy:  python scripts/e2e_phase4_loadtest.py
Điều kiện: docker infra healthy, `pnpm build`.

Đo & khẳng định (spec §4.3, §10.4, INV-10):
  - KHÔNG crash: services còn trả /health 200 sau tải.
  - Số browser đồng thời ≤ tổng max_concurrency (fixture server đếm request in-flight cực đại).
  - Job vượt công suất XẾP HÀNG trong queue: độ sâu job.pending tăng rồi RÚT về ~0 (backpressure).
  - Ghi p95 latency (POST + hoàn tất) + RAM đỉnh worker.
  - So sánh prefetch > pool (churn "không có station còn slot") vs prefetch = pool (sạch).

k6 (scripts/loadtest.js) là artifact chuẩn; k6 chưa cài ở đây nên driver này dùng ThreadPool (50 concurrent
tương đương) để đo THỰC TẾ.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "apps" / "worker" / "tests" / "fixtures"
LOG_DIR = REPO / ".e2e-logs"
API = "http://127.0.0.1:3001"
ORCH = "http://127.0.0.1:3002"

# Số request đồng thời — đặt LOADTEST_N để chạy cao hơn (50 tối thiểu; 100 = kịch bản 70-100 người dùng).
N_REQUESTS = int(os.environ.get("LOADTEST_N", "50"))
POOL = 8  # WORKER_MAX_CONCURRENCY + station max_concurrency
FIXTURE_DELAY = 0.12  # giãn thời gian xử lý để thấy concurrency + queue depth

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        _failures.append(name)


# ── fixture server: đếm request in-flight cực đại (= số browser đồng thời) ─────
class _Stats:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.inflight = 0
        self.max_inflight = 0


_stats = _Stats()
_LIVE = (FIXTURES / "live.html").read_bytes()


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        with _stats.lock:
            _stats.inflight += 1
            _stats.max_inflight = max(_stats.max_inflight, _stats.inflight)
        try:
            time.sleep(FIXTURE_DELAY)  # mô phỏng thời gian check thật
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(_LIVE)
        finally:
            with _stats.lock:
                _stats.inflight -= 1

    def log_message(self, *a: object) -> None:
        return


def start_fixture_server() -> tuple[ThreadingHTTPServer, str]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    host, port = httpd.server_address[0], httpd.server_address[1]
    return httpd, f"http://{host}:{port}"


# ── helpers ─────────────────────────────────────────────────────────────────
def http_json(method: str, url: str, body: dict | None = None) -> tuple[int, dict]:
    import json

    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, {}


def psql(sql: str) -> str:
    out = subprocess.run(
        ["docker", "exec", "fastcheck-postgres", "psql", "-U", "fastcheck", "-d", "fastcheck",
         "-tA", "-c", sql],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def queue_depth(queue: str = "job.pending") -> int:
    out = subprocess.run(
        ["docker", "exec", "fastcheck-rabbitmq", "rabbitmqctl", "list_queues", "name", "messages"],
        capture_output=True, text=True, check=False,
    )
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == queue:
            return int(parts[1])
    return 0


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


def spawn(name: str, args: list[str], env: dict[str, str]) -> subprocess.Popen:
    LOG_DIR.mkdir(exist_ok=True)
    logf = open(LOG_DIR / f"{name}-p4load.log", "w", encoding="utf-8")
    return subprocess.Popen(args, cwd=str(REPO), env=env, stdout=logf, stderr=subprocess.STDOUT)


def kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, check=False)
    else:
        proc.terminate()


def read_log(name: str) -> str:
    p = LOG_DIR / f"{name}-p4load.log"
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
    for q in ("job.pending", "job.retry", "job.dlq"):
        subprocess.run(["docker", "exec", "fastcheck-rabbitmq", "rabbitmqctl", "purge_queue", q],
                       capture_output=True, text=True, check=False)


def seed_pool(n: int) -> None:
    psql("DELETE FROM check_logs WHERE target_url LIKE '%@fc/video/load-%';")
    psql("DELETE FROM check_jobs WHERE target_url LIKE '%@fc/video/load-%';")
    psql("DELETE FROM profiles WHERE account_label LIKE 'seed-load-%';")
    for i in range(n):
        psql("INSERT INTO profiles (platform, account_label, status, health_score) "
             f"VALUES ('TIKTOK','seed-load-{i}','AVAILABLE',100);")
    flush_cache()
    purge_queues()


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


# ── một request: POST + poll tới DONE, trả (post_ms, complete_ms | None) ───────
def one_request(idx: int) -> tuple[float, float | None]:
    t0 = time.monotonic()
    # Token 'live' → fixture live.html (LIVE). Hậu tố idx làm URL DUY NHẤT → 50 job riêng (không dedupe).
    code, r = http_json("POST", f"{API}/check", {"url": f"https://www.tiktok.com/@fc/video/live-{idx}"})
    post_ms = (time.monotonic() - t0) * 1000
    if code != 202:
        return post_ms, None
    tid = r.get("trace_id", "")
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        c, j = http_json("GET", f"{API}/check/{tid}")
        if c == 200 and j.get("status") in ("DONE", "DEAD_LETTER"):
            return post_ms, (time.monotonic() - t0) * 1000
        time.sleep(0.3)
    return post_ms, None


def run_phase(label: str, prefetch: int, worker_pool: int, fixture_base: str) -> dict:
    print(f"\n=== PHASE {label}: prefetch={prefetch}, pool={worker_pool} ===")
    _stats.max_inflight = 0
    _stats.inflight = 0
    seed_pool(10)

    env = dict(os.environ)
    env.update(
        FIXTURE_BASE_URL=fixture_base,
        GEMLOGIN_MODE="fake",
        ORCHESTRATOR_PREFETCH=str(prefetch),
        WORKER_MAX_CONCURRENCY=str(worker_pool),
        RESULT_CACHE_TTL_LIVE_SECONDS="5",
        PROFILE_SYNC_INTERVAL_SECONDS="3600",
        RESOURCE_MONITOR_INTERVAL_SECONDS="3600",
    )

    procs: dict[str, subprocess.Popen] = {}
    peak_ram_mb = 0.0
    max_depth = 0
    stop_sampler = threading.Event()

    def sampler() -> None:
        nonlocal peak_ram_mb, max_depth
        import psutil

        while not stop_sampler.is_set():
            try:
                d = queue_depth()
                max_depth = max(max_depth, d)
            except Exception:
                pass
            wp = procs.get("worker")
            if wp is not None:
                try:
                    proc = psutil.Process(wp.pid)
                    rss = proc.memory_info().rss
                    for c in proc.children(recursive=True):
                        try:
                            rss += c.memory_info().rss
                        except Exception:
                            pass
                    peak_ram_mb = max(peak_ram_mb, rss / (1024 * 1024))
                except Exception:
                    pass
            time.sleep(0.2)

    try:
        procs["api"] = spawn("api", ["node", "apps/api/dist/main.js"], env)
        procs["orchestrator"] = spawn("orchestrator", ["node", "apps/orchestrator/dist/main.js"], env)
        procs["worker"] = spawn(
            "worker", ["uv", "--directory", "apps/worker", "run", "python", "-m", "fastcheck_worker"], env
        )
        ok = wait_until(lambda: http_json("GET", f"{API}/health")[0] == 200, 40)
        station = wait_until(
            lambda: any(s.get("status") == "ONLINE"
                        for s in http_json("GET", f"{ORCH}/health")[1].get("stations", [])),
            40,
        )
        if not (ok and station):
            raise RuntimeError("services chưa sẵn sàng — xem .e2e-logs/*-p4load.log")

        threading.Thread(target=sampler, daemon=True).start()

        # 50 request đồng thời (VU tương đương k6).
        t_start = time.monotonic()
        posts: list[float] = []
        completes: list[float] = []
        with ThreadPoolExecutor(max_workers=N_REQUESTS) as pool:
            for post_ms, comp_ms in pool.map(one_request, range(N_REQUESTS)):
                posts.append(post_ms)
                if comp_ms is not None:
                    completes.append(comp_ms)
        wall = time.monotonic() - t_start
        stop_sampler.set()
        time.sleep(0.3)

        # KHÔNG crash sau tải.
        healthy = http_json("GET", f"{API}/health")[0] == 200 and http_json("GET", f"{ORCH}/health")[0] == 200
        depth_now = queue_depth()
        orch_log = read_log("orchestrator")
        no_slot_churn = orch_log.count("không có station còn slot")

        return {
            "label": label,
            "prefetch": prefetch,
            "pool": worker_pool,
            "healthy": healthy,
            "completed": len(completes),
            "max_inflight": _stats.max_inflight,
            "max_queue_depth": max_depth,
            "queue_drained_to": depth_now,
            "peak_ram_mb": round(peak_ram_mb, 1),
            "p95_post_ms": round(percentile(posts, 0.95), 1),
            "p95_complete_ms": round(percentile(completes, 0.95), 1),
            "wall_s": round(wall, 1),
            "no_slot_churn": no_slot_churn,
        }
    finally:
        stop_sampler.set()
        for proc in procs.values():
            kill_tree(proc)
        time.sleep(1.0)


def main() -> int:
    httpd, fixture_base = start_fixture_server()
    print(f"fixture server: {fixture_base}")
    try:
        # Phase A: prefetch > pool → thấy churn requeue (backpressure lệch — INV-10).
        a = run_phase("A (prefetch>pool)", prefetch=20, worker_pool=POOL, fixture_base=fixture_base)
        # Phase B: prefetch = pool → backpressure nhất quán, sạch.
        b = run_phase("B (prefetch=pool)", prefetch=POOL, worker_pool=POOL, fixture_base=fixture_base)
    finally:
        httpd.shutdown()
        psql("DELETE FROM profiles WHERE account_label LIKE 'seed-load-%';")

    print("\n" + "=" * 64)
    for m in (a, b):
        print(f"\nPHASE {m['label']}:")
        print(f"  hoàn tất            : {m['completed']}/{N_REQUESTS}")
        print(f"  browser đồng thời tối đa (fixture in-flight): {m['max_inflight']}  (≤ pool {m['pool']}?)")
        print(f"  queue depth đỉnh    : {m['max_queue_depth']}  → rút về {m['queue_drained_to']}")
        print(f"  p95 POST latency    : {m['p95_post_ms']} ms")
        print(f"  p95 hoàn tất job    : {m['p95_complete_ms']} ms")
        print(f"  RAM đỉnh worker     : {m['peak_ram_mb']} MB")
        print(f"  wall time           : {m['wall_s']} s")
        print(f"  churn 'không có slot': {m['no_slot_churn']}")

    # ── Khẳng định ──
    check("A+B: không crash (health 200 sau tải)", a["healthy"] and b["healthy"])
    check("A+B: hoàn tất đủ 50/50 job", a["completed"] == N_REQUESTS and b["completed"] == N_REQUESTS,
          f"A={a['completed']} B={b['completed']}")
    check("browser đồng thời ≤ pool (cả 2 phase)",
          a["max_inflight"] <= POOL and b["max_inflight"] <= POOL,
          f"A={a['max_inflight']} B={b['max_inflight']} pool={POOL}")
    check("queue XẾP HÀNG rồi RÚT (depth đỉnh>0, rút về 0)",
          a["max_queue_depth"] > 0 and a["queue_drained_to"] == 0 and b["queue_drained_to"] == 0,
          f"A đỉnh={a['max_queue_depth']}→{a['queue_drained_to']}, B→{b['queue_drained_to']}")
    check("prefetch>pool CHURN nhiều hơn prefetch=pool (INV-10)",
          a["no_slot_churn"] > b["no_slot_churn"],
          f"A={a['no_slot_churn']} vs B={b['no_slot_churn']}")

    print("\n" + "=" * 64)
    if _failures:
        print(f"KẾT QUẢ: {len(_failures)} FAIL → {_failures}")
        return 1
    print("KẾT QUẢ: TẤT CẢ PASS (load test)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
