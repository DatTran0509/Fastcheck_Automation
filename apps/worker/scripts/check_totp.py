"""Kiểm chứng TOTP: hệ thống có sinh ĐÚNG mã 6 số giống app Google Authenticator trên điện thoại không.

Dùng CHÍNH `generate_totp` của production (fastcheck_worker.login.base) nên chứng minh đúng code đang chạy.
So mã in ra dưới đây với app Authenticator trên điện thoại — phải TRÙNG trong cùng cửa sổ 30s.

Chạy:  uv --directory apps/worker run python scripts/check_totp.py
       (hoặc truyền secret trực tiếp: ... scripts/check_totp.py "6erp msan hbrr 5z4e ...")

Lưu ý:
  * Dấu cách / hoa-thường trong secret KHÔNG quan trọng (hàm tự chuẩn hoá) — dán y như Google hiện đều được.
  * Mã phụ thuộc ĐỒNG HỒ máy (giờ UTC), KHÔNG phụ thuộc timezone GemLogin. Nếu đồng hồ máy trạm lệch > ~30s
    thì mã sẽ SAI dù secret đúng → bật đồng bộ giờ (NTP) trên máy trạm.
  * Đây là script CHẨN ĐOÁN dùng một lần — XOÁ sau khi kiểm xong. KHÔNG hardcode secret vào file (nhập lúc chạy).
"""

from __future__ import annotations

import getpass
import pathlib
import sys
import time

# Cho phép chạy như script độc lập: thêm apps/worker (chứa package fastcheck_worker) vào sys.path.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from fastcheck_worker.login.base import generate_totp  # noqa: E402


def main() -> int:
    if len(sys.argv) > 1:
        secret = sys.argv[1]
        print("(secret nhận từ tham số dòng lệnh — nhớ xoá khỏi lịch sử shell sau khi kiểm)")
    else:
        # getpass: KHÔNG echo secret ra màn hình / không lưu vào lịch sử shell.
        secret = getpass.getpass("Dán secret key (Google hiện, có/không dấu cách đều được): ")

    if not secret.strip():
        print("Chưa nhập secret.")
        return 1

    print("\nSo mã dưới đây với app Google Authenticator trên điện thoại (Ctrl+C để dừng):\n")
    try:
        last = ""
        while True:
            code = generate_totp(secret)
            remaining = 30 - int(time.time()) % 30
            marker = "  <-- MÃ MỚI" if code != last else ""
            print(f"  {code}   (đổi mã sau {remaining:2d}s){marker}")
            last = code
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nĐã dừng.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
