"""분석 일지·매매 기록 쓰기 모듈 — `prv_journal.analysis`·`trade` 의 쓰는 쪽 하나(P8 묶음 JNL,
docs/p8_design.md D-P8-15·16·19, §4.7, ADR 0033).

API 라우트(`/api/journal/*` POST·PATCH·DELETE)·CLI(`python -m kbj.services.personal journal …`)·
SD 이관(`kbj.store.legacy_import.sd_p8`)이 같은 함수를 부른다. 저장은 `PersonalRepo`, 읽기는
`JournalRepo`. 로그인 원천 어댑터·수집기를 import 하지 않는다.

- 일지: 칸 dict(API 모델 `AnalysisFields` 와 같은 키) → `AnalysisRow`(돈은 Decimal — float 를
  문자열로 거쳐 바꾼다). 고치기는 전체 칸을 바꾼다(PATCH 도 전체 — FND 모양).
- 매매: 넣기 전에 그 종목의 기존 매매 + 새 매매를 다시 계산해 보유가 음수가 되면 `NegativeHolding`
  (422). 지우기도 같은 검사(매수를 지워 뒤 매도가 보유보다 많아지면 거절). 매매 이름은 그날 유니버스
  이름(없으면 None — 지어내지 않는다).
- CSV 가져오기(`journal trades import --csv <파일>`): 머리 줄 필수(`date,code,side,price,qty` +
  선택 `fee,tax,strategy,memo,name`), side 는 buy·sell·매수·매도. 형식 오류·음수 보유는 거절 목록(줄
  번호·사유만 — 값은 찍지 않는다), 거절이 있으면 아무것도 쓰지 않는다(`--skip-rejects` 로 나머지만).
- 출력·오류 문구에 금액·수량을 넣지 않는다(개인 데이터 — 로그에 남기지 않는다).

시계는 주입한다(시험).
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import IO, Any, Final, Literal, Protocol

from kbj.config.rules_cfg import RulesConfig, load_rules_cfg
from kbj.core.rules_rows import AnalysisRow, TradeRow
from kbj.core.time import KST, utcnow
from kbj.engines.journal.portfolio import NegativeHolding, replay
from kbj.engines.rules.dsl import DslError
from kbj.engines.rules.legacy_sd import Converted, Unconverted, convert
from kbj.services.personal.rules import create_rule
from kbj.store.legacy_import.sd_p8 import (
    SOURCE as SD_SOURCE,
)
from kbj.store.legacy_import.sd_p8 import (
    Drop8,
    read_alert_rules,
    read_sd_db,
    read_sd_portfolio,
)
from kbj.store.legacy_import.sources import SourceError
from kbj.store.repos.journal import JournalRepo
from kbj.store.repos.market import MarketRepo
from kbj.store.repos.personal import PersonalRepo
from kbj.store.repos.rules import RulesRepo

__all__ = [
    "CSV_REQUIRED",
    "AnalysisNotFound",
    "CsvReject",
    "JournalRepos",
    "NegativeHolding",
    "SdImportReport",
    "TradeNotFound",
    "TradePlan",
    "add_trade",
    "analysis_row",
    "cli",
    "create_analysis",
    "delete_analysis",
    "delete_trade",
    "import_sd",
    "import_trades",
    "plan_csv",
    "trade_row",
    "update_analysis",
]

CSV_REQUIRED: Final = ("date", "code", "side", "price", "qty")
_SIDE: Final = {
    "buy": "buy",
    "sell": "sell",
    "매수": "buy",
    "매도": "sell",
    "b": "buy",
    "s": "sell",
}
_CASES: Final = ("bear", "base", "bull")
Source = Literal["api", "cli", "import:sd", "import:csv"]


class JournalRepos(Protocol):
    @property
    def journal(self) -> JournalRepo: ...

    @property
    def personal(self) -> PersonalRepo: ...

    @property
    def market(self) -> MarketRepo: ...


class AnalysisNotFound(LookupError):
    """일지가 없다(404)."""


class TradeNotFound(LookupError):
    """매매가 없다(404)."""


# ── 일지 ────────────────────────────────────────────────────────────────────────────


def _dec(v: object) -> Decimal | None:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return v
    if isinstance(v, bool) or not isinstance(v, int | float | str):
        raise ValueError("금액은 수여야 한다")
    try:
        d = Decimal(str(v))
    except InvalidOperation:
        raise ValueError("금액 형식이 틀렸다") from None
    if not d.is_finite():
        raise ValueError("금액은 유한해야 한다")
    return d


def _float(v: object) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int | float | Decimal):
        raise ValueError("수여야 한다")
    return float(v)


def _cases(m: object, conv: Callable[[object], Any]) -> dict[str, Any]:
    src: Mapping[str, object] = m if isinstance(m, Mapping) else {}
    return {c: conv(src.get(c)) for c in _CASES}


def analysis_row(
    fields: Mapping[str, Any],
    *,
    source: str,
    now: datetime,
    analysis_id: int | None = None,
) -> AnalysisRow:
    """API 모델 칸(dict) → 행. 형식 오류는 ValueError(행 검사)."""
    chain = list(fields.get("chain_ids") or [None, None, None])
    chain = [*chain, None, None, None][:3]
    mktcap = fields.get("mktcap")
    return AnalysisRow(
        id=analysis_id,
        code=str(fields["code"]),
        analyzed_on=fields["analyzed_on"],
        chain_ids=(chain[0], chain[1], chain[2]),
        price=_dec(fields.get("price")),
        ret_52w=_float(fields.get("ret_52w")),
        fwd_per=_float(fields.get("fwd_per")),
        band_pos=_float(fields.get("band_pos")),
        ev_ebitda=_float(fields.get("ev_ebitda")),
        mktcap=None if mktcap is None else _dec(mktcap),
        opm=_float(fields.get("opm")),
        eps=_cases(fields.get("eps"), _dec),
        per=_cases(fields.get("per"), _float),
        tp=_cases(fields.get("tp"), _dec),
        tp_source=fields.get("tp_source"),
        conviction=fields.get("conviction"),
        memo=fields.get("memo"),
        tags=tuple(str(t) for t in fields.get("tags") or ()),
        conclusion=fields.get("conclusion"),
        value_sources={str(k): str(v) for k, v in (fields.get("value_sources") or {}).items()},
        source=source,
        updated_at=now,
    )


def create_analysis(
    repos: JournalRepos, fields: Mapping[str, Any], *, source: Source, now: datetime
) -> AnalysisRow:
    row = analysis_row(fields, source=source, now=now)
    new_id = repos.personal.add_analysis_personal(row)
    got = repos.journal.analysis(new_id)
    if got is None:  # pragma: no cover — 방금 넣은 행
        raise RuntimeError("넣은 일지를 다시 읽지 못했다")
    return got


def update_analysis(
    repos: JournalRepos, analysis_id: int, fields: Mapping[str, Any], *, now: datetime
) -> AnalysisRow:
    """전체 칸을 바꾼다. 출처(api·import:sd …)는 처음 값을 지킨다."""
    old = repos.journal.analysis(analysis_id)
    if old is None:
        raise AnalysisNotFound(analysis_id)
    row = analysis_row(fields, source=old.source, now=now, analysis_id=analysis_id)
    if not repos.personal.update_analysis_personal(analysis_id, row):
        raise AnalysisNotFound(analysis_id)
    got = repos.journal.analysis(analysis_id)
    if got is None:  # pragma: no cover
        raise AnalysisNotFound(analysis_id)
    return got


def delete_analysis(repos: JournalRepos, analysis_id: int) -> int:
    if not repos.personal.delete_analysis_personal(analysis_id):
        raise AnalysisNotFound(analysis_id)
    return analysis_id


# ── 매매 ────────────────────────────────────────────────────────────────────────────


def _name_of(repos: JournalRepos, code: str, day: date) -> str | None:
    for u in repos.market.universe(day):
        if u.code == code:
            return u.name
    return None


def trade_row(
    fields: Mapping[str, Any],
    *,
    source: str,
    now: datetime,
    name: str | None = None,
) -> TradeRow:
    """API 모델 `TradeIn` 칸(dict) → 행(id 없음). 형식 오류는 ValueError."""
    price = _dec(fields["price"])
    if price is None:
        raise ValueError("가격이 없다")
    return TradeRow(
        id=None,
        code=str(fields["code"]),
        name_at_trade=name,
        side=fields["side"],
        trade_date=fields["trade_date"],
        price=price,
        qty=int(fields["qty"]),
        fee=_dec(fields.get("fee") or 0) or Decimal(0),
        tax=_dec(fields.get("tax") or 0) or Decimal(0),
        strategy=fields.get("strategy"),
        memo=fields.get("memo"),
        signal_id=fields.get("signal_id"),
        source=source,
        created_at=now,
    )


def _check_book(trades: Iterable[TradeRow]) -> None:
    replay(trades)  # 음수 보유면 NegativeHolding


def add_trade(
    repos: JournalRepos, fields: Mapping[str, Any], *, source: Source, now: datetime
) -> TradeRow:
    code = str(fields["code"])
    day: date = fields["trade_date"]
    row = trade_row(fields, source=source, now=now, name=_name_of(repos, code, day))
    existing = repos.journal.trades(code=code)
    # 새 행은 같은 날 기존 행 뒤에 들어간다(id 가 더 크다)
    probe = replace(row, id=max((t.id or 0 for t in existing), default=0) + 1)
    _check_book([*existing, probe])
    new_id = repos.personal.add_trade_personal(row)
    for t in repos.journal.trades(code=code):
        if t.id == new_id:
            return t
    raise RuntimeError("넣은 매매를 다시 읽지 못했다")  # pragma: no cover


def delete_trade(repos: JournalRepos, trade_id: int) -> int:
    rows = repos.journal.trades()
    target = next((t for t in rows if t.id == trade_id), None)
    if target is None:
        raise TradeNotFound(trade_id)
    _check_book(t for t in rows if t.code == target.code and t.id != trade_id)
    if not repos.personal.delete_trade_personal(trade_id):
        raise TradeNotFound(trade_id)
    return trade_id


# ── CSV 가져오기 ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CsvReject:
    line: int
    reason: Literal["bad_header", "bad_row", "bad_code", "bad_date", "bad_side", "bad_number",
                    "negative_holding"]  # fmt: skip


@dataclass
class TradePlan:
    rows: list[tuple[int, TradeRow]] = field(default_factory=list[tuple[int, TradeRow]])
    rejects: list[CsvReject] = field(default_factory=list[CsvReject])


def _cell(rec: Mapping[str, str], key: str) -> str:
    return (rec.get(key) or "").strip()


def plan_csv(text: str, *, now: datetime, existing: Sequence[TradeRow]) -> TradePlan:
    """CSV 본문 → 넣을 행·거절(줄 번호는 머리 줄이 1). 음수 보유는 기존 + 가져올 행 전체로 본다."""
    plan = TradePlan()
    reader = csv.DictReader(io.StringIO(text))
    heads = [h.strip().lower() for h in (reader.fieldnames or [])]
    if not set(CSV_REQUIRED) <= set(heads):
        plan.rejects.append(CsvReject(1, "bad_header"))
        return plan
    reader.fieldnames = heads
    for rec in reader:
        n = reader.line_num
        if not any((v or "").strip() for v in rec.values() if isinstance(v, str)):
            continue
        code = _cell(rec, "code")
        if code.isdigit() and len(code) < 6:
            code = code.zfill(6)
        try:
            day = date.fromisoformat(_cell(rec, "date"))
        except ValueError:
            plan.rejects.append(CsvReject(n, "bad_date"))
            continue
        side = _SIDE.get(_cell(rec, "side").lower())
        if side is None:
            plan.rejects.append(CsvReject(n, "bad_side"))
            continue
        try:
            qty_s = _cell(rec, "qty").replace(",", "")
            fields: dict[str, Any] = {
                "code": code,
                "side": side,
                "trade_date": day,
                "price": _cell(rec, "price").replace(",", ""),
                "qty": int(qty_s),
                "fee": _cell(rec, "fee").replace(",", "") or "0",
                "tax": _cell(rec, "tax").replace(",", "") or "0",
                "strategy": _cell(rec, "strategy") or None,
                "memo": _cell(rec, "memo") or None,
            }
        except ValueError:
            plan.rejects.append(CsvReject(n, "bad_number"))
            continue
        try:
            row = trade_row(fields, source="import:csv", now=now, name=_cell(rec, "name") or None)
        except ValueError as e:
            reason: Literal["bad_code", "bad_number"] = (
                "bad_code" if "code" in str(e) else "bad_number"
            )
            plan.rejects.append(CsvReject(n, reason))
            continue
        plan.rows.append((n, row))
    # 음수 보유 — 종목마다 기존 + 새 행(새 행 id 는 기존 뒤, 파일 순서)
    base = max((t.id or 0 for t in existing), default=0)
    probes = [(n, replace(r, id=base + i + 1)) for i, (n, r) in enumerate(plan.rows)]
    bad: set[int] = set()
    for code in sorted({r.code for _, r in probes}):
        mine = [t for t in existing if t.code == code]
        news = [(n, r) for n, r in probes if r.code == code]
        try:
            _check_book([*mine, *(r for _, r in news)])
        except NegativeHolding as e:
            sells = [n for n, r in news if r.trade_date == e.day and r.side == "sell"]
            hit = sells[0] if sells else news[0][0]
            bad.add(hit)
            plan.rejects.append(CsvReject(hit, "negative_holding"))
    plan.rows = [(n, r) for n, r in plan.rows if n not in bad]
    plan.rejects.sort(key=lambda x: x.line)
    return plan


def import_trades(repos: JournalRepos, plan: TradePlan) -> int:
    """계획의 행을 넣는다(파일 순서). 넣은 수."""
    for _, row in plan.rows:
        repos.personal.add_trade_personal(row)
    return len(plan.rows)


# ── SD 이관(P8 — kbj.store.legacy_import.sd_p8 읽기 → 이 모듈 쓰기) ───────────────────────


class SdRepos(JournalRepos, Protocol):
    @property
    def rules(self) -> RulesRepo: ...


@dataclass
class SdImportReport:
    analyses: int = 0
    trades: int = 0
    picks: int = 0
    rules: int = 0
    rules_unconverted: list[Unconverted] = field(default_factory=list[Unconverted])
    rules_rejected: int = 0  # 변환은 됐으나 DSL 검사에서 거절
    rules_existing: int = 0  # 같은 이름이 이미 있어 건너뜀(멱등)
    drops: list[Drop8] = field(default_factory=list[Drop8])
    skipped: list[str] = field(default_factory=list[str])  # 이미 이관된 표 등

    def lines(self) -> list[str]:
        out = [
            f"일지 {self.analyses}건, 매매 {self.trades}건, 추천 {self.picks}건, "
            f"규칙 {self.rules}개 넣음",
            f"규칙: 변환 못 함 {len(self.rules_unconverted)}개, 검사 거절 {self.rules_rejected}개, "
            f"이미 있음 {self.rules_existing}개",
            f"버린 행 {len(self.drops)}개",
        ]
        reasons: dict[tuple[str, str], int] = {}
        for d in self.drops:
            reasons[(d.origin, d.reason)] = reasons.get((d.origin, d.reason), 0) + 1
        out += [f"  {o} {r}: {n}" for (o, r), n in sorted(reasons.items())]
        ur: dict[str, int] = {}
        for u in self.rules_unconverted:
            ur[u.reason] = ur.get(u.reason, 0) + 1
        out += [f"  규칙 {r}: {n}" for r, n in sorted(ur.items())]
        out += [f"  건너뜀: {x}" for x in self.skipped]
        return out


def _valid_by_code(rows: Sequence[TradeRow], report: SdImportReport, origin: str) -> list[TradeRow]:
    """종목마다 보유가 음수가 되면 그 종목 매매를 통째로 뺀다(일부만 넣으면 손익이 틀어진다)."""
    keep: list[TradeRow] = []
    for code in sorted({r.code for r in rows}):
        mine = [replace(r, id=i + 1) for i, r in enumerate(x for x in rows if x.code == code)]
        try:
            replay(mine)
        except NegativeHolding:
            report.drops += [Drop8(origin, -1, "negative_holding") for _ in mine]
            continue
        keep += [replace(r, id=None) for r in mine]
    return keep


def import_sd(
    repos: SdRepos,
    *,
    now: datetime,
    db: Path | None = None,
    rules: Path | None = None,
    portfolio: Path | None = None,
    cfg: RulesConfig | None = None,
) -> SdImportReport:
    """SD 개인 기록을 넣는다. 일지·매매는 `import:sd` 행이 이미 있으면 건너뛴다(두 번 넣지 않는다),
    추천은 키 upsert, 규칙은 같은 이름이 있으면 건너뛴다. 규칙은 **꺼진 채** 넣는다(사용자가 페이지
    11 에서 보고 켠다 — 이관 직후 텔레그램이 쏟아지지 않게). 포지션 기초 매수는 SD 매매 기록에 없는
    종목만(`covered_by_trades` — 두 번 세지 않게)."""
    rep = SdImportReport()
    if db is not None:
        got = read_sd_db(db, now=now)
        rep.drops += got.drops
        rep.skipped += [f"{t}(원본에 표 없음)" for t in got.missing_tables]
        if any(a.source == SD_SOURCE for a in repos.journal.analyses(limit=1_000_000)):
            rep.skipped.append("analysis_journal(이미 이관됨)")
        else:
            for a in got.analyses:
                repos.personal.add_analysis_personal(a)
                rep.analyses += 1
        sd_trades = list(got.trades)
        if got.picks:
            rep.picks = repos.personal.put_picks_legacy_import(got.picks, loaded_by="import:sd")
    else:
        sd_trades = []
    base: list[TradeRow] = []
    if portfolio is not None:
        rows, drops = read_sd_portfolio(portfolio, now=now)
        rep.drops += drops
        traded = {t.code for t in sd_trades}
        for r in rows:
            if r.code in traded:
                rep.drops.append(Drop8("server_portfolio", -1, "covered_by_trades"))
            else:
                base.append(r)
    if sd_trades or base:
        if any(t.source == SD_SOURCE for t in repos.journal.trades()):
            rep.skipped.append("trade_journal·server_portfolio(이미 이관됨)")
        else:
            existing = repos.journal.trades()
            mixed = {t.code for t in existing}
            todo = _valid_by_code([*base, *sd_trades], rep, "trade_journal")
            for t in todo:
                if t.code in mixed:  # KBJ 에 이미 그 종목 매매가 있으면 섞지 않는다
                    rep.drops.append(Drop8("trade_journal", -1, "code_has_kbj_trades"))
                    continue
                repos.personal.add_trade_personal(t)
                rep.trades += 1
    if rules is not None:
        c = cfg if cfg is not None else load_rules_cfg()
        names = {h.name for h in repos.rules.rules(include_archived=True)}
        for i, item in enumerate(read_alert_rules(rules)):
            got_rule = convert(item, i)
            if not isinstance(got_rule, Converted):
                rep.rules_unconverted.append(got_rule)
                continue
            if got_rule.rule["name"] in names:
                rep.rules_existing += 1
                continue
            try:
                create_rule(
                    repos,
                    got_rule.rule,
                    enabled=False,
                    source="import:sd",
                    now=now,
                    loaded_by="import:sd",
                    cfg=c,
                )
            except DslError:
                rep.rules_rejected += 1
                continue
            names.add(got_rule.rule["name"])
            rep.rules += 1
    return rep


# ── CLI ───────────────────────────────────────────────────────────────────────────


def cli(
    argv: Sequence[str],
    repos: SdRepos,
    *,
    now: Callable[[], datetime] = utcnow,
    out: IO[str] | None = None,
    err: IO[str] | None = None,
) -> int:
    """`python -m kbj.services.personal journal trades import --csv <파일> [--skip-rejects]`
    · `journal trades list` · `journal analysis list`. 종료 코드: 0 성공, 1 거절, 2 파일 오류.
    출력은 줄 번호·사유·건수만(금액·수량 없음)."""
    ap = argparse.ArgumentParser(prog="python -m kbj.services.personal journal")
    sub = ap.add_subparsers(dest="group", required=True)
    tr = sub.add_parser("trades", help="매매 기록")
    tsub = tr.add_subparsers(dest="cmd", required=True)
    imp = tsub.add_parser("import", help="CSV 가져오기")
    imp.add_argument("--csv", required=True, type=Path)
    imp.add_argument("--skip-rejects", action="store_true")
    imp.add_argument("--dry-run", action="store_true")
    tsub.add_parser("list", help="매매 건수(종목별)")
    sd = sub.add_parser("import-sd", help="SD 개인 기록 이관(일지·매매·추천·규칙·포지션)")
    sd.add_argument("--db", type=Path, help="SD dashboard.db")
    sd.add_argument("--rules", type=Path, help="SD cache/alert_rules.json")
    sd.add_argument("--portfolio", type=Path, help="SD cache/server_portfolio.json")
    an = sub.add_parser("analysis", help="분석 일지")
    asub = an.add_subparsers(dest="cmd", required=True)
    asub.add_parser("list", help="일지 목록(id·종목·분석일)")
    args = ap.parse_args(list(argv))
    o = out or sys.stdout
    e = err or sys.stderr
    if args.group == "import-sd":
        if args.db is None and args.rules is None and args.portfolio is None:
            print("실패: --db·--rules·--portfolio 중 하나는 있어야 한다", file=e)
            return 2
        try:
            rep = import_sd(
                repos,
                now=now(),
                db=args.db,
                rules=args.rules,
                portfolio=args.portfolio,
            )
        except (SourceError, OSError) as x:
            print(f"실패: {x}", file=e)
            return 2
        for line in rep.lines():
            print(line, file=o)
        return 0
    if args.group == "analysis":
        for a in repos.journal.analyses(limit=10_000):
            print(f"{a.id} {a.code} {a.analyzed_on.isoformat()} {a.source}", file=o)
        return 0
    if args.cmd == "list":
        counts: dict[str, int] = {}
        for t in repos.journal.trades():
            counts[t.code] = counts.get(t.code, 0) + 1
        for code, n in sorted(counts.items()):
            print(f"{code} {n}건", file=o)
        return 0
    path: Path = args.csv
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as x:
        print(f"실패: CSV 를 읽지 못했다: {path.name} ({type(x).__name__})", file=e)
        return 2
    t = now()
    plan = plan_csv(text, now=t, existing=repos.journal.trades())
    print(f"넣을 매매 {len(plan.rows)}건, 거절 {len(plan.rejects)}건", file=o)
    for rj in plan.rejects:
        print(f"  거절 {rj.line}행 — {rj.reason}", file=o)
    if plan.rejects and not args.skip_rejects:
        print("거절이 있어 쓰지 않았다(--skip-rejects 로 나머지만)", file=e)
        return 1
    if any(r.reason == "negative_holding" for r in plan.rejects):
        # 음수 보유 거절이 있으면 나머지만 넣어도 보유가 다시 틀어질 수 있다 — 다시 계산
        plan = plan_csv(
            _without(text, {r.line for r in plan.rejects}), now=t, existing=repos.journal.trades()
        )
        if plan.rejects:
            print("거절을 뺀 뒤에도 보유가 음수가 된다 — 쓰지 않았다", file=e)
            return 1
    if args.dry_run:
        print("시험 실행 — 쓰지 않았다", file=o)
        return 0
    n = import_trades(repos, plan)
    print(f"넣음 {n}건 ({t.astimezone(KST).date().isoformat()})", file=o)
    return 0


def _without(text: str, lines: set[int]) -> str:
    rows = text.splitlines()
    return "\n".join(r for i, r in enumerate(rows, start=1) if i not in lines) + "\n"
