"""Parse cookie (JSON array hoặc chuỗi 'k=v; k2=v2') → list dict cho DrissionPage `set.cookies`.

Một nguồn duy nhất để cả detector (`DrissionPageSource`) và login (`DrissionLoginPage`) nạp cookie GIỐNG
NHAU — cookie phải nạp TRƯỚC điều hướng (INV-2). KHÔNG log giá trị cookie ở đây (INV-12).
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse


def parse_cookies(cookie: str, target_url: str) -> list[dict[str, Any]]:
    """Cookie có thể là JSON array [{name,value,domain,...}] hoặc chuỗi 'k=v; k2=v2'.

    Chuỗi thô → gán domain theo host của `target_url` (sai domain → browser bỏ qua trong im lặng — INV-2).
    """
    cookie = cookie.strip()
    if not cookie:
        return []
    if cookie[:1] in "[{":
        parsed = json.loads(cookie)
        items = parsed if isinstance(parsed, list) else [parsed]
        return [c for c in items if isinstance(c, dict) and c.get("name")]
    domain = urlparse(target_url).hostname or ""
    out: list[dict[str, Any]] = []
    for part in cookie.split(";"):
        if "=" in part:
            name, value = part.split("=", 1)
            out.append({"name": name.strip(), "value": value.strip(), "domain": f".{domain}", "path": "/"})
    return out
