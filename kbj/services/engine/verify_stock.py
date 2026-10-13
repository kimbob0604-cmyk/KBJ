"""실데이터 완료 검증 CLI — 종목 상세가 관심종목에서 오류 없이 나오는가(PLAN §8 P4).

`python -m kbj.services.engine.verify_stock --watchlist main [--json] [--show-codes]`

docs/p4_design.md §0.3·§8.8 체크리스트 #37, ADR 0016 §2-6('오류 없이'의 정의). 키를 받은 뒤 실 DB
(로그인 API 와 같은 저장소 — `kbj.store.repos.pg_repos`)로 페이지 8 라우트 14개 × 관심종목을
**프로세스 안에서** 부른다. 네트워크·로그인 세션은 쓰지 않는다 — 운영 API 와 같은 앱을 만들고 세션
의존성만 이 검사용 값으로 바꾼다(외부 원천 호출 없음 — API 계약 ⑩ 그대로).

판정(칸 = 종목 × 라우트)
- 통과: 200(봉투 모델 검증 통과·source·as_of·quality 있음·JSON 에 NaN/Infinity 없음 — 적용
  불가는 `data.applicable=false` + `na_reason`), 404 `no_data`(아직 수집 안 됨), 404
  `unknown_code`(유니버스 밖). 엑셀(`report.xlsx`)은 200 + xlsx 형식 또는 404 `no_data`.
- 실패: 5xx, 그 밖의 상태·코드, 봉투 검증 실패, NaN/Infinity.

출력은 **건수만**: 라우트별 결과 분포(ok·na:<사유>·no_data·unknown_code)와 실패 칸 수(사유별).
값은 내지 않는다. 관심종목은 개인 데이터라 종목코드도 기본은 숨기고, `--show-codes` 를 주면
**실패한 칸의** 종목코드만 낸다(VM 화면에서 원인 찾기용 — `docs/probe_results.md` 에는
옮기지 않는다).

종료 코드: 0 = 실패 칸 0, 1 = 실패 칸 있음(또는 관심종목 0개 — 검증할 것이 없다), 2 = 오류
(설정·DB·Redis).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import IO, Any, Final

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

from kbj.core.quality import Quality
from kbj.services.api.auth import SessionData
from kbj.services.api.deps import require_session
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.filings import Disclosures, Earnings, Insiders, Overhang
from kbj.services.api.models.fin import Financials, ReportView, Segments
from kbj.services.api.models.flows import StockFlowDetail
from kbj.services.api.models.stock import Consensus, PriceChart, Scenario, StockHeader, Valuation

__all__ = [
    "BASE",
    "ROUTES",
    "Cell",
    "Report",
    "RouteSpec",
    "classify",
    "main",
    "verify_app",
]

BASE: Final = "http://verify-stock.invalid"  # 프로세스 안 — 이 주소로 나가는 연결은 없다
XLSX: Final = "application/vnd.openxmlformats"
PASS: Final = frozenset({"no_data", "unknown_code"})


@dataclass(frozen=True)
class RouteSpec:
    """페이지 8 라우트 하나. `model` 이 None 이면 내려받기(엑셀)."""

    name: str
    path: str  # `{code}` 자리
    model: type[BaseModel] | None


# 페이지 8 라우트 14개(docs/p4_design.md §5.3 — `/api/stock/{code}/*` 13개 + P3 수급)
ROUTES: Final[tuple[RouteSpec, ...]] = (
    RouteSpec("header", "/api/stock/{code}/header", StockHeader),
    RouteSpec("chart", "/api/stock/{code}/chart", PriceChart),
    RouteSpec("financials", "/api/stock/{code}/financials", Financials),
    RouteSpec("report", "/api/stock/{code}/report", ReportView),
    RouteSpec("report_xlsx", "/api/stock/{code}/report.xlsx", None),
    RouteSpec("segments", "/api/stock/{code}/segments", Segments),
    RouteSpec("consensus", "/api/stock/{code}/consensus", Consensus),
    RouteSpec("valuation", "/api/stock/{code}/valuation", Valuation),
    RouteSpec("scenario", "/api/stock/{code}/scenario", Scenario),
    RouteSpec("overhang", "/api/stock/{code}/overhang", Overhang),
    RouteSpec("insiders", "/api/stock/{code}/insiders", Insiders),
    RouteSpec("disclosures", "/api/stock/{code}/disclosures", Disclosures),
    RouteSpec("earnings", "/api/stock/{code}/earnings", Earnings),
    RouteSpec("flows", "/api/flows/stock/{code}", StockFlowDetail),
)


@dataclass(frozen=True)
class Cell:
    """한 칸의 결과. `problem` 이 None 이면 통과(값은 담지 않는다 — 사유 코드만)."""

    route: str
    code: str
    status: int
    outcome: str  # ok · na:<사유> · no_data · unknown_code · fail
    problem: str | None = None


@dataclass
class Report:
    cells: list[Cell] = field(default_factory=list[Cell])

    @property
    def failures(self) -> list[Cell]:
        return [c for c in self.cells if c.problem is not None]

    def by_route(self) -> dict[str, Counter[str]]:
        out: dict[str, Counter[str]] = {r.name: Counter() for r in ROUTES}
        for c in self.cells:
            out[c.route][c.outcome] += 1
        return out

    def summary(self, *, show_codes: bool = False) -> dict[str, Any]:
        """건수만 — 값 없음. `show_codes` 면 실패 칸의 (라우트, 종목코드)."""
        problems = Counter(c.problem for c in self.failures)
        out: dict[str, Any] = {
            "stocks": len({c.code for c in self.cells}),
            "routes": len(ROUTES),
            "cells": len(self.cells),
            "status": dict(sorted(Counter(str(c.status) for c in self.cells).items())),
            "by_route": {k: dict(sorted(v.items())) for k, v in self.by_route().items()},
            "failures": len(self.failures),
            "problems": dict(sorted(problems.items())),
        }
        if show_codes:
            out["failed_cells"] = sorted((c.route, c.code) for c in self.failures)
        return out


def _strict_json(text: str) -> Any:
    def bad(word: str) -> Any:
        raise ValueError(word)

    return json.loads(text, parse_constant=bad)


def classify(route: RouteSpec, status: int, headers: Mapping[str, str], text: str) -> Cell:
    """응답 하나를 칸 결과로(종목코드는 부르는 쪽이 채운다 — 여기서는 빈 문자열)."""
    if status >= 500:
        return Cell(route.name, "", status, "fail", f"http_{status}")
    if status == 404:
        try:
            code = str(_strict_json(text).get("code", ""))
        except (ValueError, AttributeError):
            return Cell(route.name, "", status, "fail", "error_body")
        if code in PASS:
            return Cell(route.name, "", status, code)
        return Cell(route.name, "", status, "fail", f"404_{code or 'unknown'}")
    if status != 200:
        return Cell(route.name, "", status, "fail", f"http_{status}")
    if route.model is None:  # 엑셀 내려받기
        ok = headers.get("content-type", "").startswith(XLSX)
        return Cell(route.name, "", status, "ok" if ok else "fail", None if ok else "not_xlsx")
    try:
        body = _strict_json(text)
    except ValueError:
        return Cell(route.name, "", status, "fail", "nan_or_bad_json")
    try:
        Envelope[route.model].model_validate(body)
    except ValidationError:
        return Cell(route.name, "", status, "fail", "envelope")
    if not (
        str(body.get("source", "")).strip()
        and body.get("as_of")
        and body.get("quality") in {q.value for q in Quality}
    ):
        return Cell(route.name, "", status, "fail", "envelope_meta")
    data = body.get("data")
    if isinstance(data, dict) and data.get("applicable") is False:
        return Cell(route.name, "", status, f"na:{data.get('na_reason') or 'unknown'}")
    return Cell(route.name, "", status, "ok")


def _session() -> SessionData:
    t = datetime.now(UTC).isoformat()
    return SessionData(user="verify_stock", csrf="-", created=t, last_seen=t)


def verify_app(app: FastAPI, codes: Sequence[str]) -> Report:
    """앱 하나에 종목 × 라우트를 부른다(세션 의존성만 바꾼다 — 원래 값은 되돌린다)."""
    before = app.dependency_overrides.get(require_session)
    app.dependency_overrides[require_session] = _session
    report = Report()
    try:
        with TestClient(app, base_url=BASE, raise_server_exceptions=False) as client:
            for code in codes:
                for route in ROUTES:
                    r = client.get(route.path.format(code=code))
                    cell = classify(route, r.status_code, r.headers, r.text)
                    report.cells.append(
                        Cell(cell.route, code, cell.status, cell.outcome, cell.problem)
                    )
    finally:
        if before is None:
            app.dependency_overrides.pop(require_session, None)
        else:
            app.dependency_overrides[require_session] = before
    return report


def _render(summary: Mapping[str, Any], out: IO[str]) -> None:
    out.write(
        f"종목 상세 검증 — 관심종목 {summary['stocks']}개 × 라우트 {summary['routes']}개 = "
        f"{summary['cells']}칸\n"
    )
    for route, dist in summary["by_route"].items():
        parts = " · ".join(f"{k} {v}" for k, v in dist.items())
        out.write(f"  {route}: {parts}\n")
    probs = " · ".join(f"{k} {v}" for k, v in summary["problems"].items()) or "없음"
    out.write(f"실패 {summary['failures']}칸 ({probs})\n")
    for route, code in summary.get("failed_cells", []):
        out.write(f"  실패 칸: {route} {code}\n")


def _production() -> tuple[
    FastAPI, Callable[[str], list[str]]
]:  # pragma: no cover — 운영(DB·Redis)
    from kbj.config.settings import Settings
    from kbj.services.api.app import create_app
    from kbj.services.runtime.heartbeat import connect_redis
    from kbj.store.db import connect
    from kbj.store.repos import pg_repos

    settings = Settings()
    if settings.redis_url is None or settings.database_url is None:
        raise RuntimeError("KBJ_DATABASE_URL·KBJ_REDIS_URL 이 필요하다")
    repos = pg_repos(lambda: connect(settings, service="verify_stock"))
    app = create_app(
        settings, redis=connect_redis(settings.redis_url), repos=repos, static_dir=False
    )

    def watch(list_name: str) -> list[str]:
        return [i.code for i in repos.journal.watchlist(list_name)]

    return app, watch


def main(
    argv: Sequence[str] | None = None,
    *,
    out: IO[str] | None = None,
    err: IO[str] | None = None,
    build: Callable[[], tuple[FastAPI, Callable[[str], list[str]]]] | None = None,
) -> int:
    """CLI. `build` 는 시험이 (앱, 관심종목 읽기) 를 주입한다(없으면 운영 DB·Redis)."""
    o: IO[str] = out if out is not None else sys.stdout
    e_: IO[str] = err if err is not None else sys.stderr
    p = argparse.ArgumentParser(prog="python -m kbj.services.engine.verify_stock")
    p.add_argument("--watchlist", default="main", help="관심종목 목록 이름(기본 main)")
    p.add_argument("--json", action="store_true", help="건수 요약을 JSON 으로")
    p.add_argument("--show-codes", action="store_true", help="실패한 칸의 종목코드도 낸다(개인)")
    args = p.parse_args(argv)
    try:
        app, watch = (build or _production)()
        codes = sorted(set(watch(args.watchlist)))
    except Exception as e:  # 설정·DB·Redis — 사유는 형식 이름만(접속 문자열을 싣지 않는다)
        e_.write(f"오류: {type(e).__name__} — 설정·DB·Redis 를 확인한다\n")
        return 2
    if not codes:
        e_.write(
            f"관심종목 '{args.watchlist}' 이 비었다 — 가져오기 CLI(kbj.services.watchlist) 먼저\n"
        )
        return 1
    try:
        report = verify_app(app, codes)
    except Exception as e:  # 저장소 오류 등 — 라우트 오류는 칸 결과(5xx)로 잡힌다
        e_.write(f"오류: {type(e).__name__}\n")
        return 2
    summary = report.summary(show_codes=args.show_codes)
    if args.json:
        o.write(json.dumps(summary, ensure_ascii=False, sort_keys=True) + "\n")
    else:
        _render(summary, o)
    return 0 if not report.failures else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
