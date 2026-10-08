"""가짜 ETF 운용사 9곳 — 목록·구성종목(PDF) 응답을 운용사별 모양으로(`httpx.MockTransport`). 합성만.

운용사 실응답은 로그인 등급이라 레포에 넣지 않는다(CLAUDE.md §2). 응답 모양은 ET
`etf_tracker_v9/collectors.py`·`adapters/*.py` 머리말의 실측 기록을 따라 손으로 만든 합성이다
(키 이름·HTML 구조만 같고 값·이름·코드는 고정 시드로 지어낸 것 — 실제 종목코드와 겹치지 않게
`99xxx0`·
`97xx00` 대역).

세계(`FakeIssuers`)
- 운용사마다 펀드 3개: `kr`(국내 테마 — '합성반도체'), `idx`(국내 대표지수 — '합성200'),
  `us`(해외 — '합성미국테크', 국내 종목 0개라 PDF 를 거르면 빈다).
- 공시일 = 평일(주말·`holidays` 제외) 중 `latest` 이하. 기준일 t 의 구성: 펀드마다 고정 12종목 +
  CU 표류(매일 수량 +0.2%) + 날마다 바뀌는 것 — 12종목 중 하나 빠짐(t mod 12), 바깥 종목 하나
  들어옴, 홀수 날 한 종목 수량 ×1.1. 그래서 이어진 두 공시일 사이에 NEW·DROP·ADD/CUT 가 생긴다.
- 거를 것도 섞는다: 해외 티커('AAPL US')·원화예금(KRD…)·선물(A01690)·채권 분류코드(03502G)·
  ETF 를 담은 행(995010 — 분석에서 빠져야 한다). ISIN 으로 주는 운용사(HANARO·RISE·PLUS 일부)는
  우선주 ISIN(KR7990011003 → 990015)도 하나.
- 날짜 처리는 운용사마다 실측 기록대로: KODEX·KoAct·TIMEFOLIO·PLUS·RISE 는 요청일 이하 최신으로
  폴백(RISE 는 기준일을 알려주지 않는다), TIGER·ACE·HANARO 는 그날만(없으면 빈), HANARO 는
  미래일 요청을 최신으로 클램프, SOL 은 날짜를 무시하고 최신.

장애 심기: `fail[운용사] = 상태`(그 운용사 모든 요청), `flaky[운용사] = n`(처음 n 번 503),
`challenge[운용사] = n`(처음 n 번 200 + HTML — Cloudflare 챌린지 흉내). 기록: `calls`.
"""

from __future__ import annotations

import json
import random
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Final, Literal
from urllib.parse import parse_qs

import httpx

SEED: Final = 20261007
STOCKS: Final[tuple[str, ...]] = tuple(f"99{i:03d}0" for i in range(1, 41))
PREF_ISIN: Final = "KR7990011003"  # 우선주 차수 1 → 단축코드 990015
PREF_CODE: Final = "990015"
ETF_IN_FUND: Final = "995010"  # 펀드가 담은 ETF(분석에서 빠진다)
FundKind = Literal["kr", "idx", "us"]
KINDS: Final[tuple[FundKind, ...]] = ("kr", "idx", "us")
ISSUERS: Final[tuple[str, ...]] = (
    "kodex", "tiger", "timefolio", "sol", "ace", "hanaro", "koact", "plus", "rise",
)  # fmt: skip
BRAND: Final[dict[str, str]] = {
    "kodex": "KODEX", "tiger": "TIGER", "timefolio": "TIME", "sol": "SOL", "ace": "ACE",
    "hanaro": "HANARO", "koact": "KoAct", "plus": "PLUS", "rise": "RISE",
}  # fmt: skip
HOSTS: Final[dict[str, str]] = {
    "www.samsungfund.com": "kodex",
    "investments.miraeasset.com": "tiger",
    "timeetf.co.kr": "timefolio",
    "www.soletf.com": "sol",
    "papi.aceetf.co.kr": "ace",
    "www.hanaroetf.com": "hanaro",
    "www.samsungactive.co.kr": "koact",
    "www.plusetf.co.kr": "plus",
    "www.riseetf.co.kr": "rise",
}
# 거를 행 모양: short = 6자리 코드 운용사(해외 티커·원화예금), ace = + 선물·채권 분류코드(ACE 가
# `_STOCK` 으로 거른다), isin = ISIN 으로 주는 운용사(+ 선물·채권 ISIN, 우선주 ISIN)
NOISE: Final[dict[str, Literal["short", "ace", "isin"]]] = {
    "kodex": "short", "tiger": "short", "timefolio": "short", "sol": "short", "ace": "ace",
    "hanaro": "isin", "koact": "short", "plus": "isin", "rise": "isin",
}  # fmt: skip
SUFFIX: Final[dict[FundKind, str]] = {"kr": "합성반도체", "idx": "합성200", "us": "합성미국테크"}


