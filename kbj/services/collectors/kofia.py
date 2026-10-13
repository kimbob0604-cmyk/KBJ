"""금투협 종합통계 수집 — `market_stats.kofia`(13:30, D+1) → `pub_market_stats.*`(공개).

근거: docs/p5_design.md §1.2·§3.2, D-P5-20. 상단 띠 신용잔고·예탁금(공개 칩 — `credit.json`)의 원천.
어댑터 `kbj.data.public.fsc_kofia_stats`(15094809 — 공개, 공공누리 1유형).

- 데이터 키 `DATAGO:15094809/<credit|capital|fund|cma>@<전 거래일>` 마다 그날 하루를 받는다(한 키의
  실패가 다른 키를 멈추지 않는다). 그날 행이 없으면 공표 전(`not_ready` — D+1 13시 개방 [실측 필요
  #48]).
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
from datetime import date, datetime, time
from typing import Final, Literal

from kbj.core.p5_rows import MarketStatRow
from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.data.datago import DatagoTransport
from kbj.data.public.fsc_kofia_stats.client import DATASET, KofiaRow, KofiaStatsClient
from kbj.data.spec import DataKey
from kbj.services.collectors.macro import NotConfigured, NotReady, client, run_keys
from kbj.services.scheduler.handlers import JobContext, JobResult
from kbj.store.repos.public import PublicRepos, pg_public_repos

__all__ = ["ITEMS", "STATS", "rows_of", "run"]

UNIT: Final = "원"  # [실측 필요 #48]
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

    return client(ctx, "kofia", make)


def _one(ctx: JobContext, repos: PublicRepos, key: DataKey) -> int:
    op = key.dataset.split("/", 1)[1]
    if op not in STATS:
        raise NotConfigured(f"모르는 오퍼레이션 {op}")
    if op == "fund":
        raise NotConfigured("펀드 순자산 — ctg·tstMthdCtg 값 목록 실측 전(#48)")
    day = date.fromisoformat(key.as_of[:10])
    k = _client(ctx)
    fetched = {"credit": k.credit_balance, "capital": k.market_capital, "cma": k.cma}[op](day, day)
    rows = rows_of(op, fetched, day)
    if not rows:
        raise NotReady(f"{day} 행 없음(D+1 13시 개방 — #48)")
    return repos.market_stats.upsert(rows, loaded_by=ctx.job, received_at=ctx.now)


def run(ctx: JobContext) -> JobResult:
    repos: PublicRepos = ctx.resources.get("public_repos") or pg_public_repos(
        ctx.resource("connect")
    )
    handlers = {f"DATAGO:{DATASET}/{op}": (lambda k: _one(ctx, repos, k)) for op in STATS}
    return run_keys(ctx, handlers)
