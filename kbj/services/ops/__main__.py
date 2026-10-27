"""`python -m kbj.services.ops check [--since 24h] [--json]` · `week [--end YYYY-MM-DD] [--json]` ·
`errors [--since 7d] [--json]`(docs/p9_design.md §6.2, ADR 0037, docs/ops_error_catalog.md).

- check: 지금 하트비트 + 최근 기록(기본 24시간)으로 OK/WARN/FAIL 표, 표 크기 상위 10·DB 크기·디스크
  여유·마지막 백업. 종료 코드 0(OK·WARN) / 1(FAIL) / 2(설정·접속 오류).
- week: end(기본 어제 KST)를 끝으로 7일 일자별 판정 + 최종 '통과/실패'. 종료 코드 0(통과) / 1(실패·
  판정 불가) / 2(설정·접속 오류).
- errors: 최근 기록(기본 7일)을 작업마다 묶은 실패·시간 초과·건너뜀(대기) 수·마지막 시각·
  사유 — 맥에서 찍어 오류 카탈로그를 실데이터로 고친다. 종료 코드 0(목록) / 2(설정·접속 오류).
- DB·Redis 는 **읽기만**. 출력은 이름·상태·개수·시각뿐 — 키·접속 문자열·시세가 없다(오류 문장도
  가린다).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import IO, Any

from kbj.core.calendar import TradingCalendar
from kbj.core.masking import mask_text
from kbj.core.time import KST, utcnow
from kbj.services.ops.check import (
    CheckReport,
    DiskUsage,
    HeartbeatAge,
    WeekReport,
    run_check,
    run_week,
)
from kbj.services.ops.errors import errors_json, job_errors, render_errors
from kbj.services.ops.source import Expect, ExpectError, OpsSource, load_expect
from kbj.store.db import StoreError

SERVICE = "ops"
_DSN_AUTH = re.compile(r"://[^/@\s]*@")


def _safe(text: str) -> str:
    """오류 문장 가리기 — 형태(키·토큰) + 접속 문자열의 사용자·비밀번호 칸."""
    return mask_text(_DSN_AUTH.sub("://***@", text))


@dataclass(frozen=True)
class Deps:
    """CLI 가 쓰는 것 — 운영은 Postgres·Redis, 시험은 메모리·가짜."""

    source: OpsSource
    expect: Expect
    cal: TradingCalendar
    heartbeat_age: HeartbeatAge
    disk: DiskUsage | None = None


def _pg_deps() -> Deps:  # pragma: no cover — 운영 진입점(Postgres·Redis 필요)
    from kbj.config.files import config_path
    from kbj.config.settings import Settings
    from kbj.services.ops.source import PgOpsSource
    from kbj.services.runtime.heartbeat import connect_redis, heartbeat_age
    from kbj.store.db import connect

    settings = Settings()
    if settings.database_url is None or settings.redis_url is None:
        raise StoreError("KBJ_DATABASE_URL·KBJ_REDIS_URL 이 필요하다")
    redis = connect_redis(settings.redis_url)
    data_dir = settings.data_dir

    def conn() -> Any:
        return connect(settings, service=SERVICE)

    def age(service: str, now: datetime) -> float | None:
        return heartbeat_age(redis, service, now)

    def disk() -> tuple[int, int] | None:
        try:
            du = shutil.disk_usage(data_dir if data_dir.exists() else ".")
        except OSError:
            return None
        return du.free, du.total

    return Deps(
        PgOpsSource(conn),
        load_expect(config_path("jobs.yaml", settings=settings)),
        TradingCalendar.default(),
        age,
        disk,
    )


_SINCE = re.compile(r"(\d{1,3})([hd])")


def _since(text: str) -> timedelta:
    m = _SINCE.fullmatch(text)
    if m is None:
        raise argparse.ArgumentTypeError("예: 24h, 2d")
    n = int(m.group(1))
    if n < 1:
        raise argparse.ArgumentTypeError("1 이상")
    return timedelta(hours=n) if m.group(2) == "h" else timedelta(days=n)


def _bytes(n: int | None) -> str:
    if n is None:
        return "-"
    v = float(n)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if v < 1024:
            return f"{v:.0f} {unit}" if unit == "B" else f"{v:.1f} {unit}"
        v /= 1024
    return f"{v:.1f} TiB"


def check_json(r: CheckReport) -> dict[str, Any]:
    return {
        "now": r.now.isoformat(),
        "since": r.since.isoformat(),
        "level": r.level,
        "services": [
            {
                "name": s.name,
                "beat": s.beat,
                "heartbeat_s": s.heartbeat_s,
                "age_s": None if s.age_s is None else round(s.age_s),
                "level": s.level,
            }
            for s in r.services
        ],
        "items": [
            {
                "key": i.key,
                "title": i.title,
                "level": i.level,
                "value": i.value,
                "verdict": i.verdict,
            }
            for i in r.items
        ],
        "tables": [{"schema": t.schema, "table": t.table, "bytes": t.bytes} for t in r.tables],
        "database_bytes": r.database_bytes,
    }


def week_json(r: WeekReport) -> dict[str, Any]:
    return {
        "start": r.start.isoformat(),
        "end": r.end.isoformat(),
        "passed": r.passed,
        "trading_days": r.trading_days,
        "reason": r.reason,
        "days": [
            {
                "day": d.day.isoformat(),
                "trading": d.trading,
                "long_gaps": d.long_gaps,
                "final_failures": d.final_failures,
                "retried_ok": d.retried_ok,
                "morning_sent": d.morning_sent,
                "closing_sent": d.closing_sent,
                "brief_failed": d.brief_failed,
                "passed": d.passed,
            }
            for d in r.days
        ],
    }


def render_check(r: CheckReport) -> str:
    lines = [
        f"운영 점검 {r.now.astimezone(KST).isoformat(timespec='minutes')} "
        f"(기록 {r.since.astimezone(KST).isoformat(timespec='minutes')} 부터) — {r.level}",
        "",
        "서비스            하트비트   기준   상태",
    ]
    for s in r.services:
        age = "없음" if s.age_s is None else f"{s.age_s:.0f}초"
        lines.append(f"{s.name:<17} {age:>8} {s.heartbeat_s:>5}초  {s.level}")
    lines += ["", "항목                          상태  값"]
    for i in r.items:  # 판정에 들지 않는 항목은 제목에 '(참고)'
        lines.append(f"{i.title:<28} {i.level:<5} {i.value}")
    lines += ["", f"DB 크기 {_bytes(r.database_bytes)} — 표 크기 상위 {len(r.tables)}"]
    for t in r.tables:
        lines.append(f"  {t.schema}.{t.table:<40} {_bytes(t.bytes)}")
    return "\n".join(lines) + "\n"


def render_week(r: WeekReport) -> str:
    lines = [
        f"1주 무중단 판정 {r.start.isoformat()} ~ {r.end.isoformat()} "
        f"(거래일 {r.trading_days}) — {'통과' if r.passed else '실패'}",
        "",
        "날짜        거래일 끊김>10분 최종실패 재시도성공 아침 마감 발송실패 판정",
    ]
    for d in r.days:
        lines.append(
            f"{d.day.isoformat()}  {'예' if d.trading else '-':<5} {d.long_gaps:>9}"
            f" {d.final_failures:>8}"
            f" {d.retried_ok:>10} {d.morning_sent:>4} {d.closing_sent:>4} {d.brief_failed:>8}"
            f" {'통과' if d.passed else '실패'}"
        )
    if r.reason:
        lines += ["", r.reason]
    return "\n".join(lines) + "\n"


def _dump(obj: dict[str, Any]) -> str:
    return json.dumps(obj, ensure_ascii=False) + "\n"


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m kbj.services.ops",
        description="운영 점검(읽기 전용) — check: 지금 상태, week: 1주 무중단 판정(ADR 0037)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="지금 하트비트 + 최근 기록 OK/WARN/FAIL(종료 코드 0/1)")
    c.add_argument("--since", type=_since, default=timedelta(hours=24), help="기록 구간(24h·2d)")
    c.add_argument("--json", action="store_true", help="JSON 으로")
    w = sub.add_parser("week", help="7일 일자별 판정 + 통과/실패(종료 코드 0/1)")
    w.add_argument("--end", type=date.fromisoformat, default=None, help="마지막 날(기본 어제 KST)")
    w.add_argument("--json", action="store_true", help="JSON 으로")
    x = sub.add_parser("errors", help="최근 작업 실패·시간 초과·건너뜀을 작업마다(종료 코드 0)")
    x.add_argument("--since", type=_since, default=timedelta(days=7), help="기록 구간(24h·7d)")
    x.add_argument("--json", action="store_true", help="JSON 으로")
    b = sub.add_parser("budget", help="오늘(또는 --day) 출처별 호출 예산·작업별 셈")
    b.add_argument("--day", type=date.fromisoformat, default=None, help="날짜(기본 오늘 KST)")
    return p


def main(
    argv: Sequence[str] | None = None,
    *,
    deps: Callable[[], Deps] = _pg_deps,
    now: Callable[[], datetime] = utcnow,
    out: IO[str] | None = None,
    err: IO[str] | None = None,
) -> int:
    o: IO[str] = out or sys.stdout
    e_: IO[str] = err or sys.stderr
    args = _parser().parse_args(argv)
    if args.cmd == "budget":
        return _budget(args.day or now().astimezone(KST).date(), o, e_)
    try:
        d = deps()
        at = now()
        if args.cmd == "check":
            r = run_check(
                d.source, d.expect, d.cal, d.heartbeat_age, at, since=args.since, disk=d.disk
            )
            o.write(_dump(check_json(r)) if args.json else render_check(r))
            return r.exit_code
        if args.cmd == "errors":
            start = at - args.since
            items = job_errors(d.source.job_runs(start, at))
            o.write(
                _dump(errors_json(items, start, at))
                if args.json
                else render_errors(items, start, at)
            )
            return 0
        end: date = args.end or (at.astimezone(KST).date() - timedelta(days=1))
        w = run_week(d.source, d.expect, d.cal, end)
        o.write(_dump(week_json(w)) if args.json else render_week(w))
        return w.exit_code
    except (StoreError, ExpectError, OSError) as e:
        print(f"점검 실패: {_safe(str(e))}", file=e_)
        return 2
    except Exception as e:  # Redis 등 — 종류만(접속 정보가 문장에 섞일 수 있다)
        print(f"점검 실패: {type(e).__name__}: {_safe(str(e))}", file=e_)
        return 2


def _budget(day: date, out: IO[str], err: IO[str]) -> int:  # pragma: no cover — Redis 필요
    from kbj.config.settings import Settings
    from kbj.services.ops.budget_report import budget_lines
    from kbj.services.runtime.heartbeat import connect_redis

    settings = Settings()
    if settings.redis_url is None:
        print("KBJ_REDIS_URL 이 필요하다", file=err)
        return 2
    try:
        lines = budget_lines(connect_redis(settings.redis_url), day)
    except Exception as e:
        print(f"점검 실패: {type(e).__name__}: {_safe(str(e))}", file=err)
        return 2
    print("\n".join(lines), file=out)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
