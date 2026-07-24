"""execute_login (§7 — Server GỌI chạy kịch bản login) ở FAKE mode: chứng minh đường lệnh login.run.

Chạy CHÍNH strategy thật trên `_FakeLoginPage` tất định (logic login chi tiết đã test ở test_login.py).
Bất biến kiểm: cookie có → LOGGED_IN + fresh_cookie; cookie rỗng → COOKIE_DEAD (không đoán); info TikTok →
LOGGED_IN; FB + info → LoginError (đúng phạm vi Excel).
"""

from __future__ import annotations

import pytest

from fastcheck_worker.contracts import Platform
from fastcheck_worker.login import LoginError
from fastcheck_worker.login.base import LoginOutcome
from fastcheck_worker.login.execute import execute_login

# Adapter KHÔNG được dùng ở fake mode (chỉ real mode mở browser) → None an toàn.
_NO_ADAPTER = None  # type: ignore[var-annotated]


def _run(**kw: object):
    params: dict[str, object] = {
        "adapter": _NO_ADAPTER,
        "gemlogin_mode": "fake",
        "gemlogin_profile_id": "1",
        "cookie": None,
        "username": None,
        "password": None,
        "otp_secret": None,
    }
    params.update(kw)
    return execute_login(**params)  # type: ignore[arg-type]


def test_cookie_login_ok_returns_logged_in_and_fresh_cookie() -> None:
    r = _run(platform=Platform.TIKTOK, method="COOKIE", cookie="x")
    assert r.outcome == LoginOutcome.LOGGED_IN
    assert r.fresh_cookie  # để orchestrator mã hoá & refresh (spec §4.4)


def test_cookie_login_empty_cookie_is_dead_not_logged_in() -> None:
    # Không có cookie → không thấy guard → COOKIE_DEAD (INV-2: không đoán đã đăng nhập).
    r = _run(platform=Platform.FACEBOOK, method="COOKIE", cookie="")
    assert r.outcome == LoginOutcome.COOKIE_DEAD


@pytest.mark.parametrize("platform", [Platform.TIKTOK, Platform.TWITTER, Platform.YOUTUBE])
def test_info_login_ok_for_tiktok_x_youtube(platform: Platform) -> None:
    # X: user/pass gốc; TikTok & YouTube: qua Google (tài khoản Google). Tất cả → LOGGED_IN ở fake mode.
    r = _run(platform=platform, method="INFO", username="user", password="pass")
    assert r.outcome == LoginOutcome.LOGGED_IN


def test_info_login_unsupported_for_facebook_raises() -> None:
    # Facebook chỉ login-by-cookie → yêu cầu info → LoginError (báo ra, không đoán).
    with pytest.raises(LoginError):
        _run(platform=Platform.FACEBOOK, method="INFO", username="user", password="pass")


def test_userpass_login_x_logs_in() -> None:
    # X native user/pass + 2FA(TOTP): identifier → password → 2FA → home. Happy-path KHÔNG đụng Hotmail.
    r = _run(
        platform=Platform.TWITTER, method="USERPASS", username="u", password="p", otp_secret="JBSWY3DPEHPK3PXP"
    )
    assert r.outcome == LoginOutcome.LOGGED_IN
    assert r.fresh_cookie


@pytest.mark.parametrize("platform", [Platform.TIKTOK, Platform.FACEBOOK, Platform.YOUTUBE])
def test_userpass_login_unsupported_except_x(platform: Platform) -> None:
    # USERPASS CHỈ cho X (native user/pass). Nền tảng khác → LoginError (báo ra, không đoán — INV-1).
    with pytest.raises(LoginError):
        _run(platform=platform, method="USERPASS", username="u", password="p")
