"""E2E Phase 5 (Test 1–4): circuit breaker + API docs + /metrics + dashboard SSE.

Chạy:  python scripts/e2e_phase5.py
Điều kiện: docker infra healthy, `pnpm build` (gồm api + orchestrator).

  1. Circuit breaker: bơm chuỗi BLOCKED (captcha) cho TIKTOK vượt ngưỡng → POST /check TIKTOK trả 503 +
     retry_after; FACEBOOK vẫn 202 (per-platform); sau cooldown → nhận job lại (202).
  2. API docs: /docs mở được; /docs/json chứa schema sinh TỪ zod DTO (url/trace_id/retry_after_seconds).
  3. /metrics: orchestrator phơi đủ metric (result theo platform+url_status, profiles, queue depth, duration,
     profile_health); số LIVE khớp số job đã chạy. API phơi request duration.
  4. Dashboard: /dashboard/snapshot + SSE /dashboard/stream — ba tỷ lệ LIVE/DEAD/INCONCLUSIVE TÁCH BIỆT,
     station ONLINE; kill worker → OFFLINE. KHÔNG có cookie/credential trong payload.
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
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from functools import partial
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


# ── fixture server: phục vụ thư mục fixtures (dead_404 → 404) ──────────────────
class _Handler(SimpleHTTPRequestHandler):
    def send_response(self, code: int, message: str | None = None) -> None:
        if self.path.split("?")[0] == "/dead_404.html" and code == 200:
            code = 404
        super().send_response(code, message)

    def log_message(self, *a: object) -> None:
        return


def start_fixture_server() -> tuple[ThreadingHTTPServer, str]:
    handler = partial(_Handler, directory=str(FIXTURES))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    host, port = httpd.server_address[0], httpd.server_address[1]
    return httpd, f"http://{host}:{port}"


# ── helpers ─────────────────────────────────────────────────────────────────
def http_json(method: str, url: str, body: dict | None = None) -> tuple[int, dict, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode()), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode() or "{}"
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {}
        return exc.code, payload, dict(exc.headers)


def http_text(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def read_one_sse(url: str, timeout: float = 8.0) -> dict | None:
    """Mở SSE, đọc tới dòng 'data:' đầu tiên, trả JSON đã parse."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            deadline = time.monotonic() + timeout
            for raw in resp:
                if time.monotonic() > deadline:
                    return None
                line = raw.decode("utf-8", errors="replace").strip()
                if line.startswith("data:"):
                    return json.loads(line[len("data:"):].strip())
    except Exception:
        return None
    return None


