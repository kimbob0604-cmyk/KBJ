"""DART 증분 피드 — kbj.data.public.dart.feed(묶음 F, §1.3·§4.1·§8.2).

정렬 = 접수 내림차순 가정 [확인 필요 #28]. 이미 본 접수번호가 나온 쪽에서 멈춘다(쪽 경계), `full` 은
끝까지, 쪽을 넘기는 사이 밀려 내려온 행은 한 번만, `max_pages` 에 걸리면 `complete=False`. 정정 제목
머리. 가짜 DART 는 합성 세계(11-16 하루 230건 + 잡음 — 100건 쪽 경계)를 접수 시각대로 보여 준다.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from typing import Any

import httpx
import pytest

from kbj.core.time import KST
from kbj.data.public.dart.client import DartClient, DartError
from kbj.data.public.dart.feed import CORRECTION_PREFIXES, base_title, is_correction, scan_new
from tests.fakes.dart_server import KEY, FakeDart
from tests.fixtures.synthetic.p4_world import TODAY, build_world
from tests.unit.data.public._support import RecordingLimiter

NOON = datetime(2026, 11, 16, 12, 0, tzinfo=KST)
EVENING = datetime(2026, 11, 16, 19, 59, tzinfo=KST)  # 그날 공시 200건 넘게(쪽 경계 셋)


def client_for(fake: FakeDart) -> DartClient:
    return DartClient(
        KEY, transport=fake.transport("test"), limiter=RecordingLimiter(), retries=1, backoff_s=0
    )


def visible(at: datetime, day: date = TODAY) -> list[str]:
    """그 시각까지 접수된 그날 공시(접수 내림차순)."""
    ds = build_world().disclosures_on(day, now=at)
    return [d.rcept_no for d in sorted(ds, key=lambda d: (d.posted_at, d.rcept_no), reverse=True)]


class Seen:
    def __init__(self, known: Iterable[str] = ()) -> None:
        self.known = set(known)
        self.asked = 0

    def __call__(self, ids: Iterable[str]) -> set[str]:
        self.asked += 1
        return {i for i in ids if i in self.known}


def test_first_scan_reads_every_page_then_stops_at_the_seen_page() -> None:
    fake = FakeDart(lambda: EVENING, world=build_world())
    c = client_for(fake)
    want = visible(EVENING)
    assert len(want) > 200  # 쪽 경계를 넘는다
    first = scan_new(c, TODAY, Seen())
    assert [d.rcept_no for d in first.items] == want
    assert (first.complete, first.stopped_at, first.pages) == (True, None, len(want) // 100 + 1)
    assert first.total_count == len(want)
    # 다음 실행: 앞 쪽에 이미 본 것이 있으면 그 쪽에서 멈춘다(호출 1쪽)
    calls = len(fake.calls("list.json"))
    again = scan_new(c, TODAY, Seen(want))
    assert (again.new, again.pages, again.stopped_at, again.complete) == (0, 1, 1, True)
    assert len(fake.calls("list.json")) == calls + 1
    p = fake.calls("list.json")[-1].params
    assert (p["bgn_de"], p["end_de"], p["page_no"], p["page_count"]) == (
        "20261116", "20261116", "1", "100",
    )  # fmt: skip
    assert "corp_cls" not in p  # 전 시장


def test_new_rows_on_the_first_page_are_taken_before_stopping() -> None:
    clock = {"now": NOON}
    fake = FakeDart(lambda: clock["now"], world=build_world())
    c = client_for(fake)
    seen = Seen(visible(NOON))
    clock["now"] = datetime(2026, 11, 16, 14, 50, tzinfo=KST)
    got = scan_new(c, TODAY, seen)
    fresh = [r for r in visible(clock["now"]) if r not in seen.known]
    assert fresh and [d.rcept_no for d in got.items] == fresh
    assert got.stopped_at == 1


def test_full_mode_reads_every_page_and_filters_seen() -> None:
    fake = FakeDart(lambda: EVENING, world=build_world())
    want = visible(EVENING)
    seen = Seen(want[:50] + want[150:])
    got = scan_new(client_for(fake), TODAY, seen, full=True)
    assert [d.rcept_no for d in got.items] == want[50:150]
    assert got.complete and got.stopped_at is None and got.seen_count == len(want) - 100


def test_range_rescan_covers_the_weekend() -> None:
    fake = FakeDart(lambda: datetime(2026, 11, 16, 7, 0, tzinfo=KST), world=build_world())
    got = scan_new(client_for(fake), date(2026, 11, 13), Seen(), end=date(2026, 11, 15), full=True)
    assert got.complete and got.new == len(visible(datetime(2026, 11, 16, 7, 0, tzinfo=KST),
                                                   date(2026, 11, 13)))  # fmt: skip
    p = fake.calls("list.json")[0].params
    assert (p["bgn_de"], p["end_de"]) == ("20261113", "20261115")
    with pytest.raises(ValueError):
        scan_new(client_for(fake), date(2026, 11, 13), Seen(), end=date(2026, 11, 12))


def test_max_pages_cuts_the_scan_and_says_incomplete() -> None:
    fake = FakeDart(lambda: EVENING, world=build_world())
    got = scan_new(client_for(fake), TODAY, Seen(), max_pages=1)
    assert (got.pages, got.complete, got.new) == (1, False, 100)
    with pytest.raises(ValueError):
        scan_new(client_for(fake), TODAY, Seen(), max_pages=0)


def test_no_data_day_is_complete_and_empty() -> None:
    fake = FakeDart(lambda: NOON, world=build_world())
    got = scan_new(client_for(fake), date(2026, 11, 15), Seen())  # 일요일 — 013
    assert (got.new, got.complete, got.pages) == (0, True, 1)


def test_rows_pushed_down_while_paging_are_kept_once() -> None:
    """쪽을 넘기는 사이 새 공시가 들어오면 1쪽 끝 행이 2쪽 첫 행으로 밀린다."""

    def row(i: int) -> dict[str, str]:
        return {"corp_code": "99400010", "corp_name": "모의전자", "stock_code": "994010",
                "corp_cls": "Y", "report_nm": "기타경영사항(자율공시)",
                "rcept_no": f"2026111680{i:04d}", "flr_nm": "모의전자", "rcept_dt": "20261116",
                "rm": ""}  # fmt: skip

    pages = {
        "1": [row(i) for i in range(200, 100, -1)],  # 200..101
        "2": [row(i) for i in range(101, 1, -1)],  # 101(밀려 내려옴)..2
        "3": [row(1)],
    }

    def handler(req: httpx.Request) -> httpx.Response:
        p = req.url.params["page_no"]
        body: dict[str, Any] = {"status": "000", "message": "정상", "page_no": int(p),
                                "total_count": 201, "total_page": 3, "list": pages[p]}  # fmt: skip
        return httpx.Response(200, json=body)

    c = DartClient("k" * 40, transport=httpx.MockTransport(handler), limiter=RecordingLimiter())
    got = scan_new(c, TODAY, Seen())
    assert got.new == 200 and got.duplicates == 1 and got.complete


def test_list_failure_is_raised() -> None:
    fake = FakeDart(lambda: NOON, world=build_world())
    fake.inject("800", 1, endpoint="list.json")
    with pytest.raises(DartError):
        scan_new(client_for(fake), TODAY, Seen())


@pytest.mark.parametrize(
    ("title", "corr", "base"),
    [
        ("[기재정정]주식등의대량보유상황보고서(일반)", True, "주식등의대량보유상황보고서(일반)"),
        ("[첨부정정]사업보고서 (2025.12)", True, "사업보고서 (2025.12)"),
        ("[첨부추가]분기보고서 (2026.09)", True, "분기보고서 (2026.09)"),
        ("[변경등록]증권신고서(지분증권)", True, "증권신고서(지분증권)"),
        (" [기재정정] [첨부추가]사업보고서", True, "사업보고서"),
        ("[발행조건확정]증권신고서(채무증권)", False, "증권신고서(채무증권)"),
        ("주요사항보고서(전환사채권발행결정)", False, "주요사항보고서(전환사채권발행결정)"),
        ("[기재정정]", True, "[기재정정]"),
        ("", False, ""),
    ],
)
def test_correction_prefixes(title: str, corr: bool, base: str) -> None:
    assert is_correction(title) is corr
    assert base_title(title) == base


def test_correction_prefixes_follow_config() -> None:
    assert is_correction("[정정]공시", ("[정정]",))
    assert not is_correction("[기재정정]공시", ("[정정]",))
    assert CORRECTION_PREFIXES[0] == "[기재정정]"
