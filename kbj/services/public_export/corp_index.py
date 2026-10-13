"""`corp_index.json` — 공개판 종목 검색·이름표(상장사 `[코드, 이름, 시장, 결산월, 재무 파일, 리포트
파일]`).

docs/p4_design.md §7(`corp_index.json` — `corp_code`·`corp_profile`, 약 0.2 MB), §6.1(공개판 검색은
`web/src/ui/search.ts:matchItems` 가 이 목록을 쓴다).

- 원천: `pub_filings.corp_code`(DART corpCode — 매일 갈아 넣는다, `filings.corp_code`)의
  상장사(종목코드 있는 행) + `pub_filings.corp_profile`(기업개황 — 주 1회)의 결산월·법인 구분.
  기업개황이 아직 없으면 법인 구분은 그 회사의 가장 최근 공시 행
  값(`pub_filings.disclosure.corp_cls`), 결산월은 null(모름 — 12월로 보지 않는다, docs/metrics.md §9
  '회계 기간').
- 시장 이름은 DART 법인 구분에서: Y 유가증권 → KOSPI, K 코스닥 → KOSDAQ, N 코넥스 → KONEX, 그 밖
  null.
- 같은 종목코드가 두 회사에 붙어 있으면(원천 이상) 그 코드는 목록·재무 파일에서 **뺀다**(어느
  회사인지 지어내지 않는다) — notes 에 개수.
- `fin`·`report` 칸 = 이번 내보내기에 `fin/<코드>.json`·`reports/<코드>.json` 을 만들었는가(프런트가
  없는 파일을 요청하지 않게).
- 관심종목 표식은 없다(공개 파일에 넣지 않는다 — §7).
- as_of = corp_code 표를 받은 가장 늦은 시각. 7일 넘게 묵었으면 stale(수집이 멈춤).
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

from kbj.services.public_export.manifest import PublicFile, Quality, worst_quality
from kbj.services.public_export.pub_read import DART, Query, Reader, Row, won

__all__ = [
    "COLUMNS",
    "MARKET_OF",
    "NAME",
    "Q_CORPS",
    "STALE_AFTER",
    "Corp",
    "Corps",
    "build_corp_index",
    "load_corps",
    "market_of",
]

NAME: Final = "corp_index.json"
COLUMNS: Final = ("code", "name", "market", "acc_mt", "fin", "report")
MARKET_OF: Final[Mapping[str, str]] = {"Y": "KOSPI", "K": "KOSDAQ", "N": "KONEX"}
STALE_AFTER: Final = timedelta(days=7)

Q_CORPS: Final = Query(
    "corps",
    "SELECT c.stock_code, c.corp_code, c.corp_name, c.received_at, c.quality,"
    " p.acc_mt, p.induty_code, p.corp_cls AS profile_cls,"
    " (SELECT d.corp_cls FROM pub_filings.disclosure d"
    "   WHERE d.corp_code = c.corp_code AND d.corp_cls IS NOT NULL"
    "   ORDER BY d.rcept_dt DESC, d.rcept_no DESC LIMIT 1) AS disclosure_cls"
    " FROM pub_filings.corp_code c"
    " LEFT JOIN pub_filings.corp_profile p ON p.corp_code = c.corp_code"
    " WHERE c.stock_code IS NOT NULL"
    " ORDER BY c.stock_code, c.corp_code",
)


def market_of(corp_cls: str | None) -> str | None:
    return MARKET_OF.get(corp_cls) if corp_cls is not None else None


@dataclass(frozen=True)
class Corp:
    """상장사 한 곳(공개 칸만)."""

    corp_code: str
    stock_code: str
    name: str
    corp_cls: str | None
    acc_mt: int | None
    induty_code: str | None
    quality: Quality

    @property
    def market(self) -> str | None:
        return market_of(self.corp_cls)


@dataclass(frozen=True)
class Corps:
    """상장사 표 — 종목코드 → 회사, corp_code → 회사. 겹친 종목코드는 빼고 개수만."""

    by_stock: Mapping[str, Corp]
    by_corp: Mapping[str, Corp]
    duplicates: int
    received_at: datetime | None

    def __bool__(self) -> bool:
        return bool(self.by_stock)

    def stock_of(self, corp_code: str) -> Corp | None:
        return self.by_corp.get(corp_code)


def corps_from_rows(rows: Sequence[Row]) -> Corps:
    seen: dict[str, list[Corp]] = {}
    received: list[datetime] = []
    for r in rows:
        acc = won(r.get("acc_mt"))
        corp = Corp(
            corp_code=str(r["corp_code"]),
            stock_code=str(r["stock_code"]),
            name=str(r["corp_name"]),
            corp_cls=r.get("profile_cls") or r.get("disclosure_cls"),
            acc_mt=acc,
            induty_code=r.get("induty_code"),
            quality=worst_quality([str(r.get("quality") or "ok")]),
        )
        seen.setdefault(corp.stock_code, []).append(corp)
        if isinstance(r.get("received_at"), datetime):
            received.append(r["received_at"])
    by_stock = {code: cs[0] for code, cs in seen.items() if len(cs) == 1}
    dup = sum(1 for cs in seen.values() if len(cs) > 1)
    by_corp = {c.corp_code: c for c in by_stock.values()}
    return Corps(by_stock, by_corp, dup, max(received) if received else None)


def load_corps(read: Reader) -> Corps:
    return corps_from_rows(read(Q_CORPS, {}))


def build_corp_index(
    corps: Corps,
    now: datetime,
    *,
    fin_codes: Collection[str] = (),
    report_codes: Collection[str] = (),
) -> PublicFile:
    """상장사 목록 파일. `corps` 가 비면 부르지 않는다(호출자가 만들지 않는 이유를 남긴다)."""
    if not corps:
        raise ValueError("상장사 표가 비었다 — corp_index 를 만들지 않는다")
    fin = set(fin_codes)
    rep = set(report_codes)
    rows: list[list[Any]] = [
        [c.stock_code, c.name, c.market, c.acc_mt, c.stock_code in fin, c.stock_code in rep]
        for c in sorted(corps.by_stock.values(), key=lambda x: x.stock_code)
    ]
    as_of = corps.received_at or now
    quality = worst_quality(c.quality for c in corps.by_stock.values())
    notes = [
        "DART 고유번호 목록의 상장사(종목코드 있는 회사) — 시장은 DART 법인 구분(Y·K·N)",
        "결산월 null = 기업개황 아직 없음(12월로 보지 않는다)",
    ]
    if corps.duplicates:
        notes.append(f"종목코드가 두 회사에 겹친 {corps.duplicates}건은 뺐다(원천 확인 필요)")
    if now - as_of > STALE_AFTER:
        quality = "stale"
        notes.append("고유번호 목록이 7일 넘게 갱신되지 않았다")
    data: dict[str, Any] = {"columns": list(COLUMNS), "n": len(rows), "rows": rows}
    return PublicFile(
        name=NAME,
        source=DART,
        as_of=as_of,
        quality=quality,
        data=data,
        notes=tuple(notes),
        compact=True,
    )