def psql(sql: str) -> str:
    out = subprocess.run(
        ["docker", "exec", "fastcheck-postgres", "psql", "-U", "fastcheck", "-d", "fastcheck",
         "-tA", "-c", sql],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def redis_del_pattern(pattern: str) -> None:
    keys = subprocess.run(
        ["docker", "exec", "fastcheck-redis", "redis-cli", "KEYS", pattern],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    if keys:
        subprocess.run(["docker", "exec", "fastcheck-redis", "redis-cli", "DEL", *keys],
                       capture_output=True, text=True, check=False)


def purge_queues() -> None:
    for q in ("job.pending", "job.retry", "job.dlq"):
        subprocess.run(["docker", "exec", "fastcheck-rabbitmq", "rabbitmqctl", "purge_queue", q],
                       capture_output=True, text=True, check=False)


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
    logf = open(LOG_DIR / f"{name}-p5.log", "w", encoding="utf-8")
    return subprocess.Popen(args, cwd=str(REPO), env=env, stdout=logf, stderr=subprocess.STDOUT)


def kill_tree(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, check=False)
    else:
        proc.terminate()


def reset() -> None:
    psql("DELETE FROM check_logs WHERE target_url LIKE '%@fc/video/p5%';")
    psql("DELETE FROM check_jobs WHERE target_url LIKE '%@fc/video/p5%';")
    psql("DELETE FROM profiles WHERE account_label LIKE 'seed-p5-%';")
    for i in range(8):
        psql("INSERT INTO profiles (platform, account_label, status, health_score) "
             f"VALUES ('TIKTOK','seed-p5-{i}','AVAILABLE',100);")
    redis_del_pattern("fastcheck:result:*")
    redis_del_pattern("cb:*")
    purge_queues()


def metric_value(text: str, needle: str) -> float | None:
    for line in text.splitlines():
        if line.startswith(needle) and not line.startswith("#"):
            try:
                return float(line.rsplit(" ", 1)[1])
            except (ValueError, IndexError):
                return None
    return None


def poll_done(tid: str, timeout: float = 40.0) -> str:
    result = {"status": ""}

    def done() -> bool:
        _, j, _ = http_json("GET", f"{API}/check/{tid}")
        result["status"] = j.get("status", "")
        return result["status"] in ("DONE", "DEAD_LETTER")

    wait_until(done, timeout)
    return result["status"]


def station_status_from_snapshot() -> str | None:
    code, snap, _ = http_json("GET", f"{ORCH}/dashboard/snapshot")
    if code != 200 or not snap.get("stations"):
        return None
    return snap["stations"][0].get("status")


def main() -> int:  # noqa: C901, PLR0912, PLR0915
    httpd, fixture_base = start_fixture_server()
    print(f"fixture server: {fixture_base}")

    env = dict(os.environ)
    env.update(
        FIXTURE_BASE_URL=fixture_base,
        GEMLOGIN_MODE="fake",
        WORKER_MAX_CONCURRENCY="4",
        ORCHESTRATOR_PREFETCH="4",
        HEARTBEAT_INTERVAL_MS="2000",
        HEARTBEAT_TIMEOUT_MS="20000",
        PROFILE_SYNC_INTERVAL_SECONDS="3600",
        # Circuit breaker: nhạy để test nhanh.
        CIRCUIT_MIN_SAMPLES="3",
        CIRCUIT_BLOCK_THRESHOLD="0.5",
        CIRCUIT_WINDOW_SECONDS="60",
        CIRCUIT_COOLDOWN_SECONDS="6",
        # auto-switch nhanh về DLQ để không còn kết quả BLOCKED lởn vởn lúc thăm dò hồi phục.
        ORCHESTRATOR_MAX_RETRIES="2",
        RETRY_BACKOFF_BASE_MS="300",
        PROFILE_COOLDOWN_SECONDS="2",
        DASHBOARD_STREAM_INTERVAL_MS="1000",
        DASHBOARD_RATIO_WINDOW_MINUTES="60",
    )

    procs: dict[str, subprocess.Popen | None] = {}
    try:
        reset()
        procs["api"] = spawn("api", ["node", "apps/api/dist/main.js"], env)
        procs["orchestrator"] = spawn("orchestrator", ["node", "apps/orchestrator/dist/main.js"], env)
        procs["worker"] = spawn(
            "worker", ["uv", "--directory", "apps/worker", "run", "python", "-m", "fastcheck_worker"], env
        )
        ok = wait_until(lambda: http_json("GET", f"{API}/health")[0] == 200, 40)
        online = wait_until(lambda: station_status_from_snapshot() == "ONLINE", 40)
        check("services + station ONLINE", ok and online)
        if not (ok and online):
            raise RuntimeError("services chưa sẵn sàng — xem .e2e-logs/*-p5.log")

        # ── chạy vài job LIVE để có dữ liệu metric/dashboard ─────────────────────
        live_tids = []
        for i in range(5):
            _, r, _ = http_json("POST", f"{API}/check", {"url": f"https://www.tiktok.com/@fc/video/p5live-{i}"})
            if r.get("trace_id"):
                live_tids.append(r["trace_id"])
        live_done = sum(1 for t in live_tids if poll_done(t) == "DONE")
        check("5 job LIVE hoàn tất (dữ liệu cho metric/dashboard)", live_done == 5, f"done={live_done}")

        # ── Test 2: API docs ────────────────────────────────────────────────────
        print("\n[2] API docs (/docs sinh từ zod):")
        code_docs, _ = http_text(f"{API}/docs")
        check("/docs mở được (200)", code_docs == 200, f"code={code_docs}")
        code_json, docs = http_text(f"{API}/docs/json")
        check("/docs/json trả OpenAPI (200)", code_json == 200, f"code={code_json}")
        for field in ("url", "trace_id", "retry_after_seconds", "url_hash", "INCONCLUSIVE"):
            check(f"docs chứa field/ngữ nghĩa '{field}' (schema từ DTO)", field in docs)

        # ── Test 3: /metrics ────────────────────────────────────────────────────
        print("\n[3] /metrics (Prometheus):")
        time.sleep(6)  # để gauge queue depth + refresh chạy
        m_code, mtext = http_text(f"{ORCH}/metrics")
        check("orchestrator /metrics 200", m_code == 200)
        required = [
            "fastcheck_check_result_total",
            "fastcheck_profile_health_total",
            "fastcheck_check_duration_ms",
            "fastcheck_queue_messages",
            "fastcheck_profiles",
            "fastcheck_worker_ram_mb",
            "fastcheck_circuit_open",
        ]
        for metric in required:
            check(f"metric '{metric}' có mặt", metric in mtext)
        live_count = metric_value(
            mtext, 'fastcheck_check_result_total{platform="TIKTOK",url_status="LIVE"}'
        )
        check("số LIVE (TIKTOK) khớp thực tế (>=5)", (live_count or 0) >= 5, f"live={live_count}")
        a_code, atext = http_text(f"{API}/metrics")
        check("api /metrics có request duration", a_code == 200 and "fastcheck_api_request_duration_ms" in atext)

        # ── Test 4a: dashboard snapshot + SSE + không cookie ─────────────────────
        print("\n[4] Dashboard (snapshot + SSE):")
        s_code, snap, _ = http_json("GET", f"{ORCH}/dashboard/snapshot")
        check("/dashboard/snapshot 200", s_code == 200)
        tiktok_ratio = next((r for r in snap.get("ratios", []) if r.get("platform") == "TIKTOK"), None)
        check("ratio TIKTOK có ĐỦ 3 trạng thái TÁCH BIỆT (live/dead/inconclusive)",
              tiktok_ratio is not None and all(k in tiktok_ratio for k in ("live", "dead", "inconclusive")),
              f"ratio={tiktok_ratio}")
        check("dashboard có station", len(snap.get("stations", [])) >= 1)
        raw_snap = json.dumps(snap).lower()
        check("KHÔNG lộ cookie/credential trong snapshot (INV-12)",
              "cookie" not in raw_snap and "ciphertext" not in raw_snap and "credential" not in raw_snap)
        sse = read_one_sse(f"{ORCH}/dashboard/stream")
        check("SSE /dashboard/stream đẩy snapshot realtime", sse is not None and "ratios" in (sse or {}))

        # ── Test 1: circuit breaker ─────────────────────────────────────────────
        print("\n[1] Circuit breaker (§10.6):")
        # Bơm chuỗi BLOCKED (captcha) cho TIKTOK.
        for i in range(6):
            http_json("POST", f"{API}/check", {"url": f"https://www.tiktok.com/@fc/video/p5captcha-{i}"})
            time.sleep(0.05)
        opened = wait_until(
            lambda: http_json("POST", f"{API}/check",
                              {"url": f"https://www.tiktok.com/@fc/video/p5live-probe-{time.time()}"})[0] == 503,
            15,
        )
        check("chuỗi BLOCKED → API TIKTOK trả 503", opened)
        code503, body503, hdr503 = http_json("POST", f"{API}/check",
                                              {"url": "https://www.tiktok.com/@fc/video/p5live-x"})
        has_retry_hdr = any(k.lower() == "retry-after" for k in hdr503)  # header name case-insensitive
        check("503 có retry_after_seconds > 0 + Retry-After header",
              code503 == 503 and body503.get("retry_after_seconds", 0) > 0 and has_retry_hdr,
              f"code={code503} body={body503} retryHdr={has_retry_hdr}")
        # Platform khác vẫn nhận job (circuit per-platform).
        code_fb, _, _ = http_json("POST", f"{API}/check", {"url": "https://www.facebook.com/somepage/posts/1"})
        check("FACEBOOK vẫn 202 (circuit per-platform)", code_fb == 202, f"code={code_fb}")
        # Sau cooldown → hồi, nhận job lại.
        recovered = wait_until(
            lambda: http_json("POST", f"{API}/check",
                              {"url": f"https://www.tiktok.com/@fc/video/p5live-recover-{time.time()}"})[0] == 202,
            25,
        )
        check("sau cooldown → circuit đóng, TIKTOK nhận job lại (202)", recovered)

        # ── Test 4b: kill worker → dashboard OFFLINE ─────────────────────────────
        print("\n[4b] Kéo tắt station → dashboard OFFLINE:")
        kill_tree(procs["worker"])
        procs["worker"] = None
        offline = wait_until(lambda: station_status_from_snapshot() == "OFFLINE", 20)
        check("dashboard phản ánh station OFFLINE sau khi tắt", offline,
              f"status={station_status_from_snapshot()}")

    finally:
        for proc in procs.values():
            kill_tree(proc)
        httpd.shutdown()
        psql("DELETE FROM profiles WHERE account_label LIKE 'seed-p5-%';")
        psql("UPDATE profiles SET status='AVAILABLE', cooldown_until=NULL, lease_expires_at=NULL, "
             "assigned_station_id=NULL WHERE platform='TIKTOK';")
        redis_del_pattern("cb:*")
        time.sleep(1.0)

    print("\n" + "=" * 60)
    if _failures:
        print(f"KẾT QUẢ: {len(_failures)} FAIL → {_failures}")
        return 1
    print("KẾT QUẢ: TẤT CẢ PASS (1, 2, 3, 4)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