@dataclass(frozen=True)
class FakeFund:
    issuer: str
    kind: FundKind
    key: str  # 운용사 고유 키(형식은 운용사마다)
    ticker: str
    name: str

    @property
    def isin(self) -> str:
        return f"KR7{self.ticker}00{ISSUERS.index(self.issuer)}"

    @property
    def fund_id(self) -> str:
        return f"{self.issuer}:{self.key}"


@dataclass(frozen=True)
class Line:
    """PDF 한 줄(가공 전) — code 는 운용사가 주는 그대로(단축코드·ISIN·해외 티커)."""

    code: str
    isin: str
    name: str
    qty: float
    val: float
    wt: float


def _key(issuer: str, i: int, j: int) -> str:
    if issuer == "hanaro":
        return f"{0xA0000000000000 + i * 16 + j:016X}"
    if issuer == "timefolio":
        return str(100 + j)
    if issuer == "tiger":  # TIGER 는 ISIN 이 키(KR7 + 티커 + 체크 3자리)
        return f"KR797{i}{j}0000{i}"
    if issuer == "rise":
        return f"R{i}{j}0"
    return f"{issuer[:2].upper()}{j:03d}"


def funds_of(issuer: str) -> list[FakeFund]:
    i = ISSUERS.index(issuer)
    return [
        FakeFund(issuer, k, _key(issuer, i, j), f"97{i}{j}00", f"{BRAND[issuer]} {SUFFIX[k]}")
        for j, k in enumerate(KINDS)
    ]


ALL_FUNDS: Final[tuple[FakeFund, ...]] = tuple(f for iss in ISSUERS for f in funds_of(iss))


def _isin(code: str) -> str:
    return f"KR7{code}003"


def _price(code: str) -> float:
    digits = "".join(ch for ch in code if ch.isdigit()) or "0"
    return float(10_000 + (int(digits[-4:]) * 137) % 90_000)


