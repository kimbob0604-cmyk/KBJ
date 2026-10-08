"""운용사 어댑터 9곳 — 합성 응답(가짜 운용사 서버·견본 파일)으로 목록·PDF 파싱·기준일·거르기.

원본 시험 없음(ET etf_tracker_v9 — docs/p3_design.md §1.11). ET 어댑터 머리말의 실측 주의를 시험으로
고정한다: 우선주 ISIN 보정, 해외·현금·선물·채권 코드 거르기, 휴장일 폴백, HANARO 미래일 클램프,
RISE 기준일 역추적, ACE 중복 코드 합산, KoAct 챌린지 재시도.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path

import httpx
import pytest

from kbj.core.quality import Quality
from kbj.data.private.etf_issuers.base import FundRef, IssuerAdapter, IssuerHttp
from kbj.data.private.etf_issuers.registry import ADAPTERS, KEYS, build
from tests.fakes.etf_issuer_server import (
    ETF_IN_FUND,
    PREF_CODE,
    FakeFund,
    FakeIssuers,
    funds_of,
    samples,
)

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "synthetic" / "etf_issuers"
TUE, MON = date(2026, 10, 6), date(2026, 10, 12)
FRI, SAT = date(2026, 10, 9), date(2026, 10, 10)


def setup(
    latest: date = TUE, today: date = date(2026, 10, 7), **kw: object
) -> tuple[FakeIssuers, dict[str, IssuerAdapter], list[float]]:
    w = FakeIssuers(latest=latest, **kw)  # type: ignore[arg-type]
    sleeps: list[float] = []
    http = IssuerHttp(httpx.Client(transport=w.transport()), sleep=sleeps.append)
    ads = {a.KEY: a for a in build(http, today=lambda: today)}
    return w, ads, sleeps


def ref_of(refs: list[FundRef], f: FakeFund) -> FundRef:
    return next(r for r in refs if r.fund_key == f.key)


# ── 목록 ──


@pytest.mark.parametrize("key", KEYS)
def test_universe(key: str) -> None:
    _, ads, _ = setup()
    refs = ads[key].universe()
    want = funds_of(key)
    assert sorted(r.fund_key for r in refs) == sorted(f.key for f in want)
    for f in want:
        r = ref_of(refs, f)
        assert r.ticker == f.ticker
        assert r.name == (None if key == "tiger" else f.name)  # TIGER 목록엔 이름이 없다


# ── PDF ──


@pytest.mark.parametrize("key", KEYS)
def test_holdings_keep_only_domestic_codes(key: str) -> None:
    w, ads, _ = setup()
    for f in funds_of(key):
        rows, real = ads[key].holdings(f.key, TUE)
        if f.kind == "us":
            assert rows == {}  # 해외형 — 국내 종목 0개
            continue
        assert real == TUE
        got = {c: h.qty for c, h in rows.items()}
        want = w.expected(f, TUE)
        if key == "ace" and f.kind == "kr":
            assert got.pop("990990") == 0.0  # 숫자 6자리 채권 — 남지만 수량 0(주의 7)
        assert got == want, key
        for h in rows.values():
            assert h.source == f"ETF_ISSUERS:{key}" and h.quality is Quality.OK
        if f.kind == "kr":
            assert ETF_IN_FUND in rows  # ETF 를 담은 행은 어댑터가 남긴다(분석에서 뺀다)


@pytest.mark.parametrize("key", ["hanaro", "rise", "plus"])
def test_preferred_share_isin_is_fixed(key: str) -> None:
    _, ads, _ = setup()
    rows, _ = ads[key].holdings(funds_of(key)[0].key, TUE)
    assert PREF_CODE in rows and "990011" not in rows


def test_ace_sums_duplicate_codes_and_drops_derivatives() -> None:
    _, ads, _ = setup()
    rows, _ = ads["ace"].holdings(funds_of("ace")[0].key, TUE)
    assert rows["990990"].val == 3.0e6 and rows["990990"].wt == pytest.approx(1.0)
    assert "A01690" not in rows and "03502G" not in rows


# ── 기준일 ──


@pytest.mark.parametrize("key", ["kodex", "koact", "timefolio", "plus", "sol"])
def test_fallback_issuers_report_real_date(key: str) -> None:
    _, ads, _ = setup(latest=FRI)
    rows, real = ads[key].holdings(funds_of(key)[0].key, SAT)
    assert rows and real == FRI


@pytest.mark.parametrize(("key", "back"), [("tiger", 7), ("ace", 5), ("hanaro", 7)])
def test_exact_date_issuers_walk_back(key: str, back: int) -> None:
    _, ads, _ = setup(latest=FRI, today=MON)
    rows, real = ads[key].holdings(funds_of(key)[0].key, MON)
    assert rows and real == FRI
    if key == "hanaro":
        return  # HANARO 는 미래일을 최신으로 클램프한다(아래 시험) — 늘 자료가 있다
    # 아주 오래 비면(백 일수 초과) 빈 결과 + 요청일
    w2, ads2, _ = setup(latest=date(2026, 9, 1), today=MON)
    rows2, real2 = ads2[key].holdings(funds_of(key)[0].key, MON)
    assert rows2 == {} and real2 == MON
    pdf_calls = [c for c in w2.calls_of(key) if "pdf" in c[1].lower() or "holdings" in c[1]]
    assert len(pdf_calls) == back + 1


def test_hanaro_future_clamp_uses_detail_page_date() -> None:
    w, ads, _ = setup(latest=TUE, today=date(2026, 10, 8))
    f = funds_of("hanaro")[0]
    rows, real = ads["hanaro"].holdings(f.key, date(2026, 10, 8))  # 미래일 → 서버가 최신으로 대체
    assert real == TUE
    assert {c: h.qty for c, h in rows.items()} == w.expected(f, TUE)
    assert any(p == f"/fund/{f.key}" for _, p, _ in w.calls_of("hanaro"))
    # 같은 펀드를 다시 부르면 상세페이지를 다시 받지 않는다(캐시) — 그리고 선클램프
    n = len(w.calls_of("hanaro"))
    ads["hanaro"].holdings(f.key, date(2026, 10, 8))
    new = w.calls_of("hanaro")[n:]
    assert all(p != f"/fund/{f.key}" for _, p, _ in new)


def test_rise_traces_real_date_and_caches_per_request_day() -> None:
    w, ads, _ = setup(latest=FRI, today=MON)
    a, b = funds_of("rise")[0], funds_of("rise")[1]
    rows, real = ads["rise"].holdings(a.key, MON)
    assert rows and real == FRI  # 월요일 요청 → 금요일 자료(응답에 기준일 없음)
    n = len(w.calls_of("rise"))
    _, real_b = ads["rise"].holdings(b.key, MON)
    assert real_b == FRI and len(w.calls_of("rise")) == n + 1  # 캐시 — 역추적 다시 안 함


def test_rise_future_request_starts_from_today() -> None:
    _, ads, _ = setup(latest=TUE, today=date(2026, 10, 7))
    _, real = ads["rise"].holdings(funds_of("rise")[0].key, date(2026, 12, 1))
    assert real == TUE


# ── 오류·재시도 ──


def test_hard_error_is_raised_with_masked_reason() -> None:
    from kbj.data.private.etf_issuers.base import IssuerError

    w, ads, sleeps = setup(fail={"kodex": 500})
    with pytest.raises(IssuerError) as ei:
        ads["kodex"].universe()
    assert ei.value.status == 500 and "xyz" not in str(ei.value)
    assert len(w.calls_of("kodex")) == 3 and sleeps == [1.5, 3.0]  # 일시 오류 — 3번
    w2, ads2, _ = setup(fail={"sol": 404})
    with pytest.raises(IssuerError):
        ads2["sol"].universe()
    assert len(w2.calls_of("sol")) == 1  # 404 는 다시 부르지 않는다


def test_flaky_then_ok_and_ace_404_is_empty() -> None:
    w, ads, sleeps = setup(flaky={"tiger": 2})
    assert len(ads["tiger"].universe()) == 3 and sleeps == [1.5, 3.0]
    rows, real = ads["ace"].holdings("ZZ999", TUE)  # 미존재 펀드 → 404 → 빈
    assert rows == {} and real == TUE
    assert w.calls_of("ace")


def test_koact_challenge_html_is_retried_with_cooldown() -> None:
    _, ads, sleeps = setup(challenge={"koact": 1})
    assert len(ads["koact"].universe()) == 3
    assert sleeps == [35.0]


def test_size_cap() -> None:
    from kbj.data.private.etf_issuers.base import IssuerError

    w = FakeIssuers(latest=TUE)
    http = IssuerHttp(httpx.Client(transport=w.transport()), max_bytes=100, sleep=lambda s: None)
    with pytest.raises(IssuerError, match="보다 크다"):
        build(http, ["kodex"])[0].holdings(funds_of("kodex")[0].key, TUE)


class _Lim:
    def __init__(self, host: str, cap: float | None, log: list[tuple[str, str | float | None]]):
        self.host, self.log = host, log
        log.append(("new", cap))

    def acquire(self, priority: object, tr_id: str, timeout: float | None = None) -> None:
        self.log.append(("acquire", tr_id))

    def on_rate_limited(self) -> float:
        self.log.append(("slow", self.host))
        return 0.5


def test_limiter_per_host_and_koact_cap() -> None:
    w = FakeIssuers(latest=TUE, fail={"plus": 429})
    log: list[tuple[str, str | float | None]] = []
    make: Callable[[str, float | None], _Lim] = lambda h, c: _Lim(h, c, log)  # noqa: E731
    http = IssuerHttp(httpx.Client(transport=w.transport()), limiter_for=make,  # type: ignore[arg-type]
                      sleep=lambda s: None)  # fmt: skip
    ads = {a.KEY: a for a in build(http, ["koact", "plus"])}
    ads["koact"].universe()
    assert ("new", 0.5) in log  # KoAct 호스트는 0.5/s 상한
    assert ("acquire", "www.samsungactive.co.kr") in log
    with pytest.raises(Exception, match="429"):
        ads["plus"].universe()
    assert log.count(("slow", "www.plusetf.co.kr")) == 3  # 429 마다 감속 신호


# ── 목록·견본 ──


def test_registry_has_nine_and_no_naver() -> None:
    assert KEYS == ("kodex", "tiger", "timefolio", "sol", "ace", "hanaro", "koact", "plus", "rise")
    assert all(a.DEPTH == "full" for a in ADAPTERS)
    assert "naver" not in KEYS
    with pytest.raises(ValueError, match="모르는"):
        build(IssuerHttp(httpx.Client()), ["naver"])


def test_fixture_files_match_generator() -> None:
    for name, text in samples().items():
        assert (FIX / name).read_text(encoding="utf-8") == text, name


@pytest.mark.parametrize("key", KEYS)
def test_parse_fixture_file(key: str) -> None:
    """견본 파일(목록·PDF)을 그대로 돌려주는 전송으로 파싱 — 파일만 봐도 모양을 알 수 있게."""
    pdf = next(FIX.glob(f"{key}_pdf.*")).read_text(encoding="utf-8")
    uni = next(FIX.glob(f"{key}_universe.*")).read_text(encoding="utf-8")

    def handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        listy = any(s in p for s in ("product.do", "list.ajax", "m11_list", "/api/etf/pds",
                                     "/api/funds", "search-list", "etf.do", "find/list",
                                     "listJquery"))  # fmt: skip
        if listy and not any(s in p for s in ("pdf", "holdings")) and p != "/api/etf/pds/pdf":
            body = uni
            if ("product.do" in p or "etf.do" in p) and req.url.params.get("pageNo") != "1":
                body = "[]" if "product.do" in p else '{"etfs": []}'
            if p == "/api/etf/pds" and req.url.params.get("page") != "1":
                body = '{"items": []}'
            return httpx.Response(200, text=body)
        if key == "hanaro" and p.startswith("/fund/"):
            return httpx.Response(200, text="")
        if key == "rise" and "productView" in p:
            return httpx.Response(200, text=pdf if b"2026-10-06" in req.content else "<table/>")
        return httpx.Response(200, text=pdf)

    http = IssuerHttp(httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda s: None)
    [a] = build(http, [key], today=lambda: TUE)
    refs = a.universe()
    assert refs
    rows, _ = a.holdings(refs[0].fund_key, TUE)
    assert rows and all(c[:2] == "99" for c in rows)
