"""종목코드 하나로 DART 재무 리포트(xlsx)를 만든다 — legacy `dart-report/run.py` CLI 그대로.

    uv run python -m kbj.reports.dart_excel --stock 006110
    uv run python -m kbj.reports.dart_excel --stock 006110 --years 2023 2026 --out out/x.xlsx
    uv run python -m kbj.reports.dart_excel --stock 006110 --diagnose-costs   # 주석 계정명 진단
    uv run python -m kbj.reports.dart_excel --stock 006110 --diagnose-bridge  # 영업외손익 표 진단

docs/p4_design.md §1.5(`__main__` — 옛 CLI 옵션 그대로). 키는 `KBJ_DART_API_KEY`(설정 — 출력에
남기지 않는다). 매핑 기본 경로는 `config/dart_report/mapping.yaml`(legacy `config/mapping.yaml` 을
옮겼다). `--cache` 를 주면 DART 응답을 그 폴더에 둔다(캐시 키·내용에 인증키가 없다 — 정본 클라이언트
규칙). 종료 코드: 0 성공, 1 진단에서 표를 못 찾음, 2 재무제표를 하나도 받지 못함(legacy 그대로).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from kbj.core.time import utcnow
from kbj.engines.financials.costs import load_mapping
from kbj.reports.dart_excel.excel import build_workbook
from kbj.reports.dart_excel.pipeline import NoStatements, ReportSource, collect_inputs, diagnose

__all__ = ["main", "parse_args"]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m kbj.reports.dart_excel", description="DART 재무 리포트 생성기"
    )
    p.add_argument("--stock", required=True, help="종목코드 6자리 (예: 006110)")
    p.add_argument(
        "--years",
        nargs=2,
        type=int,
        metavar=("FROM", "TO"),
        help="대상 연도 범위. 기본: 최근 4개년",
    )
    p.add_argument(
        "--fs-div", default="CFS", choices=["CFS", "OFS"], help="CFS=연결(기본), OFS=별도"
    )
    p.add_argument("--out", default=None, help="출력 xlsx 경로")
    p.add_argument(
        "--mapping", default=None, help="매핑 YAML(기본 config/dart_report/mapping.yaml)"
    )
    p.add_argument("--cache", default=None, help="DART 응답 캐시 폴더(기본 없음)")
    p.add_argument("--no-orders", action="store_true", help="수주 공시 수집 생략 (빠름)")
    p.add_argument(
        "--diagnose-costs",
        action="store_true",
        help="주석에서 발견한 계정명과 매핑 결과만 출력하고 종료",
    )
    p.add_argument(
        "--diagnose-bridge",
        action="store_true",
        help="브릿지 대상 보고서의 영업외손익 표를 파일별로 덤프하고 종료",
    )
    p.add_argument("-q", "--quiet", action="store_true")
    return p.parse_args(argv)


def _client(cache: str | None) -> Any:
    from kbj.data.public.dart.client import DartClient

    return DartClient(cache_dir=Path(cache) if cache else None)


def main(
    argv: Sequence[str] | None = None,
    *,
    client: ReportSource | None = None,
    now: datetime | None = None,
    out: Callable[[str], None] = print,
    err: Callable[[str], None] = lambda s: print(s, file=sys.stderr),
) -> int:
    """CLI 진입점. 시험은 `client`(가짜 DART)·`now`(시계)·`out`/`err`(출력 모음)를 넣는다."""
    args = parse_args(argv)
    verbose = not args.quiet
    log = out if verbose else None
    mapping = load_mapping(args.mapping)
    src: ReportSource = client if client is not None else _client(args.cache)
    when = now if now is not None else utcnow()
    years = tuple(args.years) if args.years else None
    if args.diagnose_costs or args.diagnose_bridge:
        kind = "costs" if args.diagnose_costs else "bridge"
        try:
            return diagnose(
                src, args.stock, years, args.fs_div, mapping, kind=kind, now=when, log=out
            )
        except NoStatements as e:
            err(str(e))
            return 2
    try:
        inputs = collect_inputs(
            src,
            args.stock,
            years,
            args.fs_div,
            mapping,
            now=when,
            log=log,
            orders=not args.no_orders,
        )
    except NoStatements as e:
        err(str(e))
        return 2
    path = Path(args.out or f"out/{inputs.corp_name}_{inputs.stock}_재무리포트.xlsx")
    path.parent.mkdir(parents=True, exist_ok=True)
    build_workbook(str(path), **inputs.workbook_kwargs(), generated_at=when)
    out(f"\n✔ 생성 완료: {path}")
    if inputs.warnings:
        out("  경고:")
        for w in inputs.warnings:
            out(f"   ⚠ {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
