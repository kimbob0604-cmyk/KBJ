"""금투협 종합통계 수집 — `market_stats.kofia`(13:30, D+1) → `pub_market_stats.*`(공개).

근거: docs/p5_design.md §1.2·§3.2, D-P5-20. 상단 띠 신용잔고·예탁금(공개 칩 — `credit.json`)의 원천.
어댑터 `kbj.data.public.fsc_kofia_stats`(15094809 — 공개, 공공누리 1유형).

- 데이터 키 `DATAGO:15094809/<credit|capital|fund|cma>@<전 거래일>` 마다 그날까지 최근
  `LOOKBACK_DAYS` 를 받아 있는 날은 다 넣는다(한 키의 실패가 다른 키를 멈추지 않는다). 포털은
  실제로 2영업일쯤 늦다(실측 2026-10-20, #48) — 그날 행이 없어도 받은 최신 날이
  `LAG_OK_TRADING_DAYS` 거래일 안이면 공표 지연으로 키를 `skipped`(날마다 '공표 전' 실패·알림이
  쌓이던 것 — 늦은 날은 다음 실행의 창이 채운다, docs/ops_error_catalog.md). 더 늦거나 창 안에 행이
  하나도 없으면 공표 전(`not_ready` → 재시도·실패)으로 드러낸다.
- 열 → 항목 이름(`ITEMS` — 0016 `item` 은 소문자 식별자). 응답 열 이름·단위는 [실측 필요 #48]. 값이
  빈 칸은 None + invalid(0 금지). 단위는 '원'으로 적는다 [실측 필요 #48].
- 구분 열이 있는 오퍼레이션(cma — 운용 대상·투자자 구분)은 항목 이름에 구분의 짧은 해시를 붙인다
  (한글 구분을 식별자로 못 쓴다 — 실측 뒤 이름표를 정한다 [실측 필요 #48]).
- 펀드 순자산(fund)은 필수 입력(`ctg`·`tstMthdCtg`)의 값 목록을 모른다 — 실측 전이라 키는 `skipped`.
- 공개 작업(계약 ⑬·⑭): 로그인 원천·저장소를 import 하지 않는다 — 저장소는 `kbj.store.repos.public`.
- 자원: `public_repos`(없으면 `connect`), `kofia`(KofiaStatsClient — 없으면 설정 키로 만든다).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
from typing import Final, Literal

from kbj.core.calendar import TradingCalendar
from kbj.core.p5_rows import MarketStatRow
from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.data.datago import DatagoTransport
from kbj.data.public.fsc_kofia_stats.client import DATASET, KofiaRow, KofiaStatsClient
from kbj.data.spec import DataKey
from kbj.services.collectors.macro import Lagging, NotConfigured, NotReady, client, run_keys
from kbj.services.scheduler.handlers import JobContext, JobResult
from kbj.store.repos.public import PublicRepos, pg_public_repos

__all__ = ["ITEMS", "STATS", "rows_of", "run"]

UNIT: Final = "원"  # [실측 필요 #48]
LOOKBACK_DAYS: Final = 10  # 공표가 늦은 날까지 함께 받는 창(달력일)
# 받은 최신 날이 그날보다 이 거래일 수 안으로 늦으면 공표 지연(실패 아님).
# 실측 2영업일(#48) + 1 [추정]
LAG_OK_TRADING_DAYS: Final = 3
Stat = Literal["credit_balance", "market_capital", "fund_nav", "cma"]
STATS: Final[Mapping[str, Stat]] = {
    "credit": "credit_balance",
    "capital": "market_capital",
    "fund": "fund_nav",
    "cma": "cma",
}
ITEMS: Final[Mapping[str, Mapping[str, str]]] = {
    "credit": {
        "crdTrFingWhl": "loan_total",
        "crdTrFingScrs": "loan_kospi",
        "crdTrFingKosdaq": "loan_kosdaq",
        "crdTrLndrWhl": "short_total",
        "crdTrLndrScrs": "short_kospi",
        "crdTrLndrKosdaq": "short_kosdaq",
        "sbscCapLn": "subscription_loan",
        "dpsgScrtMogFing": "deposit_secured_loan",
    },
    "capital": {
        "invrDpsgAmt": "investor_deposit",
        "onbdDrvPrdTrRcAdvAmt": "derivatives_deposit",
        "toCstRpchCndBndSlgBal": "rp_balance",
        "brkTrdUcolMny": "uncollected",
        "brkTrdUcolMnyVsOppsTrdAmt": "forced_sale",
        "ucolMnyVsOppsTrdRlImpt": "forced_sale_ratio",
    },
    "fund": {"nPptTotAmt": "net_assets"},
    "cma": {"scrtCmpyCnt": "companies", "actCnt": "accounts", "actBal": "balance"},
}


def _label_tag(labels: Mapping[str, str]) -> str:
    if not labels:
        return ""
    raw = "|".join(f"{k}={labels[k]}" for k in sorted(labels))
    return "_g" + hashlib.sha256(raw.encode()).hexdigest()[:8]


def rows_of(
    op: str, rows: list[KofiaRow], day: date, now_quality: Quality = Quality.OK
) -> list[MarketStatRow]:
    """어댑터 행 → 저장 행(그날만). 값 None 은 invalid."""
    stat = STATS[op]
    out: list[MarketStatRow] = []
    as_of = datetime.combine(day, time(15, 30), tzinfo=KST)  # 기준일 장 마감 값 [실측 필요 #48]
    for r in rows:
        if r.bas_dt != day:
            continue
        tag = _label_tag(r.labels)
        for col, name in ITEMS[op].items():
            v = r.values.get(col)
            out.append(
                MarketStatRow(
                    stat,
                    day,
                    f"{name}{tag}",
                    v,
                    UNIT,
                    "DATAGO",
                    as_of,
                    now_quality if v is not None else Quality.INVALID,
                )
            )
    return out


def _client(ctx: JobContext) -> KofiaStatsClient:
    def make() -> KofiaStatsClient:
        if ctx.settings is None or ctx.settings.datago_key is None:
            raise RuntimeError("KBJ_DATAGO_KEY 가 없다(공공데이터포털)")
        t = DatagoTransport.for_datasets(ctx.settings.datago_key, ctx.resource("redis"), [DATASET])
        return KofiaStatsClient(t)

    return client(ctx, "kofia", make, need="datago_key")


def _one(ctx: JobContext, repos: PublicRepos, key: DataKey) -> int:
    op = key.dataset.split("/", 1)[1]
    if op not in STATS:
        raise NotConfigured(f"모르는 오퍼레이션 {op}")
    if op == "fund":
        raise NotConfigured("펀드 순자산 — ctg·tstMthdCtg 값 목록 실측 전(#48)")
    day = date.fromisoformat(key.as_of[:10])
    k = _client(ctx)
    fetch = {"credit": k.credit_balance, "capital": k.market_capital, "cma": k.cma}[op]
    # 실측(2026-10-20): 포털은 2영업일쯤 늦게 연다(10/20 15시에 최신 10/16). 그날 하루만 부르면
    # 늘 '공표 전'으로 끝나 표가 한 번도 차지 않았다 — 최근 창을 받아 있는 날은 다 넣는다
    fetched = fetch(day - timedelta(days=LOOKBACK_DAYS), day)
    days = sorted({r.bas_dt for r in fetched})
    n = 0
    for d in days:
        rows = rows_of(op, [r for r in fetched if r.bas_dt == d], d)
        if rows:
            n += repos.market_stats.upsert(rows, loaded_by=ctx.job, received_at=ctx.now)
    if day not in days:
        if days and _behind(ctx, days[-1], day) <= LAG_OK_TRADING_DAYS:
            raise Lagging(
                f"{day} 행 없음 — 포털 공표 지연(받은 최신 {days[-1]}, "
                f"{LAG_OK_TRADING_DAYS}거래일 안이라 다음 실행이 채운다, #48)"
            )
        last = days[-1].isoformat() if days else "없음"
        raise NotReady(f"{day} 행 없음(포털 공표 지연 — 받은 최신 {last}, #48)")
    return n


def _behind(ctx: JobContext, last: date, day: date) -> int:
    """`last` 뒤 `day` 까지의 거래일 수(받은 최신 날이 그날보다 몇 거래일 늦은가)."""
    cal: TradingCalendar = ctx.resources.get("kr") or TradingCalendar.default()
    n, d = 0, last + timedelta(days=1)
    while d <= day:
        n += cal.is_trading_day(d)
        d += timedelta(days=1)
    return n


def run(ctx: JobContext) -> JobResult:
    repos: PublicRepos = ctx.resources.get("public_repos") or pg_public_repos(
        ctx.resource("connect")
    )
    handlers = {f"DATAGO:{DATASET}/{op}": (lambda k: _one(ctx, repos, k)) for op in STATS}
    return run_keys(ctx, handlers)
