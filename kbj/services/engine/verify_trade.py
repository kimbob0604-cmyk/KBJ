"""매핑표 검증 CLI(docs/p6_design.md §0.3·§4.5, probe_results #56 — 묶음 MAP). **건수만** 낸다.

    python -m kbj.services.engine.verify_trade --map-only config/knowledge/trade_map.yaml
    python -m kbj.services.engine.verify_trade --watchlist main --json      # VM(실 DB) [실측 필요
    #56]
    python -m kbj.services.engine.verify_trade --public                     # VM — 공개 매핑표 전체

- `--map-only <파일>`: DB 없이 파일 하나를 해석(형식·짝수 자리·가중치·중복·근거 —
  `kbj.config.trade`) 하고 파일 사이 대조(`mapping.cross_check` — HS2 장·지역 ID)를 한다. CI·사람이
  매핑표를 고친 뒤 쓴다.
- `--watchlist <목록>`: VM 에서 실 DB(로그인 저장소 포함 — 관심종목은 `prv_journal.watchlist`)를
  읽어 ① 관심종목 수 ② 그중 매핑된 종목 수(공개·local 병합) ③ 매핑 종목코드가 상장사 고유번호 표에
  없는 수 ④ 매핑 HS 접두가 수집된 `pub_trade.hs_dim` 에 없는 줄 수 ⑤ 매핑된 관심종목의 겹쳐 보기
  상태별 수(지수 없음 포함)를 낸다. 종목코드·값은 내지 않는다(관심종목 — 개인 데이터).

- `--public`: 공개 매핑표 전체(판 3)를 실 DB 와 대조 — 종목코드가 상장사 표에 있는가·이름이 같은가,
  HS 접두가 hs_dim 에 있는가, 줄마다 최근 12개월 값이 있는가(전국 품목·시군구 수출), 매핑 HS6 의
  시군구 행 가운데 trade_regions.yaml 에 없는 이름. 코드·HS 는 찍고 값은 찍지 않는다.

종료 코드: 0 = 문제 0, 1 = 문제 있음(파일 검증 실패·없는 코드/HS·관심종목 0), 2 = 오류(파일
없음·DB). 이 모듈은 VM CLI 라 로그인 저장소를 읽는다 — 공개 작업(`trade_map`)과 따로 둔다(계약 ⑰
대상 밖).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

from kbj.config.files import ConfigFileError
from kbj.config.trade import (
    HsChapters,
    TradeRegions,
    load_hs_chapters,
    load_local_map,
    load_trade_regions,
)
from kbj.core.trade_rows import StockExportRow, StockMapRow
from kbj.engines.trade.mapping import cross_check, db_check, map_rows, map_version, resolve

__all__ = ["DbView", "PublicView", "check_file", "main", "verify_public", "verify_watchlist"]


def check_file(path: Path, *, chapters: HsChapters, regions: TradeRegions) -> dict[str, Any]:
    """파일 하나 검증 — {stocks, lines, reviewed, problems, problem_lines}. 해석 실패는
    ConfigFileError 그대로(부르는 쪽이 1 로)."""
    tm = load_local_map(path)
    probs = cross_check(tm, chapters=chapters, regions=regions)
    rows = map_rows(tm, origin="public", version=map_version(tm), as_of=datetime.now(UTC))
    return {
        "file": path.name,
        "map_version": map_version(tm),
        "stocks": len(tm.stocks),
        "lines": len(rows),
        "reviewed": sum(1 for s in tm.stocks if s.reviewed),
        "problems": len(probs),
        "problem_lines": [p.line() for p in probs],  # 공개 매핑표 — 코드를 보여도 된다
    }


@dataclass(frozen=True)
class DbView:
    """VM 검증 입력(시험이 주입)."""

    watch: Collection[str]
    smap: Sequence[StockMapRow]
    listed: Collection[str]
    hs_codes: Collection[str]
    exports: Sequence[StockExportRow]


def verify_watchlist(v: DbView) -> dict[str, Any]:
    """건수 요약(값·코드 없음)."""
    res = resolve(v.smap)
    watch = set(v.watch)
    mapped = sorted(watch & set(res.codes()))
    lines = [ln for c in mapped for ln in res.lines[c]]
    probs = db_check(lines, listed=v.listed, hs_codes=v.hs_codes)
    last: dict[str, StockExportRow] = {}
    for r in sorted(v.exports, key=lambda r: (r.is_quarter, r.period)):
        if r.code in watch:
            last[r.code] = r
    status: Counter[str] = Counter(last[c].status if c in last else "no_index" for c in mapped)
    kinds = Counter(p.kind for p in probs)
    return {
        "watchlist": len(watch),
        "mapped": len(mapped),
        "unmapped": len(watch) - len(mapped),
        "code_missing": kinds.get("code_missing", 0),
        "hs_missing": kinds.get("hs_missing", 0),
        "status": dict(sorted(status.items())),
        "conflicts": len(res.warnings),
    }


@dataclass(frozen=True)
class PublicView:
    """공개 매핑표 전체 대조 입력(시험이 주입). live_* = 최근 12개월에 값(0 아님)이 있는 키."""

    smap: Sequence[StockMapRow]  # origin public 줄
    corp: Mapping[str, str]  # 상장사 종목코드 → 회사명
    hs_codes: Collection[str]
    live_hs: Collection[tuple[str, str]]  # (HS 접두, exp|imp) — 전국 품목에 값이 있는 것
    live_sgg: Collection[tuple[str, str]]  # (region_id, HS6) — 시군구 수출에 값이 있는 것
    unmatched_sgg: Collection[
        tuple[str, str]
    ]  # 매핑 HS6 시군구 행 중 region_id 를 못 붙인 (시도, 이름)


def _norm_name(s: str) -> str:
    return "".join(s.split()).replace("(주)", "").replace("㈜", "").lower()


def verify_public(v: PublicView) -> dict[str, Any]:
    """공개 매핑표 전체 — 종목코드·이름·HS·최근 실적·시군구 이름(값은 내지 않는다)."""
    rows = [r for r in v.smap if r.origin == "public"]
    probs = db_check(rows, listed=v.corp, hs_codes=v.hs_codes)
    lines = [p.line() for p in probs]
    names: dict[str, str] = {r.code: r.name for r in rows}
    for code, nm in sorted(names.items()):
        got = v.corp.get(code)
        if got is not None and _norm_name(got) != _norm_name(nm):
            lines.append(f"{code} name_mismatch 매핑 '{nm}' · 상장사 표 '{got}'")
    dead = 0
    for r in rows:
        key_ok = (r.region_id, r.hs) in v.live_sgg if r.region_id else (r.hs, r.flow) in v.live_hs
        if not key_ok:
            dead += 1
            where = r.region_id or "전국"
            lines.append(f"{r.code} no_recent {r.flow} HS {r.hs} {where} — 최근 12개월 값 없음")
    for sido, nm in sorted(v.unmatched_sgg):
        lines.append(
            f"- region_unmatched 시도 {sido} '{nm}' — trade_regions.yaml 에 없음(매핑 HS6 행)"
        )
    kinds = Counter(p.kind for p in probs)
    return {
        "stocks": len(names),
        "lines": len(rows),
        "code_missing": kinds.get("code_missing", 0),
        "hs_missing": kinds.get("hs_missing", 0),
        "name_mismatch": sum(1 for x in lines if " name_mismatch " in x),
        "no_recent": dead,
        "region_unmatched": len(v.unmatched_sgg),
        "problem_lines": lines,
    }


def _production_public() -> PublicView:  # pragma: no cover — 운영(DB)
    from kbj.config.settings import Settings
    from kbj.core.trade_rows import prev_ym
    from kbj.store.db import connect
    from kbj.store.repos import pg_repos

    settings = Settings()
    if settings.database_url is None:
        raise RuntimeError("KBJ_DATABASE_URL 이 필요하다")
    repos = pg_repos(lambda: connect(settings, service="verify_trade"))
    t = repos.trade
    smap = t.stock_map(origin="public")
    last = t.last_ym("pub_trade.item_monthly") or "2000-01"
    lo = prev_ym(last, 11)
    live_hs: set[tuple[str, str]] = set()
    for hs in sorted({r.hs for r in smap if not r.region_id}):
        for row in t.item_monthly(prefix=hs, ym_from=lo, ym_to=last):
            if row.exp_usd:
                live_hs.add((hs, "exp"))
            if row.imp_usd:
                live_hs.add((hs, "imp"))
    hs6s = {r.hs for r in smap if r.region_id}
    live_sgg: set[tuple[str, str]] = set()
    unmatched: set[tuple[str, str]] = set()
    slast = t.last_ym("pub_trade.sigungu_monthly") or last
    for row in t.sigungu_monthly(ym_from=prev_ym(slast, 11), ym_to=slast):
        if row.hs6 not in hs6s:
            continue
        if row.region_id is None:
            unmatched.add((row.sido_cd, row.sgg_nm))
        elif row.exp_usd:
            live_sgg.add((row.region_id, row.hs6))
    return PublicView(
        smap=smap,
        corp={c: nm for c, (_cc, nm) in repos.filings.corp_index().items()},
        hs_codes={d.hs for d in t.hs_dim()},
        live_hs=live_hs,
        live_sgg=live_sgg,
        unmatched_sgg=unmatched,
    )


def _production(list_name: str) -> DbView:  # pragma: no cover — 운영(DB)
    from kbj.config.settings import Settings
    from kbj.store.db import connect
    from kbj.store.repos import pg_repos

    settings = Settings()
    if settings.database_url is None:
        raise RuntimeError("KBJ_DATABASE_URL 이 필요하다")
    repos = pg_repos(lambda: connect(settings, service="verify_trade"))
    return DbView(
        watch=[i.code for i in repos.journal.watchlist(list_name)],
        smap=repos.trade.stock_map(),
        listed=set(repos.filings.corp_index()),
        hs_codes={d.hs for d in repos.trade.hs_dim()},
        exports=repos.trade.stock_export(),
    )


def _render(summary: Mapping[str, Any], out: IO[str]) -> None:
    for k, v in summary.items():
        if isinstance(v, list):
            out.write(f"{k}:\n")
            for x in v:  # pyright: ignore[reportUnknownVariableType]
                out.write(f"  - {x}\n")
        else:
            out.write(f"{k}: {v}\n")


def _write(summary: Mapping[str, Any], out: IO[str], *, as_json: bool) -> None:
    if as_json:
        out.write(json.dumps(summary, ensure_ascii=False, sort_keys=True) + "\n")
    else:
        _render(summary, out)


def main(
    argv: Sequence[str] | None = None,
    *,
    out: IO[str] | None = None,
    err: IO[str] | None = None,
    db: Callable[[str], DbView] | None = None,
    public: Callable[[], PublicView] | None = None,
) -> int:
    o: IO[str] = out if out is not None else sys.stdout
    e_: IO[str] = err if err is not None else sys.stderr
    p = argparse.ArgumentParser(prog="python -m kbj.services.engine.verify_trade")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--map-only", type=Path, help="매핑표 파일 하나만 검증(DB 없음)")
    g.add_argument("--watchlist", help="관심종목 목록 이름(VM — 실 DB)")
    g.add_argument("--public", action="store_true", help="공개 매핑표 전체를 실 DB 와 대조(VM)")
    p.add_argument("--json", action="store_true", help="요약을 JSON 으로")
    args = p.parse_args(argv)
    if args.map_only is not None:
        try:
            summary = check_file(
                args.map_only, chapters=load_hs_chapters(), regions=load_trade_regions()
            )
        except FileNotFoundError:
            e_.write(f"오류: 파일 없음 — {args.map_only.name}\n")
            return 2
        except ConfigFileError as ex:
            e_.write(f"매핑표 형식 오류: {ex}\n")
            return 1
        _write(summary, o, as_json=args.json)
        return 0 if summary["problems"] == 0 else 1
    if args.public:
        try:
            pv = (public or _production_public)()
        except Exception as ex:  # 설정·DB — 사유는 형식 이름만
            e_.write(f"오류: {type(ex).__name__} — 설정·DB 를 확인한다\n")
            return 2
        summary = verify_public(pv)
        _write(summary, o, as_json=args.json)
        bad = ("code_missing", "hs_missing", "name_mismatch", "no_recent", "region_unmatched")
        return 0 if all(summary[k] == 0 for k in bad) else 1
    try:
        view = (db or _production)(args.watchlist)
    except Exception as ex:  # 설정·DB — 사유는 형식 이름만(접속 문자열을 싣지 않는다)
        e_.write(f"오류: {type(ex).__name__} — 설정·DB 를 확인한다\n")
        return 2
    summary = verify_watchlist(view)
    _write(summary, o, as_json=args.json)
    if summary["watchlist"] == 0:
        e_.write(f"관심종목 '{args.watchlist}' 이 비었다\n")
        return 1
    return 0 if summary["code_missing"] == 0 and summary["hs_missing"] == 0 else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
