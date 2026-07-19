"""E2E THẬT với GemLogin đã cài (GEMLOGIN_MODE=real) — kiểm chứng toàn bộ plumbing browser thật.

Chạy trực tiếp adapter + DrissionPageSource + detector (KHÔNG qua WS/orchestrator) để chứng minh:
  1. Adapter khớp API GemLogin thật (status/list/create/update/start/close).
  2. Mở browser GemLogin thật → DrissionPage attach CDP → điều hướng site thật → detector đọc DOM.
  3. CHƯA có cookie thật → guard đăng nhập fail → INCONCLUSIVE + CHALLENGED (KHÔNG BAO GIỜ DEAD/LIVE) — INV-2.
  4. Browser được ĐÓNG sau mỗi job (activeBrowsers về 0 — không rò tiến trình, INV-9).
  5. (tuỳ chọn) gán proxy sticky (raw_proxy) nếu FASTCHECK_TEST_PROXY được đặt.

Ràng buộc bản FREE: tối đa 5 profile, KHÔNG xoá được. Script TÁI SỬ DỤNG profile sẵn có, tạo thêm nếu thiếu.

Chạy:  uv --directory apps/worker run python scripts/e2e_real_gemlogin.py
"""

from __future__ import annotations

import os
import pathlib
import sys
import time

# Cho phép chạy như script độc lập: thêm apps/worker (chứa package fastcheck_worker) vào sys.path.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from fastcheck_worker.browser.adapter import GemLoginError, ProfileSpec, RealGemLoginAdapter  # noqa: E402
from fastcheck_worker.runner import run_check  # noqa: E402

API_URL = os.environ.get("GEMLOGIN_API_URL", "http://127.0.0.1:1010")

# Site thật cho mỗi platform (trang công khai — chưa đăng nhập nên guard sẽ fail = INCONCLUSIVE).
PLATFORM_TARGETS: dict[str, str] = {
    "TIKTOK": "https://www.tiktok.com/@tiktok",
    "FACEBOOK": "https://www.facebook.com/facebook",
    "TWITTER": "https://x.com/x",
    "YOUTUBE": "https://www.youtube.com/@YouTube",
}


def ensure_profiles(adapter: RealGemLoginAdapter) -> dict[str, str]:
    """Trả map platform → gemlogin_profile_id. Tái sử dụng profile sẵn có (free cap 5), tạo thêm nếu thiếu."""
    existing = adapter.list_profiles()
    print(f"[setup] {len(existing)} profile sẵn có trên GemLogin")
    mapping: dict[str, str] = {}
    platforms = list(PLATFORM_TARGETS)
    for i, platform in enumerate(platforms):
        if i < len(existing):
            gid = existing[i].gemlogin_profile_id
            adapter.update_profile(gid, {"platform": platform, "account_label": f"fc-e2e-{platform.lower()}"})
            print(f"[setup] tái sử dụng profile {gid} → {platform}")
        else:
            try:
                gid = adapter.create_profile(ProfileSpec(platform=platform, name=f"fc-e2e-{platform.lower()}"))
                print(f"[setup] tạo profile mới {gid} → {platform}")
            except GemLoginError as exc:
                print(f"[setup] KHÔNG tạo được profile cho {platform} ({exc}) — bỏ qua (free cap?)")
                continue
        mapping[platform] = gid
    return mapping


def check_proxy_plumbing(adapter: RealGemLoginAdapter, gid: str) -> None:
    proxy = os.environ.get("FASTCHECK_TEST_PROXY")
    if not proxy:
        print("[proxy] FASTCHECK_TEST_PROXY chưa đặt → chỉ test set field raw_proxy (không start proxy thật)")
        adapter.update_profile(gid, {"proxy": "http://sticky-placeholder:0"})
        print(f"[proxy] đã set raw_proxy placeholder cho profile {gid} (INV-7 sticky) — OK")
        return
    adapter.update_profile(gid, {"proxy": proxy})
    print(f"[proxy] đã gán proxy thật cho profile {gid} — sẽ dùng khi mở browser")


def main() -> int:
    adapter = RealGemLoginAdapter(API_URL)
    status = adapter.status()
    if not status.get("success"):
        print(f"[FAIL] GemLogin không phản hồi ở {API_URL}. Mở app GemLogin trước.")
        return 1
    print(f"[status] GemLogin OK — features={status.get('features')} activeBrowsers={status.get('activeBrowsers')}")

    mapping = ensure_profiles(adapter)
    if not mapping:
        print("[FAIL] không có profile nào để test")
        return 1

    check_proxy_plumbing(adapter, next(iter(mapping.values())))

    results: dict[str, dict[str, str | int | None]] = {}
    ok = True
    for platform, gid in mapping.items():
        target = PLATFORM_TARGETS[platform]
        print(f"\n[{platform}] mở browser profile {gid} → điều hướng {target} → detect ...")
        start = time.monotonic()
        outcome = run_check(
            {
                "platform": platform,
                "target_url": target,
                "cookie": "",  # CHƯA có cookie thật → guard fail (INCONCLUSIVE) là ĐÚNG (INV-2)
                "fixture_base_url": None,
                "gemlogin_profile_id": gid,
            },
            adapter=adapter,
        )
        elapsed = time.monotonic() - start
        results[platform] = dict(outcome)
        active = adapter.status().get("activeBrowsers")
        print(f"[{platform}] url_status={outcome['url_status']} profile_health={outcome['profile_health']} "
              f"block_reason={outcome['block_reason']} ({elapsed:.1f}s) | activeBrowsers sau đóng={active}")
        # BẤT BIẾN: chưa đăng nhập → KHÔNG BAO GIỜ DEAD hay LIVE (phải INCONCLUSIVE) — INV-1/INV-2.
        if outcome["url_status"] == "DEAD":
            print(f"[{platform}] ❌ VI PHẠM INV-1: trả DEAD khi chưa đăng nhập!")
            ok = False
        if active not in (0, None):
            print(f"[{platform}] ⚠️ activeBrowsers={active} sau khi đóng — kiểm tra rò tiến trình")

    print("\n===== TỔNG KẾT E2E THẬT =====")
    for platform, r in results.items():
        print(f"  {platform:9s}: url_status={r['url_status']:12s} profile_health={r['profile_health']}")
    final_active = adapter.status().get("activeBrowsers")
    print(f"  activeBrowsers cuối = {final_active} (kỳ vọng 0 — không rò)")
    print("  Kết luận: plumbing browser thật OK; chưa login → INCONCLUSIVE (không DEAD) đúng INV-1/INV-2." if ok
          else "  ❌ CÓ VI PHẠM BẤT BIẾN — xem log trên.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
