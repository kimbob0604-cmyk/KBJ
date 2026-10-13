"""매핑표 검증 CLI(docs/p6_design.md §0.3·§4.5, probe_results #56 — 묶음 MAP). **건수만** 낸다.

    python -m kbj.services.engine.verify_trade --map-only config/knowledge/trade_map.yaml
    python -m kbj.services.engine.verify_trade --watchlist main --json      # VM(실 DB) [실측 필요
    #56]

- `--map-only <파일>`: DB 없이 파일 하나를 해석(형식·짝수 자리·가중치·중복·근거 —
  `kbj.config.trade`) 하고 파일 사이 대조(`mapping.cross_check` — HS2 장·지역 ID)를 한다. CI·사람이
  매핑표를 고친 뒤 쓴다.
- `--watchlist <목록>`: VM 에서 실 DB(로그인 저장소 포함 — 관심종목은 `prv_journal.watchlist`)를
  읽어 ① 관심종목 수 ② 그중 매핑된 종목 수(공개·local 병합) ③ 매핑 종목코드가 상장사 고유번호 표에
  없는 수 ④ 매핑 HS 접두가 수집된 `pub_trade.hs_dim` 에 없는 줄 수 ⑤ 매핑된 관심종목의 겹쳐 보기
  상태별 수(지수 없음 포함)를 낸다. 종목코드·값은 내지 않는다(관심종목 — 개인 데이터).

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

__all__ = ["DbView", "check_file", "main", "verify_watchlist"]


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
) -> int:
    o: IO[str] = out if out is not None else sys.stdout
    e_: IO[str] = err if err is not None else sys.stderr
    p = argparse.ArgumentParser(prog="python -m kbj.services.engine.verify_trade")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--map-only", type=Path, help="매핑표 파일 하나만 검증(DB 없음)")
    g.add_argument("--watchlist", help="관심종목 목록 이름(VM — 실 DB)")
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
