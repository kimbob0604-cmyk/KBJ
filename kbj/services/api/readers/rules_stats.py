"""알림 후 성과 통계 리더 — `/api/rules/stats`(docs/p8_design.md §4.5·§5, metrics §30.4 — 로그인).

읽을 때 계산(표 없음): `ctx.repos.rules.signals`(허락된 신호 — sent·suppressed·dry) →
`outcomes`(rules.outcome 이 쌓은 성과) → `kbj.engines.rules.stats`(백테스트 '기대' 칸과 같은 함수).

- 행 = 규칙(판을 주면 그 판만, 아니면 판 합침 — version None). 칸 = config/rules.yaml horizons.
- 분포 = 요청 기간(없으면 5일, 설정에 없으면 첫 기간)의 수익(%) 구간.
- 값 단위 % (승률·평균·중앙·사분위·초과·MFE·MAE). n < stats.min_samples 면 `small`.
- 봉투 notes: "과거 신호의 사후 수익 — 비용·체결 미반영"(수익을 약속하는 문구 없음).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Final

from kbj.config.rules_cfg import RulesConfig, load_rules_cfg
from kbj.core.rules_rows import ALLOWED_SIGNAL_STATUSES, OutcomeRow
from kbj.engines.flows.ledger import worst_quality
from kbj.engines.rules.stats import PCT, histogram, stats_cells
from kbj.services.api.models.backtest import DistBin, RuleStats, RuleStatsRow, StatsCell
from kbj.services.api.models.common import Envelope
from kbj.services.api.readers._common import NoData, ReadContext, envelope

__all__ = ["NOTE_PCT", "NOTE_POST_HOC", "NO_STATS", "SOURCE", "rule_stats", "stats_cell_models"]

NO_STATS: Final = "아직 없음 — rules.outcome 08:30"
SOURCE: Final = "KBJ:rules.outcome"
NOTE_POST_HOC: Final = "과거 신호의 사후 수익 — 비용·체결 미반영"
NOTE_PCT: Final = "값 단위 %(승률·수익·초과·MFE·MAE)"
SINCE: Final = date(2000, 1, 1)
DEFAULT_HORIZON: Final = 5


def stats_cell_models(rows: Sequence[OutcomeRow], cfg: RulesConfig) -> list[StatsCell]:
    return [
        StatsCell(
            horizon=c.horizon,
            n=c.n,
            excluded=c.excluded,
            win_rate=c.win_rate,
            mean=c.mean,
            median=c.median,
            q25=c.q25,
            q75=c.q75,
            excess_mean=c.excess_mean,
            mfe_median=c.mfe_median,
            mae_median=c.mae_median,
            small=c.small,
        )
        for c in stats_cells(rows, cfg.horizons, min_samples=cfg.stats.min_samples)
    ]


def _dist(rows: Sequence[OutcomeRow], horizon: int) -> list[DistBin]:
    vals = [r.ret * PCT for r in rows if r.horizon == horizon and r.ret is not None]
    return [DistBin(lo=b.lo, hi=b.hi, n=b.n) for b in histogram(vals)]


def rule_stats(
    ctx: ReadContext,
    *,
    rule_id: str | None,
    version: int | None,
    horizon: int | None,
    cfg: RulesConfig | None = None,
) -> Envelope[RuleStats]:
    c = cfg or load_rules_cfg()
    hz = horizon or (DEFAULT_HORIZON if DEFAULT_HORIZON in c.horizons else c.horizons[0])
    sigs = [
        s
        for s in ctx.repos.rules.signals(SINCE, ctx.today(), rule_id=rule_id)
        if s.status in ALLOWED_SIGNAL_STATUSES and (version is None or s.version == version)
    ]
    if not sigs:
        raise NoData(NO_STATS)
    outs = ctx.repos.rules.outcomes({s.signal_id for s in sigs})
    if not outs:
        raise NoData(NO_STATS)
    rule_of = {s.signal_id: s.rule_id for s in sigs}
    by_rule: dict[str, list[OutcomeRow]] = {}
    for o in outs:
        rid = rule_of.get(o.signal_id)
        if rid is not None:
            by_rule.setdefault(rid, []).append(o)
    rows: list[RuleStatsRow] = []
    for rid in sorted(by_rule):
        head = ctx.repos.rules.rule(rid)
        rs = by_rule[rid]
        rows.append(
            RuleStatsRow(
                rule_id=rid,
                version=version,
                name=None if head is None else head.name,
                cells=stats_cell_models(rs, c),
                dist=_dist(rs, hz),
            )
        )
    notes = [NOTE_POST_HOC, NOTE_PCT, f"분포 = {hz}거래일 수익"]
    if any(cell.small for r in rows for cell in r.cells):
        notes.append(f"표본 {c.stats.min_samples} 미만 칸은 '표본 적음'")
    excluded = sum(1 for o in outs if o.ret is None)
    if excluded:
        notes.append(f"정지·상폐·분할 의심으로 뺀 성과 {excluded}건")
    used = [o.quality for o in outs if o.ret is not None]
    return envelope(
        RuleStats(horizons=list(c.horizons), rows=rows),
        source=SOURCE,
        as_of=max(o.computed_at for o in outs),
        quality=worst_quality(used),
        notes=notes,
        generated_at=ctx.now(),
    )
