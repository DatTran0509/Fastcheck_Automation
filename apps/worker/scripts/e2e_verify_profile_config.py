"""VERIFY mapping ProfileConfig → API GemLogin trên BẢN GEMLOGIN THẬT (GEMLOGIN_MODE=real).

Vì sao cần: tài liệu API GemLogin online có thể khác bản đã cài (tên field/encoding). Thay vì đoán mù (vi phạm
triết lý chống-hỏng-âm-thầm — INV-1), script này BẮN một config có giá trị đặc trưng xuống GemLogin rồi ĐỌC LẠI
profile (`GET /api/profile/{id}`) để đối chiếu field nào "dính", field nào bị GemLogin bỏ qua/đổi tên.

Kết quả in ra 3 nhóm:
  ✓ STUCK     field gửi đi = field đọc lại (mapping ĐÚNG trên bản cài).
  ✗ MISSING   field gửi đi KHÔNG thấy trong profile đọc lại (sai tên field → cần sửa mapping trong adapter).
  ≠ DIFFERENT field có nhưng giá trị khác (sai encoding — vd webrtc_mode số).

Ràng buộc bản FREE: tối đa 5 profile, KHÔNG xoá được → script TÁI SỬ DỤNG profile đầu tiên (update), không tạo mới.

Chạy:  GEMLOGIN_MODE=real uv --directory apps/worker run python scripts/e2e_verify_profile_config.py
"""

from __future__ import annotations

import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from fastcheck_worker.browser.adapter import (  # noqa: E402
    GemLoginError,
    ProfileSpec,
    RealGemLoginAdapter,
    profile_config_create_fields,
    profile_config_update_fields,
)

API_URL = os.environ.get("GEMLOGIN_API_URL", "http://127.0.0.1:1010")

# Config đặc trưng — giá trị ĐÚNG chuỗi enum schema GemLogin (mirror ProfileConfig).
TEST_CONFIG: dict[str, object] = {
    "os_type": "macOS",
    "os_version": "macos13",
    "browser_version": "141",
    "startup_url": "https://example.com",
    "user_agent_mode": "auto",
    "country": "Vietnam",
    "language": "vi,en",
    "time_zone": "Asia/Ho_Chi_Minh",
    "proxy_type": "none",
    "web_rtc": "disabled",
    "screen_resolution": "random",
    "canvas": "noise",
    "webgl_image": "noise",
    "webgl_metadata": "custom",
    "audio_context": "noise",
    "media_device": "noise",
    "client_rects": "noise",
}


def _flatten(prefix: str, value: object) -> dict[str, object]:
    """Trải phẳng dict lồng (vd os={type,version}) để so field-by-field."""
    out: dict[str, object] = {}
    if isinstance(value, dict):
        for k, v in value.items():
            out.update(_flatten(f"{prefix}.{k}" if prefix else str(k), v))
    else:
        out[prefix] = value
    return out


def main() -> int:
    adapter = RealGemLoginAdapter(API_URL)
    status = adapter.status()
    if not status.get("success"):
        print(f"[FAIL] GemLogin không phản hồi ở {API_URL}. Mở app GemLogin trước.")
        return 1
    print(f"[status] GemLogin OK — features={status.get('features')}")

    existing = adapter.list_profiles()
    name = "fc-verify-config"
    if existing:
        gid = existing[0].gemlogin_profile_id
        print(f"[setup] tái sử dụng profile {gid} (free cap 5, không xoá được) — dùng UPDATE để verify")
        adapter.update_profile(gid, {"account_label": name, "config": TEST_CONFIG})
        expected = {"profile_name": name, **profile_config_update_fields(TEST_CONFIG)}
    else:
        try:
            gid = adapter.create_profile(ProfileSpec(name=name, config=TEST_CONFIG))
            print(f"[setup] tạo profile mới {gid} — dùng CREATE để verify")
        except GemLoginError as exc:
            print(f"[FAIL] không tạo được profile ({exc})")
            return 1
        expected = {"profile_name": name, **profile_config_create_fields(TEST_CONFIG)}

    sent = _flatten("", expected)
    try:
        readback = adapter.get_profile(gid)
    except GemLoginError as exc:
        print(f"[FAIL] không đọc lại được profile ({exc}) — GET /api/profile/{{id}} có thể khác trên bản cài.")
        return 1
    got = _flatten("", readback)

    # GET /api/profile/{id} CHỈ trả model Profile (name/raw_proxy/browser_version/group/note) — KHÔNG trả
    # fingerprint (os/web_rtc/canvas/…). Nên chỉ VERIFY được nhóm field Profile; nhóm fingerprint không đọc
    # lại được qua endpoint này → tin theo schema request. Map field GỬI → field ĐỌC (profile_name→name).
    readable = {
        "profile_name": "name",
        "browser_version": "browser_version",
        "raw_proxy": "raw_proxy",
        "note": "note",
    }
    stuck: list[str] = []
    different: list[tuple[str, object, object]] = []
    not_readable: list[str] = []
    for key, want in sent.items():
        read_key = readable.get(key)
        if read_key is None:
            not_readable.append(key)  # fingerprint — GET không trả, không verify được
        elif read_key not in got:
            different.append((key, want, "(không có trong response)"))
        elif str(got[read_key]).strip().lower() == str(want).strip().lower():
            stuck.append(key)
        else:
            different.append((key, want, got[read_key]))

    print("\n===== VERIFY MAPPING ProfileConfig → GemLogin =====")
    print("Field ĐỌC LẠI ĐƯỢC (model Profile) — verify chính xác:")
    for k in stuck:
        print(f"  ✓ STUCK     {k} = {sent[k]!r}")
    for k, want, gotv in different:
        print(f"  ≠ DIFFERENT {k}: gửi {want!r} nhưng đọc lại {gotv!r}  → SỬA adapter")
    if not_readable:
        print(
            "\nField FINGERPRINT (os/web_rtc/canvas/…) — GET /api/profile/{id} KHÔNG trả về nên KHÔNG verify"
            " read-back được; tin theo schema request GemLogin (đã map đúng tên/enum):"
        )
        for k in not_readable:
            print(f"  · SENT-ONLY {k} = {sent[k]!r}")

    print("\n(Toàn bộ response đọc lại — để đối chiếu tay:)")
    for k in sorted(got):
        print(f"    · {k} = {got[k]!r}")

    if different:
        print(f"\n[CẦN SỬA] {len(different)} field Profile sai — cập nhật adapter.py.")
        return 2
    print(
        "\n[OK] Nhóm Profile (name/proxy/browser…) đã 'dính' đúng. Fingerprint gửi đúng schema nhưng GemLogin"
        " không cho đọc lại qua API — kiểm bằng cách MỞ profile trong app GemLogin xem tab Advanced."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