@dataclass
class FakeIssuers:
    latest: date
    holidays: frozenset[date] = frozenset()
    seed: int = SEED
    fail: dict[str, int] = field(default_factory=dict[str, int])
    fail_paths: dict[str, int] = field(default_factory=dict[str, int])  # 경로 조각 → 상태
    empty_funds: set[str] = field(default_factory=set[str])  # fund_id — 국내 종목 없는 PDF
    flaky: dict[str, int] = field(default_factory=dict[str, int])
    challenge: dict[str, int] = field(default_factory=dict[str, int])
    calls: list[tuple[str, str, str, dict[str, str]]] = field(
        default_factory=list[tuple[str, str, str, dict[str, str]]]
    )
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # ── 세계 ──
    def published(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self.holidays and d <= self.latest

    def on_or_before(self, d: date) -> date | None:
        d = min(d, self.latest)
        for _ in range(40):
            if self.published(d):
                return d
            d -= timedelta(days=1)
        return None

    def lines(self, fund: FakeFund, d: date) -> list[Line]:
        """그날 PDF(가공 전 — 거를 행 포함). 거를 행의 모양은 운용사 종류마다(`NOISE`)."""
        noise = NOISE[fund.issuer]
        if fund.kind == "us" or fund.fund_id in self.empty_funds:
            return [
                Line("AAPL US", "US0378331005", "합성애플", 100.0, 1.0e7, 50.0),
                Line("MSFT US", "US5949181045", "합성마이크로", 80.0, 1.0e7, 49.0),
                Line("KRD010010001", "KRD010010001", "원화예금", 0.0, 1.0e5, 1.0),
            ]
        rng = random.Random(f"{self.seed}:{fund.fund_id}")  # noqa: S311 — 합성
        base = rng.sample(STOCKS, 12)
        base_qty = [float(rng.randrange(100, 5000)) for _ in base]
        rest = [s for s in STOCKS if s not in base]
        t = d.toordinal()
        out: list[Line] = []
        drift = 1.0 + 0.002 * (t % 50)  # CU 표류 — 모든 종목 같은 비율
        for k, code in enumerate(base):
            if k == t % 12:
                continue  # 오늘 빠진 종목
            qty = base_qty[k] * drift
            if k == (t + 5) % 12 and t % 2:
                qty *= 1.1
            out.append(Line(code, _isin(code), f"합성종목{code[2:5]}", round(qty), 0.0, 0.0))
        extra = rest[t % len(rest)]
        out.append(Line(extra, _isin(extra), f"합성종목{extra[2:5]}", 1000.0, 0.0, 0.0))
        if fund.kind == "kr":
            out.append(Line(ETF_IN_FUND, _isin(ETF_IN_FUND), "합성ETF", 500.0, 0.0, 0.0))
        if noise == "isin":
            out.append(Line(PREF_ISIN, PREF_ISIN, "합성종목001우", 300.0, 0.0, 0.0))
        total = sum(ln.qty * _price(ln.code) for ln in out) or 1.0
        out = [
            Line(ln.code, ln.isin, ln.name, ln.qty, ln.qty * _price(ln.code),
                 round(ln.qty * _price(ln.code) / total * 100, 4))
            for ln in out
        ]  # fmt: skip
        out += [
            Line("AAPL US", "US0378331005", "합성애플", 10.0, 1.0e6, 0.5),
            Line("KRD010010001", "KRD010010001", "원화예금", 0.0, 1.0e6, 0.3),
        ]
        if noise in ("ace", "isin"):
            out += [
                Line("A01690", "KR4A01690002", "합성지수선물", 1.0, 1.0e6, 0.2),
                Line("03502G", "KR103502GF30", "합성국고채", 0.0, 1.0e6, 0.1),
            ]
        return out

    def expected(self, fund: FakeFund, d: date) -> dict[str, float]:
        """어댑터가 남겨야 할 {코드: 수량}(국내 주식·ETF 단축코드만 — 거를 행 제외)."""
        out: dict[str, float] = {}
        for ln in self.lines(fund, d):
            code = PREF_CODE if ln.code == PREF_ISIN else ln.code
            if code.startswith("99") and len(code) == 6:
                out[code] = ln.qty
        return out

    def fund(self, issuer: str, key: str) -> FakeFund | None:
        return next((f for f in funds_of(issuer) if f.key == key), None)

    # ── HTTP ──
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        issuer = HOSTS.get(request.url.host)
        params = {k: v for k, v in request.url.params.items()}
        if request.method == "POST" and request.content:
            ctype = request.headers.get("content-type", "")
            if "json" in ctype:
                params.update({k: str(v) for k, v in json.loads(request.content).items()})
            else:
                form = parse_qs(request.content.decode())
                params.update({k: v[0] for k, v in form.items()})
        with self._lock:
            self.calls.append((issuer or "?", request.method, request.url.path, params))
            if issuer is None:
                return httpx.Response(404, text="unknown host")
            if issuer in self.fail:
                return httpx.Response(self.fail[issuer], text=f"{issuer} 합성 오류 serviceKey=xyz")
            for frag, status in self.fail_paths.items():
                if frag in request.url.path:
                    return httpx.Response(status, text="합성 경로 오류")
            if self.flaky.get(issuer, 0) > 0:
                self.flaky[issuer] -= 1
                return httpx.Response(503, text="잠시 후 다시")
            if self.challenge.get(issuer, 0) > 0:
                self.challenge[issuer] -= 1
                return httpx.Response(200, text="<html>Just a moment...</html>")
        route: Callable[[str, dict[str, str]], httpx.Response] = getattr(self, f"_{issuer}")
        return route(request.url.path, params)

    def calls_of(self, issuer: str) -> list[tuple[str, str, dict[str, str]]]:
        return [(m, p, q) for i, m, p, q in self.calls if i == issuer]

    # ── 운용사별 ──
    def _kodex_like(self, issuer: str, path: str, p: dict[str, str], list_path: str,
                    pdf_prefix: str, list_key: str | None) -> httpx.Response:  # fmt: skip
        if path == list_path:
            page = int(p.get("pageNo", "1"))
            items = [{"fId": f.key, "stkTicker": f.ticker, "fNm": f.name}
                     for f in funds_of(issuer)] if page == 1 else []  # fmt: skip
            return httpx.Response(200, json=items if list_key is None else {list_key: items})
        if path.startswith(pdf_prefix):
            fund = self.fund(issuer, path[len(pdf_prefix) :].removesuffix(".do"))
            if fund is None:
                return httpx.Response(404, json={"message": "없는 펀드"})
            want = date.fromisoformat(p["gijunYMD"].replace(".", "-"))
            real = self.on_or_before(want)
            if real is None:
                return httpx.Response(200, json={"pdf": {"gijunYMD": None, "list": []}})
            rows = [{"itmNo": ln.code, "secNm": ln.name, "ratio": f"{ln.wt}",
                     "applyQ": f"{ln.qty:,.0f}", "evalA": f"{ln.val:,.0f}"}
                    for ln in self.lines(fund, real)]  # fmt: skip
            return httpx.Response(200, json={"pdf": {"gijunYMD": real.strftime("%Y.%m.%d"),
                                                      "list": rows}})  # fmt: skip
        return httpx.Response(404, text="not found")

    def _kodex(self, path: str, p: dict[str, str]) -> httpx.Response:
        return self._kodex_like("kodex", path, p, "/api/v1/kodex/product.do",
                                "/api/v1/kodex/product-pdf/", None)  # fmt: skip

    def _koact(self, path: str, p: dict[str, str]) -> httpx.Response:
        return self._kodex_like("koact", path, p, "/api/v1/product/etf.do",
                                "/api/v1/product/etf-pdf/", "etfs")  # fmt: skip

    @staticmethod
    def _table(rows: list[list[str]], *, head: bool = True) -> str:
        h = "<tr><th>코드</th><th>종목명</th><th>수량</th><th>평가금액</th><th>비중</th></tr>"
        body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
        return f"<table><thead>{h if head else ''}</thead><tbody>{body}</tbody></table>"

    def _tiger(self, path: str, p: dict[str, str]) -> httpx.Response:
        if path.endswith("/search/list.ajax"):
            links = "".join(f'<a href="detail/index.do?ksdFund={f.isin}">상품</a>'
                            f'<a href="x?ksdFund={f.isin}">다시</a>'
                            for f in funds_of("tiger"))  # fmt: skip
            return httpx.Response(200, text=f"<div>{links}</div>")
        if path.endswith("/pdfListAjax.ajax"):
            fund = next((f for f in funds_of("tiger") if f.isin == p.get("ksdFund")), None)
            d = date.fromisoformat(p["fixDate"].replace(".", "-"))
            if fund is None or not self.published(d):
                return httpx.Response(200, text=self._table([]))
            rows = [[ln.code, ln.name, f"{ln.qty:,.0f}", f"{ln.val:,.0f}", f"{ln.wt}"]
                    for ln in self.lines(fund, d)]  # fmt: skip
            return httpx.Response(200, text=self._table(rows))
        return httpx.Response(404, text="not found")

    def _timefolio(self, path: str, p: dict[str, str]) -> httpx.Response:
        if path == "/m11_list.php":
            fs = [f for f in funds_of("timefolio") if (f.kind == "us") == (p["cate"] == "001")]
            items = "".join(
                f'<li><a href="m11_view.php?idx={f.key}&cate={p["cate"]}"><div class="name">'
                f'{f.name.replace("&", "&amp;")}</div><div class="codeNum"><span> {f.ticker} '
                "</span></div></a></li>"
                for f in fs
            )
            return httpx.Response(200, text=f"<ul>{items}</ul>")
        if path == "/m11_view.php":
            fund = self.fund("timefolio", p.get("idx", ""))
            real = self.on_or_before(date.fromisoformat(p["pdfDate"]))
            if fund is None or real is None:
                return httpx.Response(200, text="<html>자료 없음</html>")
            rows = [[ln.code, ln.name, f"{ln.qty:,.0f}", f"{ln.val:,.0f}", f"{ln.wt}"]
                    for ln in self.lines(fund, real)]  # fmt: skip
            html = (f'<input type="text" id="pdfDate" value="{real.isoformat()}">'
                    f'<div id="constituentItems">{self._table(rows)}</div>')  # fmt: skip
            return httpx.Response(200, text=html)
        return httpx.Response(404, text="not found")

    def _sol(self, path: str, p: dict[str, str]) -> httpx.Response:
        if path == "/api/etf/pds":
            items = ([{"FUND_CD": f.key, "ETF_CD6": f.ticker, "ETF_NAME": f.name}
                      for f in funds_of("sol")] if p.get("page") == "1" else [])  # fmt: skip
            return httpx.Response(200, json={"items": items})
        if path.startswith("/api/etf/pds/pdf/"):
            fund = self.fund("sol", path.rsplit("/", 1)[1])
            real = self.on_or_before(self.latest)
            if fund is None or real is None:
                return httpx.Response(200, json={"items": []})
            items = [{"STOCK_CODE": ln.code, "SEC_NM": ln.name, "QTY": ln.qty, "PRICE": ln.val,
                      "WT_DISP": f"{ln.wt}%"} for ln in self.lines(fund, real)]  # fmt: skip
            return httpx.Response(200, json={"workDt": real.strftime("%Y%m%d"), "items": items})
        return httpx.Response(404, text="not found")

    def _ace(self, path: str, p: dict[str, str]) -> httpx.Response:
        if path == "/api/funds":
            data = [{"fundCd": f.key, "stockCd": f.isin, "fundNm": f.name}
                    for f in funds_of("ace")]  # fmt: skip
            return httpx.Response(200, json={"data": data, "page": {"totalElements": 99}})
        if path.startswith("/api/funds/") and path.endswith("/pdf"):
            fund = self.fund("ace", path.split("/")[3])
            if fund is None:
                return httpx.Response(404, json={"message": "없음"})
            d = date.fromisoformat(f"{p['std_dt'][:4]}-{p['std_dt'][4:6]}-{p['std_dt'][6:]}")
            if not self.published(d):
                return httpx.Response(200, json={"pdfList": [], "std_DT": None})
            rows = [{"jm_KSC_CD": ln.code, "sec_NM": ln.name, "wg": ln.wt,
                     "cu_ITEM_CNT": f"{ln.qty:.0f}", "val_AM": ln.val, "std_DT": d.isoformat()}
                    for ln in self.lines(fund, d)]  # fmt: skip
            if fund.kind == "kr":  # 숫자 6자리로 오는 채권이 두 줄(같은 분류코드 — 합산 대상)
                for v in (1.0e6, 2.0e6):
                    rows.append(
                        {
                            "jm_KSC_CD": "990990",
                            "sec_NM": "합성회사채",
                            "wg": 0.5,
                            "cu_ITEM_CNT": "0",
                            "val_AM": v,
                            "std_DT": d.isoformat(),
                        }
                    )
            return httpx.Response(200, json={"pdfList": rows, "std_DT": d.isoformat()})
        return httpx.Response(404, text="not found")

    def _hanaro_frag(self, fund: FakeFund, d: date) -> str:
        rows = "".join(
            f"<tr><td>{n}</td><td>{ln.isin}</td><th>{ln.name.replace('&', '&amp;')}</th>"
            f"<td>{ln.qty:,.0f}</td><td>{ln.val:,.0f}</td><td>{ln.wt}%</td></tr>"
            for n, ln in enumerate(self.lines(fund, d), 1)
        )
        return f"<table><tbody>\n{rows}\n</tbody></table>"

    def _hanaro(self, path: str, p: dict[str, str]) -> httpx.Response:
        if path == "/api/v1/fund/get-fund-search-list":
            if p.get("pageNo") != "1":
                return httpx.Response(200, text='<ul></ul><input value="3">')
            items = "".join(
                f'<li data-target="fund" data-fund-code="EPM{n}"><a href="/fund/{f.key}">'
                f"<h5>{f.name}</h5></a><dl><dt>종목코드</dt><dd>{f.ticker}</dd></dl></li>"
                for n, f in enumerate(funds_of("hanaro"))
            )
            return httpx.Response(200, text=f"<ul>{items}</ul>")
        if path.startswith("/fund/") and path.count("/") == 2:
            fund = self.fund("hanaro", path.split("/")[2])
            real = self.on_or_before(self.latest)
            if fund is None or real is None:
                return httpx.Response(404, text="없음")
            pad = "x" * 2000
            return httpx.Response(
                200, text=f'<html>{pad}<input type="text" id="pdfDate" class="d" '
                          f'value="{real.strftime("%Y.%m.%d")}">{pad}</html>'
            )  # fmt: skip
        if path.endswith("/get-fund-holdings-list"):
            fund = self.fund("hanaro", path.split("/")[4])
            d = date.fromisoformat(p["baseDate"].replace(".", "-"))
            if fund is None:
                return httpx.Response(200, text="<table><tbody></tbody></table>")
            if d > self.latest:  # 미래일 요청 → 최신 기준일 내용으로 조용히 클램프(주의 2)
                real = self.on_or_before(self.latest)
                if real is None:
                    return httpx.Response(200, text="<table><tbody></tbody></table>")
                return httpx.Response(200, text=self._hanaro_frag(fund, real))
            if not self.published(d):
                return httpx.Response(200, text="<table><tbody></tbody></table>")
            return httpx.Response(200, text=self._hanaro_frag(fund, d))
        return httpx.Response(404, text="not found")

    def _plus(self, path: str, p: dict[str, str]) -> httpx.Response:
        if path == "/api/v1/product/find/list":
            page = int(p.get("page", "0"))
            items = ([{"id": f.key, "nameCode": f.ticker, "displayName": f.name}
                      for f in funds_of("plus")] if page == 0 else [])  # fmt: skip
            return httpx.Response(200, json={"content": items, "last": True})
        if path == "/api/v1/product/pdf/list":
            fund = self.fund("plus", p.get("n", ""))
            d = p["d"]
            real = self.on_or_before(date(int(d[:4]), int(d[4:6]), int(d[6:])))
            if fund is None or real is None:
                return httpx.Response(200, json={"content": [], "totalPages": 0})
            rows: list[dict[str, Any]] = []
            for ln in self.lines(fund, real):
                # 거를 행은 jmCd 없이 ISIN 만, 우선주는 jmCd 자리에 ISIN(실측 주의 3)
                jm: str | None = ln.code if ln.code.startswith("99") else None
                if ln.code == PREF_ISIN:
                    jm = PREF_ISIN
                rows.append({"wkdate": real.strftime("%Y%m%d"), "jmCd": jm, "krJmCd": ln.isin,
                             "jmNm": ln.name, "amount": ln.qty, "ratio": ln.wt})  # fmt: skip
            return httpx.Response(200, json={"content": rows, "totalPages": 1})
        return httpx.Response(404, text="not found")

    def _rise(self, path: str, p: dict[str, str]) -> httpx.Response:
        if path == "/prod/finder/listJquery":
            items = "".join(
                f'<li><p class="tit"><a href="/prod/finderDetail/{f.key}">{f.name}</a></p>\n'
                f'<span class="code">({f.ticker})</span></li>'
                for f in funds_of("rise")
            )
            return httpx.Response(200, text=f'<div>전체 <span class="n">3</span> 건</div>{items}')
        if path == "/prod/finder/productViewSearchTabJquery3":
            fund = self.fund("rise", p.get("fundCd", ""))
            real = self.on_or_before(date.fromisoformat(p["searchDate"]))
            if fund is None or real is None:
                return httpx.Response(200, text="<table></table>")
            rows = "".join(
                f"<tr><td>{n}</td><td>{ln.name}</td><td>{ln.isin}</td><td>{ln.qty:,.0f}</td>"
                f"<td>{ln.wt}</td><td>{ln.val:,.0f}</td></tr>"
                for n, ln in enumerate(self.lines(fund, real), 1)
            )
            return httpx.Response(200, text=f"<table><tbody>{rows}</tbody></table>")
        return httpx.Response(404, text="not found")


# ── 견본(tests/fixtures/synthetic/etf_issuers) ───────────────────────────────────────────

SAMPLE_DAY: Final = date(2026, 10, 6)  # 화요일


def samples() -> dict[str, str]:
    """운용사마다 목록·PDF 응답 견본 하나씩(파일 이름 → 본문). 고정 시드라 늘 같다."""
    w = FakeIssuers(latest=SAMPLE_DAY)
    kd = "https://www.samsungfund.com/api/v1/kodex"
    tg = "https://investments.miraeasset.com/tigeretf/ko/product/search"
    tf = "https://timeetf.co.kr"
    so = "https://www.soletf.com/api/etf/pds"
    ac = "https://papi.aceetf.co.kr/api/funds"
    hn = "https://www.hanaroetf.com/api/v1/fund"
    ka = "https://www.samsungactive.co.kr/api/v1/product"
    pl = "https://www.plusetf.co.kr/api/v1/product"
    rs = "https://www.riseetf.co.kr/prod/finder"
    tiger_isin = funds_of("tiger")[0].isin
    hanaro_uid = funds_of("hanaro")[0].key
    rise_key = funds_of("rise")[0].key
    req: dict[str, tuple[str, str, dict[str, Any] | None]] = {
        "kodex_universe.json": ("GET", f"{kd}/product.do?pageNo=1", None),
        "kodex_pdf.json": ("GET", f"{kd}/product-pdf/KO000.do?gijunYMD=2026.10.06", None),
        "tiger_universe.html": ("GET", f"{tg}/list.ajax", None),
        "tiger_pdf.html": (
            "GET", f"{tg}/detail/pdfListAjax.ajax?ksdFund={tiger_isin}&fixDate=2026.10.06", None),
        "timefolio_universe.html": ("GET", f"{tf}/m11_list.php?cate=002", None),
        "timefolio_pdf.html": ("GET", f"{tf}/m11_view.php?idx=100&pdfDate=2026-10-06", None),
        "sol_universe.json": ("GET", f"{so}?page=1", None),
        "sol_pdf.json": ("GET", f"{so}/pdf/SO000", None),
        "ace_universe.json": ("GET", f"{ac}?page=1&size=1000", None),
        "ace_pdf.json": ("GET", f"{ac}/AC000/pdf?page=1&size=5000&std_dt=20261006", None),
        "hanaro_universe.html": ("GET", f"{hn}/get-fund-search-list?pageNo=1", None),
        "hanaro_pdf.html": (
            "GET", f"{hn}/{hanaro_uid}/get-fund-holdings-list?baseDate=2026.10.06", None),
        "koact_universe.json": ("GET", f"{ka}/etf.do?pageNo=1", None),
        "koact_pdf.json": ("GET", f"{ka}/etf-pdf/KO000.do?gijunYMD=2026.10.06", None),
        "plus_universe.json": ("POST", f"{pl}/find/list", {"page": 0}),
        "plus_pdf.json": ("GET", f"{pl}/pdf/list?n=PL000&d=20261006&page=0&pageSize=1000", None),
        "rise_universe.html": ("POST", f"{rs}/listJquery", None),
        "rise_pdf.html": (
            "POST", f"{rs}/productViewSearchTabJquery3",
            {"searchDate": "2026-10-06", "fundCd": rise_key}),
    }  # fmt: skip
    out: dict[str, str] = {}
    for name, (method, url, body) in req.items():
        if method == "POST" and name.startswith("plus"):
            r = httpx.Request(method, url, json=body)
        elif method == "POST":
            r = httpx.Request(method, url, data=body or {"page": "50"})
        else:
            r = httpx.Request(method, url)
        resp = w.handle(r)
        text = resp.text
        if name.endswith(".json"):
            text = json.dumps(json.loads(text), ensure_ascii=False, indent=1, sort_keys=True)
        out[name] = text + "\n"
    return out
