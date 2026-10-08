"""운용사 어댑터 공통 도우미(ET collectors.py `_f`·`_is_kr`·`isin_to_code`·`_ymd`·
`_rows_from_table`)."""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from kbj.core.rows import HoldingRow
from kbj.data.private.etf_issuers.base import (
    IssuerHttp,
    add_holding,
    holding,
    is_kr_code,
    isin_to_code,
    parse_num,
    rows_from_table,
    unescape_basic,
    ymd,
)


@pytest.mark.parametrize(
    ("isin", "code"),
    [
        ("KR7005930003", "005930"),  # 삼성전자
        ("KR7005931001", "005935"),  # 삼성전자우 — 차수 1 → 5
        ("KR7005381005", "005385"),  # 현대차우
        ("KR7005382003", "005387"),  # 현대차2우B — 차수 2 → 7
        ("KR70126Z0002", "0126Z0"),  # 영문 섞인 신규 코드
        ("kr7005930003", "005930"),
        ("KRD010010001", None),  # 원화예금
        ("KR4A01690002", None),  # 선물
        ("US0378331005", None),
        ("", None),
        (None, None),
    ],
)
def test_isin_to_code(isin: str | None, code: str | None) -> None:
    assert isin_to_code(isin) == code


def test_is_kr_code() -> None:
    assert is_kr_code("005930") and is_kr_code("0185L0") and is_kr_code(" 005930 ")
    assert not is_kr_code("AAPL US") and not is_kr_code("KRD010") and not is_kr_code("ABCDEF")
    assert not is_kr_code(None) and not is_kr_code("12345")


def test_parse_num_and_ymd_and_unescape() -> None:
    assert parse_num("1,234.5") == 1234.5 and parse_num("-") == 0.0 and parse_num(None) == 0.0
    assert parse_num(7) == 7.0
    assert ymd("2026.08.04") == ymd("20260804") == ymd("2026-08-04") == date(2026, 8, 4)
    assert ymd(None) is None and ymd("2026-13-01") is None and ymd("abc") is None
    assert unescape_basic("S&amp;P &lt;x&gt; &quot;q&quot;") == 'S&P <x> "q"'


def test_rows_from_table_and_sum() -> None:
    html = ("<table><tr><th>코드</th><th>이름</th></tr>"
            "<tr><td>005930</td><td><b>삼성</b></td><td>1,000</td><td>70,000,000</td>"
            "<td>12.5</td></tr><tr><td>AAPL US</td><td>애플</td><td>1</td><td>1</td><td>1</td></tr>"
            "<tr><td>000660</td><td>하닉</td></tr></table>")  # fmt: skip
    rows = rows_from_table(html, "tiger")
    assert list(rows) == ["005930"]
    h = rows["005930"]
    want = ("삼성", 1000.0, 7e7, 12.5, "ETF_ISSUERS:tiger")
    assert (h.name, h.qty, h.val, h.wt, h.source) == want
    acc: dict[str, HoldingRow] = {}
    add_holding(acc, holding("ace", "03502G", "국고", 0.0, 10.0, 100.0))
    add_holding(acc, holding("ace", "03502G", "국고", 0.0, 20.0, 200.0))
    assert (acc["03502G"].wt, acc["03502G"].val) == (30.0, 300.0)
    assert holding("ace", "005930", "  ", 1, 1, 1).name is None


def test_http_needs_absolute_url_and_passes_query() -> None:
    seen: list[str] = []

    def h(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        return httpx.Response(200, json={"ok": 1})

    http = IssuerHttp(httpx.Client(transport=httpx.MockTransport(h)), sleep=lambda s: None)
    assert http.json("x", "https://a.example/p?q=1") == {"ok": 1}
    assert seen == ["https://a.example/p?q=1"]  # params 가 없을 때 URL 의 쿼리를 지우지 않는다
    with pytest.raises(ValueError):
        http.text("x", "/relative")
    with pytest.raises(ValueError):
        http.cap_rate("a.example", 0)
