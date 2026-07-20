"""`HtmlPageView` — đọc tín hiệu DOM từ HTML đã tải, không phụ thuộc browser.

Vì sao tự parse thay vì grep chuỗi: INV-8 yêu cầu *selector bền + fallback* (role/aria/testid),
không phải so khớp chuỗi thô. Engine này hiện thực một tập con selector đủ dùng cho detector
(tag, #id, .class, [attr], [attr=value] và tổ hợp) trên `html.parser` của stdlib — chạy được ở
mọi môi trường (golden set, CI) mà không cần Chromium. Adapter DrissionPage thật (Phase sau) hiện
thực cùng `PageView` Protocol nên detector không cần biết nguồn DOM đến từ đâu.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

# Một bộ chọn đơn (không có tổ hợp con cháu): tag?, #id?, .class*, [attr(op value)?]*
# Toán tử attr hỗ trợ: `=` (bằng), `*=` (chứa), `^=` (bắt đầu), `$=` (kết thúc), `~=` (token).
_TOKEN_RE = re.compile(
    r"""
    (?P<tag>^[a-zA-Z][\w-]*)
    | \#(?P<id>[\w-]+)
    | \.(?P<cls>[\w-]+)
    | \[\s*(?P<attr>[\w:-]+)\s*
        (?:(?P<op>[*^$~|]?=)\s*(?P<q>["'])?(?P<val>[^"'\]]*)(?P=q)?\s*)?
      \]
    """,
    re.VERBOSE,
)


def _attr_matches(op: str | None, actual: str, expected: str) -> bool:
    if op is None:
        return True  # chỉ cần có thuộc tính
    if op == "=":
        return actual == expected
    if op == "*=":
        return expected in actual
    if op == "^=":
        return actual.startswith(expected)
    if op == "$=":
        return actual.endswith(expected)
    if op in ("~=", "|="):
        return expected in actual.split()
    return False


class _Element:
    __slots__ = ("tag", "attrs", "classes")

    def __init__(self, tag: str, attrs: dict[str, str]) -> None:
        self.tag = tag
        self.attrs = attrs
        self.classes = set((attrs.get("class") or "").split())


class _Selector:
    """Một bộ chọn đã phân tích. `matches(el)` = TẤT CẢ ràng buộc đúng (AND).

    `empty=True` khi chuỗi không phân tích được ra ràng buộc nào → matches() luôn False.
    (KHÔNG được coi selector rỗng là "khớp tất cả": đó là hỏng âm thầm — mọi trang thành BLOCKED.)
    """

    __slots__ = ("tag", "id", "classes", "attrs", "empty")

    def __init__(self, raw: str) -> None:
        self.tag: str | None = None
        self.id: str | None = None
        self.classes: list[str] = []
        self.attrs: list[tuple[str, str | None, str]] = []  # (name, op|None, value)
        for m in _TOKEN_RE.finditer(raw.strip()):
            if m.group("tag"):
                self.tag = m.group("tag").lower()
            elif m.group("id"):
                self.id = m.group("id")
            elif m.group("cls"):
                self.classes.append(m.group("cls"))
            elif m.group("attr"):
                op = m.group("op")
                val = m.group("val") or ""
                self.attrs.append((m.group("attr").lower(), op, val))
        self.empty = (
            self.tag is None and self.id is None and not self.classes and not self.attrs
        )

    def matches(self, el: _Element) -> bool:
        if self.empty:
            return False
        if self.tag is not None and el.tag != self.tag:
            return False
        if self.id is not None and el.attrs.get("id") != self.id:
            return False
        for cls in self.classes:
            if cls not in el.classes:
                return False
        for name, op, val in self.attrs:
            if name not in el.attrs:
                return False
            if not _attr_matches(op, el.attrs[name], val):
                return False
        return True


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[_Element] = []
        self.text_parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        self.elements.append(_Element(tag.lower(), attr_map))
        if tag.lower() in ("script", "style"):
            self._skip_depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        self.elements.append(_Element(tag.lower(), attr_map))

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in ("script", "style") and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        # Bỏ text trong <script>/<style> để soft-404 không khớp nhầm chuỗi trong JS/JSON nhúng.
        if self._skip_depth == 0:
            self.text_parts.append(data)


class HtmlPageView:
    """`PageView` dựng từ HTML + HTTP status + URL cuối cùng. Xem `base.PageView`."""

    def __init__(self, html: str, http_status: int | None, final_url: str) -> None:
        collector = _Collector()
        collector.feed(html)
        self._elements = collector.elements
        self._text = " ".join(collector.text_parts).lower()
        self._http_status = http_status
        self._final_url = final_url

    @property
    def http_status(self) -> int | None:
        return self._http_status

    @property
    def final_url(self) -> str:
        return self._final_url

    def has_element(self, *selectors: str) -> bool:
        """True nếu BẤT KỲ selector nào khớp — nhiều selector = fallback bền (INV-8)."""
        parsed = [_Selector(s) for s in selectors]
        for el in self._elements:
            for sel in parsed:
                if sel.matches(el):
                    return True
        return False

    def text_contains(self, *needles: str) -> bool:
        """True nếu văn bản hiển thị chứa BẤT KỲ chuỗi nào (không phân biệt hoa thường)."""
        return any(n.lower() in self._text for n in needles)

    def text_length(self) -> int:
        """Độ dài text hiển thị — để phân biệt 'trang render xong' với 'shell trắng/JS chưa tải' (INV-8)."""
        return len(self._text.strip())

    def cookie_names(self) -> set[str]:
        """Fake/golden: KHÔNG mô phỏng cookie → rỗng → guard dùng DOM fallback (giữ nguyên hành vi golden)."""
        return set()
