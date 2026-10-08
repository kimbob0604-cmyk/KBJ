"""KRX D+1 대조 — 전날 KIS 마감 스냅 대 KRX 확정 일별(docs/p3_design.md §3.6, metrics §6.6, D-P3-8).

승격 원본: ET `board/engine/build.py:close_provenance`:38(`CLOSE_CONFIRM_MIN` 0.9 — 확정 비율은
board 엔진의 몫이라 여기 옮기지 않는다), ET `board/ingest/pipeline.py:CLOSE_DIFF_PCT`:64(반올림 차이
문턱 — KBJ 는 `config/markets.yaml` `reconcile.*` 허용치로 바꿨다), `_apply_krx_snapshot`:121 의 '큰
차이 예시'.

- 순수 `reconcile(kis, krx, *, cfg, day, checked_at)` → 종목·필드(close·turnover·mktcap)마다
  `ReconcileRow`. 판정: 둘 다 있으면 상대 차이(%) ≤ 허용치면 ok, 아니면 mismatch. 한쪽에만 있으면
  missing_kis·missing_krx. 종가 허용치 0 = 정확히 같아야 한다. 시가총액은 KIS 가 억원 단위로 주므로
  (`quotes.MKTCAP_UNIT_KRW` [실측 필요 #22]) 차이가 그 단위 미만이면 ok(소형주의 반올림이 불일치로
  KIS 행을 무효로 만들지 않게).
- 기록 `record(repos, day, now, cfg)`: 그날 KIS 스냅(source kis·ok·거래소 KRX — KRX OpenAPI 는 KRX
  체결분 [실측 필요])과 KRX 스냅(source krx)을 읽어 대조하고 `prv_market.eod_reconcile` 에 쓴다.
  불일치 종목의 KIS 스냅은 `invalid` + 사유(필드·차이). 원장은 krx 행을 고른다(D-P3-7). KIS 마감값이
  그날 하나도 없으면 대조하지 않는다(missing_kis 수천 행을 만들지 않는다 — 사유를 남긴다). KRX 행은
  KIS 가 받는 시장(코스피·코스닥)만, 그중 KRX 가 이미 낸 시장만 대조한다.
- 요약(불일치 수·최대 차이·불일치 비율)은 `ops.job_run.detail` 로 간다. 비율 ≥
  `reconcile.warn_mismatch_pct` 면 `warn=True`(health `reconcile_mismatch` — 처리기가 로그로 낸다).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final, Literal

from kbj.config.markets import ReconcileCfg
from kbj.core.quality import Quality
from kbj.core.rows import ReconcileRow, Snap
from kbj.data.private.kis.quotes import MKTCAP_UNIT_KRW
from kbj.services.collectors._p3 import RepoSet

__all__ = [
    "EXAMPLE_DIFF_PCT",
    "KIS_MARKETS",
    "ReconcileSummary",
    "reconcile",
    "record",
    "summarize",
]

Field = Literal["close", "turnover", "mktcap"]
FIELDS: Final[tuple[Field, ...]] = ("close", "turnover", "mktcap")
KIS_MARKETS: Final = frozenset({"KOSPI", "KOSDAQ"})  # market.close_collect 가 받는 시장
EXAMPLE_DIFF_PCT: Final = 3.0  # 이보다 큰 차이는 예시로 남긴다(ET _apply_krx_snapshot)
EXAMPLES_MAX: Final = 5


def _tol(cfg: ReconcileCfg, f: Field) -> float:
    return {
        "close": cfg.close_tol_pct,
        "turnover": cfg.turnover_tol_pct,
        "mktcap": cfg.mktcap_tol_pct,
    }[f]


def _value(s: Snap, f: Field) -> float | None:
    v = getattr(s, f)
    return None if v is None else float(v)


def _market(name: str | None) -> str:
    """'KOSDAQ GLOBAL' → 'KOSDAQ'(첫 낱말) — 소속 이름이 달라 대조에서 빠지지 않게."""
    parts = (name or "").upper().split()
    return parts[0] if parts else ""


def _diff_pct(kis: float, krx: float) -> float | None:
    if krx == 0:
        return 0.0 if kis == 0 else None
    return (kis - krx) / abs(krx) * 100


def reconcile(
    kis: Mapping[str, Snap],
    krx: Mapping[str, Snap],
    *,
    cfg: ReconcileCfg,
    day: date,
    checked_at: datetime,
) -> list[ReconcileRow]:
    """종목·필드마다 대조 한 줄(코드·필드 순)."""
    out: list[ReconcileRow] = []
    for code in sorted(set(kis) | set(krx)):
        a, b = kis.get(code), krx.get(code)
        for f in FIELDS:
            kv = None if a is None else _value(a, f)
            rv = None if b is None else _value(b, f)
            if kv is None and rv is None:
                continue  # 양쪽 다 그 칸이 없다 — 대조할 것이 없다
            if kv is None:
                verdict: Literal["ok", "mismatch", "missing_kis", "missing_krx"] = "missing_kis"
                diff = None
            elif rv is None:
                verdict, diff = "missing_krx", None
            else:
                diff = _diff_pct(kv, rv)
                tol = _tol(cfg, f)
                same = kv == rv if tol == 0 else diff is not None and abs(diff) <= tol
                if f == "mktcap" and abs(kv - rv) < MKTCAP_UNIT_KRW:
                    same = True  # KIS 시총은 억원 단위(hts_avls) — 단위 미만 차이는 표기 차이
                verdict = "ok" if same else "mismatch"
            out.append(ReconcileRow(day, code, f, kv, rv, diff, verdict, checked_at))
    return out


@dataclass(frozen=True)
class ReconcileSummary:
    checked: int  # 양쪽에 다 있는 종목
    mismatch: int  # 불일치 필드가 하나라도 있는 종목
    missing_kis: int
    missing_krx: int
    max_abs_diff_pct: float | None
    mismatch_pct: float | None
    warn: bool
    examples: tuple[str, ...]  # 큰 차이 예시('코드 필드 ±x.x%')
    invalidated: int = 0
    skipped: str = ""

    def detail(self) -> dict[str, Any]:
        if self.skipped:
            return {"reconcile": {"skipped": self.skipped}}
        d: dict[str, Any] = {
            "checked": self.checked,
            "mismatch": self.mismatch,
            "missing_kis": self.missing_kis,
            "missing_krx": self.missing_krx,
            "invalidated": self.invalidated,
            "warn": self.warn,
        }
        if self.max_abs_diff_pct is not None:
            d["max_abs_diff_pct"] = round(self.max_abs_diff_pct, 4)
        if self.examples:
            d["examples"] = list(self.examples)
        return {"reconcile": d}


def summarize(rows: list[ReconcileRow], cfg: ReconcileCfg) -> ReconcileSummary:
    by_code: dict[str, set[str]] = {}
    for r in rows:
        by_code.setdefault(r.code, set()).add(r.verdict)
    checked = sum(1 for v in by_code.values() if not v & {"missing_kis", "missing_krx"})
    mismatch = sum(1 for v in by_code.values() if "mismatch" in v)
    diffs = [abs(r.diff_pct) for r in rows if r.diff_pct is not None and r.verdict == "mismatch"]
    pct = None if checked == 0 else mismatch / checked * 100
    examples = tuple(
        f"{r.code} {r.field} {r.diff_pct:+.1f}%"
        for r in rows
        if r.verdict == "mismatch" and r.diff_pct is not None and abs(r.diff_pct) > EXAMPLE_DIFF_PCT
    )[:EXAMPLES_MAX]
    return ReconcileSummary(
        checked=checked,
        mismatch=mismatch,
        missing_kis=sum(1 for v in by_code.values() if "missing_kis" in v),
        missing_krx=sum(1 for v in by_code.values() if "missing_krx" in v),
        max_abs_diff_pct=max(diffs) if diffs else None,
        mismatch_pct=pct,
        warn=pct is not None and pct >= cfg.warn_mismatch_pct,
        examples=examples,
    )


def _note(rows: list[ReconcileRow]) -> str:
    parts = [
        f"{r.field} {r.diff_pct:+.3f}%" if r.diff_pct is not None else r.field
        for r in rows
        if r.verdict == "mismatch"
    ]
    return "KRX D+1 대조 불일치: " + ", ".join(parts)


def record(repos: RepoSet, day: date, now: datetime, cfg: ReconcileCfg) -> ReconcileSummary:
    """그날 KIS 마감 대 KRX 확정을 대조해 쓰고, 불일치 KIS 스냅을 invalid 로 바꾼다."""
    krx_all = repos.market.snapshots(day, source="krx")
    present = {m for s in krx_all if (m := _market(s.market)) in KIS_MARKETS}
    kis_rows = [
        s
        for s in repos.market.snapshots(day, source="kis")
        if s.venue == "KRX" and s.quality is not Quality.INVALID
    ]
    empty = ReconcileSummary(0, 0, 0, 0, None, None, False, ())
    if not kis_rows:
        return ReconcileSummary(**{**empty.__dict__, "skipped": "그날 KIS 마감값이 없다"})
    if not present:
        return ReconcileSummary(**{**empty.__dict__, "skipped": "KRX 코스피·코스닥 행이 아직 없다"})
    kis = {s.code: s for s in kis_rows if s.market is None or _market(s.market) in present}
    krx = {s.code: s for s in krx_all if _market(s.market) in present}
    rows = reconcile(kis, krx, cfg=cfg, day=day, checked_at=now)
    repos.market.put_reconcile(rows)
    by_code: dict[str, list[ReconcileRow]] = {}
    for r in rows:
        by_code.setdefault(r.code, []).append(r)
    invalidated = 0
    for code, rs in by_code.items():
        if any(r.verdict == "mismatch" for r in rs):
            s = kis[code]
            if repos.market.set_snapshot_quality(
                day, code, source=s.source, venue=s.venue, quality=Quality.INVALID, note=_note(rs)
            ):
                invalidated += 1
    summary = summarize(rows, cfg)
    return ReconcileSummary(**{**summary.__dict__, "invalidated": invalidated})
