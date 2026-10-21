"""공시 저장소 — 0010 `pub_filings.*`(공개 — 원천 DART) 읽기·쓰기(docs/p4_design.md
§1.1·§3.4·§4.1~§4.4).

공개 작업(`filings.dart_feed`·`filings.company_profile`·`filings.derive`·`fin.quarterly`)이 쓴다. 이
모듈은
`pub_*` 표만 다룬다 — 공개 작업 경계(계약 ⑬)를 지키려고 로그인 저장소(consensus·journal)를 import
하지
않는다. 공개 작업은 `kbj.store.repos` 패키지 이름이 아니라 이 모듈(또는
`kbj.store.repos.public`)에서
가져온다(§2 '⑬ 을 지키는 import 모양').

규칙
- 공시 목록 upsert 는 목록에서 온 칸만 덮는다. `first_seen_at`(처음 본 시각)·파싱 상태·키워드
  점수·재무 적재
  시각은 처음 값을 지킨다(`keep`) — 같은 공시를 다시 훑어도(07:00 전 영업일 다시 훑기) 상태가 되돌지
  않는다.
- `pending` = 파싱 대기(pending·failed 이고 시도 < 상한) — 접수번호 오름차순. `mark_parsed(…,
  'failed')` 는
  시도 수를 하나 올리고 그 수를 돌려준다(3회면 'na' + 사유로 바꾸는 판단은 처리기 F 몫 — §4.1).
- 정정 공시는 새 접수번호의 새 행이다. 읽는 쪽이 가장 늦은 접수번호를 쓴다(이 저장소는 고르지
  않는다).
- 파생 표(overhang_state·summary·insider_window·earnings_schedule)는 (회사, as_of)·(회사들) 단위로
  **통째로
  바꾼다**(같은 as_of 재실행 멱등 — 사라진 행이 남지 않게).
- `corp_index` 는 P2 `pub_filings.corp_code`(filings.corp_code 가 매일 갈아 넣는 표)를 읽기만 한다.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Any, Final, Protocol

from kbj.core.fin_rows import (
    PARSE_STATES,
    BuybackEvent,
    CorpProfile,
    DisclosureRow,
    InsiderReport,
    InsiderWindow,
    LargestHolder,
    MajorHolderReport,
    OverhangAdjust,
    OverhangEvent,
    OverhangState,
    OverhangSummary,
    PrelimRow,
    ScheduleRow,
    ShareCount,
)
from kbj.store.repos._common import ConnFactory, require_aware, require_loaded_by
from kbj.store.repos._table import MemoryTable, Table, delete, select, upsert

__all__ = [
    "FilingsRepo",
    "MemoryFilingsRepo",
    "PgFilingsRepo",
]

_RECEIVED: Final = ("received_at", "loaded_by")
_COMPUTED: Final = ("computed_at", "loaded_by")

DISCLOSURE: Final = Table(
    "pub_filings.disclosure",
    DisclosureRow,
    key=("rcept_no",),
    extra=_RECEIVED,
    keep=frozenset({
        "first_seen_at", "kw_score", "kw_matched", "parse_state", "parse_tries", "parse_note",
        "fin_loaded_at",
    }),
)  # fmt: skip
PROFILE: Final = Table("pub_filings.corp_profile", CorpProfile, key=("corp_code",), extra=_RECEIVED)
PRELIM: Final = Table(
    "pub_filings.earnings_prelim",
    PrelimRow,
    key=("rcept_no",),
    extra=_RECEIVED,
    json_cols=frozenset({"yoy_reported", "notes"}),
)
SCHEDULE: Final = Table(
    "pub_filings.earnings_schedule",
    ScheduleRow,
    key=("corp_code", "period_end", "kind"),
    extra=_COMPUTED,
)
OVERHANG_EVENT: Final = Table(
    "pub_filings.overhang_event",
    OverhangEvent,
    key=("rcept_no",),
    extra=_RECEIVED,
    json_cols=frozenset({"notes"}),
)
OVERHANG_ADJUST: Final = Table(
    "pub_filings.overhang_adjust", OverhangAdjust, key=("rcept_no",), extra=_RECEIVED
)
OVERHANG_STATE: Final = Table(
    "pub_filings.overhang_state",
    OverhangState,
    key=("corp_code", "as_of", "rcept_no"),
    extra=_COMPUTED,
    json_cols=frozenset({"notes"}),
)
OVERHANG_SUMMARY: Final = Table(
    "pub_filings.overhang_summary",
    OverhangSummary,
    key=("corp_code", "as_of"),
    extra=_COMPUTED,
    json_cols=frozenset({"notes"}),
)
INSIDER: Final = Table(
    "pub_filings.insider_report", InsiderReport, key=("rcept_no",), extra=_RECEIVED
)
MAJOR: Final = Table(
    "pub_filings.major_holder_report", MajorHolderReport, key=("rcept_no",), extra=_RECEIVED
)
WINDOW: Final = Table(
    "pub_filings.insider_window", InsiderWindow, key=("corp_code", "as_of", "days"), extra=_COMPUTED
)
BUYBACK: Final = Table(
    "pub_filings.buyback_event",
    BuybackEvent,
    key=("rcept_no",),
    extra=_RECEIVED,
    rename={"start": "start_date", "end": "end_date"},
)
SHARE_COUNT: Final = Table(
    "pub_filings.share_count",
    ShareCount,
    key=("corp_code", "period_end", "rcept_no"),
    extra=_RECEIVED,
)
LARGEST: Final = Table(
    "pub_filings.largest_holder",
    LargestHolder,
    key=("corp_code", "period_end", "rcept_no"),
    extra=_RECEIVED,
    json_cols=frozenset({"holders"}),
)


class FilingsRepo(Protocol):
    # ── 공시 목록 ──
    def upsert_disclosures(
        self, rows: Sequence[DisclosureRow], *, loaded_by: str, now: datetime
    ) -> int:
        """목록에서 온 칸만 덮는다(처음 본 시각·파싱 상태·점수·재무 적재 시각은 처음 값)."""
        ...

    def seen(self, rcept_nos: Iterable[str]) -> set[str]:
        """이미 있는 접수번호(증분 멈춤 — §4.1)."""
        ...

    def disclosure(self, rcept_no: str) -> DisclosureRow | None: ...

    def disclosures(
        self,
        *,
        start: date,
        end: date,
        stock_code: str | None = None,
        corp_code: str | None = None,
        kinds: Collection[str] | None = None,
        limit: int | None = None,
    ) -> list[DisclosureRow]:
        """rcept_dt ∈ [start, end] — 접수번호 내림차순(최근 것부터)."""
        ...

    def pending(
        self,
        *,
        kinds: Collection[str] | None = None,
        max_tries: int = 3,
        limit: int = 500,
        now: datetime | None = None,
        wait_min: Sequence[int] = (),
    ) -> list[DisclosureRow]:
        """파싱 대기(pending·failed 이고 parse_tries < max_tries) — 접수번호 오름차순. `now` 를 주면
        n 번 실패한 행은 first_seen_at + wait_min[n-1] 분 ≤ now 일 때만(값이 모자라면 마지막 값)."""
        ...

    def mark_parsed(
        self, rcept_no: str, state: str, *, note: str | None = None, now: datetime
    ) -> int:
        """파싱 상태를 바꾼다. 'failed' 면 시도 수 + 1. 바꾼 뒤의 시도 수(없는 접수번호는
        KeyError)."""
        ...

    def set_scores(self, scores: Mapping[str, tuple[int, Sequence[str]]]) -> int:
        """{접수번호: (키워드 점수, 맞은 키워드)} — filings.derive. 바꾼 행 수."""
        ...

    def fin_unloaded(
        self, *, kinds: Collection[str] = ("periodic",), limit: int = 500
    ) -> list[DisclosureRow]:
        """재무를 아직 적재하지 않은 공시(fin_loaded_at 없음) — 접수번호 오름차순(fin.quarterly)."""
        ...

    def mark_fin_loaded(self, rcept_nos: Iterable[str], *, now: datetime) -> int: ...

    def changed_corps(self, since: datetime) -> set[str]:
        """first_seen_at ≥ since 인 공시가 있는 회사(filings.derive '그날 바뀐 회사')."""
        ...

    def corp_index(self) -> dict[str, tuple[str, str]]:
        """{종목코드: (corp_code, 회사명)} — P2 pub_filings.corp_code(상장사만, 읽기만)."""
        ...

    # ── 기업개황 ──
    def upsert_profiles(
        self, rows: Sequence[CorpProfile], *, loaded_by: str, now: datetime
    ) -> int: ...

    def profiles(self, corp_codes: Collection[str] | None = None) -> dict[str, CorpProfile]: ...
    def profiles_received_since(self, since: datetime) -> set[str]:
        """`since` 뒤에 기업개황을 받은 corp_code(같은 주에 다시 받지 않게 — company_profile)."""
        ...

    # ── 잠정실적·실적 일정 ──
    def put_prelims(self, rows: Sequence[PrelimRow], *, loaded_by: str, now: datetime) -> int: ...

    def prelim(self, rcept_no: str) -> PrelimRow | None: ...

    def prelims(self, *, start: date, end: date, corp_code: str | None = None) -> list[PrelimRow]:
        """rcept_dt ∈ [start, end] — 접수번호 오름차순."""
        ...

    def replace_schedule(
        self,
        corp_codes: Collection[str],
        rows: Sequence[ScheduleRow],
        *,
        loaded_by: str,
        now: datetime,
    ) -> int:
        """그 회사들의 일정 행을 통째로 바꾼다(rows 는 그 회사들 것만)."""
        ...

    def schedule(
        self, *, start: date, end: date, corp_code: str | None = None
    ) -> list[ScheduleRow]:
        """planned_date ∈ [start, end] — 날짜·회사 순."""
        ...

    # ── 오버행 ──
    def put_overhang_events(
        self, rows: Sequence[OverhangEvent], *, loaded_by: str, now: datetime
    ) -> int: ...

    def overhang_events(
        self, corp_code: str | None = None, *, start: date | None = None, end: date | None = None
    ) -> list[OverhangEvent]:
        """회사(또는 전체)의 결정 공시 — rcept_dt 범위(주면), 접수번호 오름차순."""
        ...

    def put_overhang_adjusts(
        self, rows: Sequence[OverhangAdjust], *, loaded_by: str, now: datetime
    ) -> int: ...

    def overhang_adjusts(self, corp_code: str) -> list[OverhangAdjust]: ...

    def put_overhang_state(
        self,
        corp_code: str,
        as_of: date,
        states: Sequence[OverhangState],
        summary: OverhangSummary | None,
        *,
        loaded_by: str,
        now: datetime,
    ) -> int:
        """(회사, as_of) 의 상태·요약을 통째로 바꾼다."""
        ...

    def overhang_state(self, corp_code: str, as_of: date | None = None) -> list[OverhangState]:
        """as_of 를 안 주면 가장 최근 as_of 의 상태."""
        ...

    def overhang_summary(
        self, corp_code: str, as_of: date | None = None
    ) -> OverhangSummary | None: ...

    def overhang_summaries(self, as_of: date) -> dict[str, OverhangSummary]:
        """그날 요약 {corp_code: 요약}(공개 내보내기·페이지 5)."""
        ...

    # ── 내부자·대량보유 ──
    def put_insider_reports(
        self, rows: Sequence[InsiderReport], *, loaded_by: str, now: datetime
    ) -> int: ...

    def insider_reports(
        self, corp_code: str, *, start: date | None = None, end: date | None = None
    ) -> list[InsiderReport]: ...

    def put_major_holder_reports(
        self, rows: Sequence[MajorHolderReport], *, loaded_by: str, now: datetime
    ) -> int: ...

    def major_holder_reports(
        self, corp_code: str, *, start: date | None = None, end: date | None = None
    ) -> list[MajorHolderReport]: ...

    def put_insider_windows(
        self,
        corp_code: str,
        as_of: date,
        rows: Sequence[InsiderWindow],
        *,
        loaded_by: str,
        now: datetime,
    ) -> int:
        """(회사, as_of) 의 창 행을 통째로 바꾼다."""
        ...

    def insider_windows(self, corp_code: str, as_of: date | None = None) -> list[InsiderWindow]:
        """as_of 를 안 주면 가장 최근 as_of — days 오름차순."""
        ...

    def insider_windows_on(self, as_of: date, days: int) -> list[InsiderWindow]:
        """그날·그 창의 모든 회사(페이지 5 내부자 큰 변동)."""
        ...

    # ── 자기주식·주식총수 ──
    def put_buybacks(
        self, rows: Sequence[BuybackEvent], *, loaded_by: str, now: datetime
    ) -> int: ...

    def buybacks(self, corp_code: str) -> list[BuybackEvent]: ...

    def put_share_counts(
        self, rows: Sequence[ShareCount], *, loaded_by: str, now: datetime
    ) -> int: ...

    def share_counts(self, corp_code: str) -> list[ShareCount]:
        """period_end·접수번호 오름차순."""
        ...

    def latest_share_count(self, corp_code: str, upto: date | None = None) -> ShareCount | None:
        """가장 최근(period_end, 접수번호) 행. `upto` 를 주면 rcept_dt ≤ upto 인 것만(PIT)."""
        ...

    # ── 최대주주 현황 ──
    def put_largest_holders(
        self, rows: Sequence[LargestHolder], *, loaded_by: str, now: datetime
    ) -> int: ...

    def latest_largest_holder(
        self, corp_code: str, upto: date | None = None
    ) -> LargestHolder | None:
        """가장 최근(period_end, 접수번호) 행. `upto` 를 주면 rcept_dt ≤ upto 인 것만(PIT)."""
        ...


def _check(loaded_by: str, now: datetime) -> dict[str, object]:
    require_loaded_by(loaded_by)
    require_aware("now", now)
    return {"received_at": now, "computed_at": now, "loaded_by": loaded_by}


def _ext(table: Table[Any], base: Mapping[str, object]) -> dict[str, object]:
    return {c: base[c] for c in table.extra}


def _check_state(state: str) -> None:
    if state not in PARSE_STATES:
        raise ValueError(f"parse_state 는 {PARSE_STATES} 중 하나: {state!r}")


def _same_corp(corp_code: str, rows: Iterable[Any]) -> None:
    for r in rows:
        if r.corp_code != corp_code:
            raise ValueError(f"다른 회사의 행이 섞였다: {r.corp_code} ≠ {corp_code}")


def _same_day(as_of: date, rows: Iterable[Any]) -> None:
    for r in rows:
        if r.as_of != as_of:
            raise ValueError(f"다른 날짜의 행이 섞였다: {r.as_of} ≠ {as_of}")


# ── Postgres ───────────────────────────────────────────────────────────────────────────


class PgFilingsRepo:
    """`FilingsRepo` 의 Postgres 구현(연결 공장을 부를 때마다 짧게 연다)."""

    def __init__(self, conn_factory: ConnFactory) -> None:
        self._connect = conn_factory

    def __repr__(self) -> str:
        return "PgFilingsRepo()"

    def _put(self, table: Table[Any], rows: Sequence[Any], loaded_by: str, now: datetime) -> int:
        base = _check(loaded_by, now)
        if not rows:
            return 0
        with self._connect() as conn:
            return upsert(conn, table, rows, _ext(table, base))

    def _get(self, table: Table[Any], where: str, params: Sequence[object], order: str = "",
             limit: int | None = None) -> list[Any]:  # fmt: skip
        with self._connect() as conn:
            return select(conn, table, where, params, order, limit)

    # 공시 목록
    def upsert_disclosures(
        self, rows: Sequence[DisclosureRow], *, loaded_by: str, now: datetime
    ) -> int:
        return self._put(DISCLOSURE, rows, loaded_by, now)

    def seen(self, rcept_nos: Iterable[str]) -> set[str]:
        wanted = sorted(set(rcept_nos))
        if not wanted:
            return set()
        with self._connect() as conn:
            rows = conn.execute(
                b"SELECT rcept_no FROM pub_filings.disclosure WHERE rcept_no = ANY(%s::text[])",
                (wanted,),
            ).fetchall()
        return {r[0] for r in rows}

    def disclosure(self, rcept_no: str) -> DisclosureRow | None:
        got = self._get(DISCLOSURE, "rcept_no = %s", (rcept_no,))
        return got[0] if got else None

    def disclosures(
        self,
        *,
        start: date,
        end: date,
        stock_code: str | None = None,
        corp_code: str | None = None,
        kinds: Collection[str] | None = None,
        limit: int | None = None,
    ) -> list[DisclosureRow]:
        kind_list = None if kinds is None else sorted(set(kinds))
        return self._get(
            DISCLOSURE,
            "rcept_dt BETWEEN %s AND %s AND (%s::text IS NULL OR stock_code = %s) "
            "AND (%s::text IS NULL OR corp_code = %s) "
            "AND (%s::text[] IS NULL OR kind = ANY(%s::text[]))",
            (start, end, stock_code, stock_code, corp_code, corp_code, kind_list, kind_list),
            "rcept_no DESC",
            limit,
        )

    def pending(
        self,
        *,
        kinds: Collection[str] | None = None,
        max_tries: int = 3,
        limit: int = 500,
        now: datetime | None = None,
        wait_min: Sequence[int] = (),
    ) -> list[DisclosureRow]:
        kind_list = None if kinds is None else sorted(set(kinds))
        if now is not None:
            require_aware("now", now)
        waits = [int(x) for x in wait_min]
        return self._get(
            DISCLOSURE,
            "parse_state IN ('pending', 'failed') AND parse_tries < %s "
            "AND (%s::text[] IS NULL OR kind = ANY(%s::text[])) "
            "AND (%s::timestamptz IS NULL OR parse_tries = 0 OR cardinality(%s::int[]) = 0 "
            "     OR first_seen_at + make_interval(mins => (%s::int[])"
            "[LEAST(parse_tries, cardinality(%s::int[]))]) <= %s::timestamptz)",
            (max_tries, kind_list, kind_list, now, waits, waits, waits, now),
            "rcept_no",
            limit,
        )

    def mark_parsed(
        self, rcept_no: str, state: str, *, note: str | None = None, now: datetime
    ) -> int:
        _check_state(state)
        require_aware("now", now)
        with self._connect() as conn:
            r = conn.execute(
                b"UPDATE pub_filings.disclosure SET parse_state = %s, parse_note = %s, "
                b"parse_tries = parse_tries + (CASE WHEN %s = 'failed' THEN 1 ELSE 0 END) "
                b"WHERE rcept_no = %s RETURNING parse_tries",
                (state, note, state, rcept_no),
            ).fetchone()
        if r is None:
            raise KeyError(f"없는 접수번호: {rcept_no}")
        return int(r[0])

    def set_scores(self, scores: Mapping[str, tuple[int, Sequence[str]]]) -> int:
        if not scores:
            return 0
        n = 0
        with self._connect() as conn, conn.cursor() as cur:
            for rc, (score, matched) in sorted(scores.items()):
                cur.execute(
                    b"UPDATE pub_filings.disclosure SET kw_score = %s, kw_matched = %s "
                    b"WHERE rcept_no = %s",
                    (score, list(matched), rc),
                )
                n += cur.rowcount
        return n

    def fin_unloaded(
        self, *, kinds: Collection[str] = ("periodic",), limit: int = 500
    ) -> list[DisclosureRow]:
        return self._get(
            DISCLOSURE,
            "fin_loaded_at IS NULL AND kind = ANY(%s::text[])",
            (sorted(set(kinds)),),
            "rcept_no",
            limit,
        )

    def mark_fin_loaded(self, rcept_nos: Iterable[str], *, now: datetime) -> int:
        require_aware("now", now)
        wanted = sorted(set(rcept_nos))
        if not wanted:
            return 0
        with self._connect() as conn:
            cur = conn.execute(
                b"UPDATE pub_filings.disclosure SET fin_loaded_at = %s "
                b"WHERE rcept_no = ANY(%s::text[])",
                (now, wanted),
            )
            return cur.rowcount

    def changed_corps(self, since: datetime) -> set[str]:
        require_aware("since", since)
        with self._connect() as conn:
            rows = conn.execute(
                b"SELECT DISTINCT corp_code FROM pub_filings.disclosure WHERE first_seen_at >= %s",
                (since,),
            ).fetchall()
        return {r[0] for r in rows}

    def corp_index(self) -> dict[str, tuple[str, str]]:
        with self._connect() as conn:
            rows = conn.execute(
                b"SELECT stock_code, corp_code, corp_name FROM pub_filings.corp_code "
                b"WHERE stock_code IS NOT NULL ORDER BY stock_code"
            ).fetchall()
        return {r[0]: (r[1], r[2]) for r in rows}

    # 기업개황
    def upsert_profiles(self, rows: Sequence[CorpProfile], *, loaded_by: str, now: datetime) -> int:
        return self._put(PROFILE, rows, loaded_by, now)

    def profiles(self, corp_codes: Collection[str] | None = None) -> dict[str, CorpProfile]:
        wanted = None if corp_codes is None else sorted(set(corp_codes))
        if wanted == []:
            return {}
        rows: list[CorpProfile] = self._get(
            PROFILE, "(%s::text[] IS NULL OR corp_code = ANY(%s::text[]))", (wanted, wanted),
            "corp_code",
        )  # fmt: skip
        return {p.corp_code: p for p in rows}

    def profiles_received_since(self, since: datetime) -> set[str]:
        require_aware("since", since)
        with self._connect() as conn:
            rows = conn.execute(
                b"SELECT corp_code FROM pub_filings.corp_profile WHERE received_at >= %s", (since,)
            ).fetchall()
        return {r[0] for r in rows}

    # 잠정실적·일정
    def put_prelims(self, rows: Sequence[PrelimRow], *, loaded_by: str, now: datetime) -> int:
        return self._put(PRELIM, rows, loaded_by, now)

    def prelim(self, rcept_no: str) -> PrelimRow | None:
        got = self._get(PRELIM, "rcept_no = %s", (rcept_no,))
        return got[0] if got else None

    def prelims(self, *, start: date, end: date, corp_code: str | None = None) -> list[PrelimRow]:
        return self._get(
            PRELIM,
            "rcept_dt BETWEEN %s AND %s AND (%s::text IS NULL OR corp_code = %s)",
            (start, end, corp_code, corp_code),
            "rcept_no",
        )

    def replace_schedule(
        self,
        corp_codes: Collection[str],
        rows: Sequence[ScheduleRow],
        *,
        loaded_by: str,
        now: datetime,
    ) -> int:
        base = _check(loaded_by, now)
        corps = sorted(set(corp_codes))
        stray = [r.corp_code for r in rows if r.corp_code not in set(corps)]
        if stray:
            raise ValueError(f"바꿀 회사 목록 밖의 일정 행: {stray[:3]}")
        with self._connect() as conn, conn.transaction():
            delete(conn, SCHEDULE, "corp_code = ANY(%s::text[])", (corps,))
            return upsert(conn, SCHEDULE, rows, _ext(SCHEDULE, base))

    def schedule(
        self, *, start: date, end: date, corp_code: str | None = None
    ) -> list[ScheduleRow]:
        return self._get(
            SCHEDULE,
            "planned_date BETWEEN %s AND %s AND (%s::text IS NULL OR corp_code = %s)",
            (start, end, corp_code, corp_code),
            "planned_date, corp_code, kind",
        )

    # 오버행
    def put_overhang_events(
        self, rows: Sequence[OverhangEvent], *, loaded_by: str, now: datetime
    ) -> int:
        return self._put(OVERHANG_EVENT, rows, loaded_by, now)

    def overhang_events(
        self, corp_code: str | None = None, *, start: date | None = None, end: date | None = None
    ) -> list[OverhangEvent]:
        return self._get(
            OVERHANG_EVENT,
            "(%s::text IS NULL OR corp_code = %s) AND (%s::date IS NULL OR rcept_dt >= %s) "
            "AND (%s::date IS NULL OR rcept_dt <= %s)",
            (corp_code, corp_code, start, start, end, end),
            "rcept_no",
        )

    def put_overhang_adjusts(
        self, rows: Sequence[OverhangAdjust], *, loaded_by: str, now: datetime
    ) -> int:
        return self._put(OVERHANG_ADJUST, rows, loaded_by, now)

    def overhang_adjusts(self, corp_code: str) -> list[OverhangAdjust]:
        return self._get(OVERHANG_ADJUST, "corp_code = %s", (corp_code,), "rcept_no")

    def put_overhang_state(
        self,
        corp_code: str,
        as_of: date,
        states: Sequence[OverhangState],
        summary: OverhangSummary | None,
        *,
        loaded_by: str,
        now: datetime,
    ) -> int:
        base = _check(loaded_by, now)
        items = [*states, *([] if summary is None else [summary])]
        _same_corp(corp_code, items)
        _same_day(as_of, items)
        with self._connect() as conn, conn.transaction():
            for table in (OVERHANG_STATE, OVERHANG_SUMMARY):
                delete(conn, table, "corp_code = %s AND as_of = %s", (corp_code, as_of))
            n = upsert(conn, OVERHANG_STATE, states, _ext(OVERHANG_STATE, base))
            if summary is not None:
                n += upsert(conn, OVERHANG_SUMMARY, [summary], _ext(OVERHANG_SUMMARY, base))
        return n

    def _latest_as_of(self, table: str, corp_code: str) -> date | None:
        with self._connect() as conn:
            r = conn.execute(
                f"SELECT max(as_of) FROM {table} WHERE corp_code = %s".encode(),  # noqa: S608 — 상수 표
                (corp_code,),
            ).fetchone()
        return None if r is None else r[0]

    def overhang_state(self, corp_code: str, as_of: date | None = None) -> list[OverhangState]:
        day = as_of or self._latest_as_of(OVERHANG_STATE.name, corp_code)
        if day is None:
            return []
        return self._get(
            OVERHANG_STATE, "corp_code = %s AND as_of = %s", (corp_code, day), "rcept_no"
        )

    def overhang_summary(self, corp_code: str, as_of: date | None = None) -> OverhangSummary | None:
        day = as_of or self._latest_as_of(OVERHANG_SUMMARY.name, corp_code)
        if day is None:
            return None
        got = self._get(OVERHANG_SUMMARY, "corp_code = %s AND as_of = %s", (corp_code, day))
        return got[0] if got else None

    def overhang_summaries(self, as_of: date) -> dict[str, OverhangSummary]:
        rows: list[OverhangSummary] = self._get(
            OVERHANG_SUMMARY, "as_of = %s", (as_of,), "corp_code"
        )
        return {s.corp_code: s for s in rows}

    # 내부자·대량보유
    def put_insider_reports(
        self, rows: Sequence[InsiderReport], *, loaded_by: str, now: datetime
    ) -> int:
        return self._put(INSIDER, rows, loaded_by, now)

    def _by_corp_dates(
        self, table: Table[Any], corp_code: str, start: date | None, end: date | None
    ) -> list[Any]:
        return self._get(
            table,
            "corp_code = %s AND (%s::date IS NULL OR rcept_dt >= %s) "
            "AND (%s::date IS NULL OR rcept_dt <= %s)",
            (corp_code, start, start, end, end),
            "rcept_no",
        )

    def insider_reports(
        self, corp_code: str, *, start: date | None = None, end: date | None = None
    ) -> list[InsiderReport]:
        return self._by_corp_dates(INSIDER, corp_code, start, end)

    def put_major_holder_reports(
        self, rows: Sequence[MajorHolderReport], *, loaded_by: str, now: datetime
    ) -> int:
        return self._put(MAJOR, rows, loaded_by, now)

    def major_holder_reports(
        self, corp_code: str, *, start: date | None = None, end: date | None = None
    ) -> list[MajorHolderReport]:
        return self._by_corp_dates(MAJOR, corp_code, start, end)

    def put_insider_windows(
        self,
        corp_code: str,
        as_of: date,
        rows: Sequence[InsiderWindow],
        *,
        loaded_by: str,
        now: datetime,
    ) -> int:
        base = _check(loaded_by, now)
        _same_corp(corp_code, rows)
        _same_day(as_of, rows)
        with self._connect() as conn, conn.transaction():
            delete(conn, WINDOW, "corp_code = %s AND as_of = %s", (corp_code, as_of))
            return upsert(conn, WINDOW, rows, _ext(WINDOW, base))

    def insider_windows(self, corp_code: str, as_of: date | None = None) -> list[InsiderWindow]:
        day = as_of or self._latest_as_of(WINDOW.name, corp_code)
        if day is None:
            return []
        return self._get(WINDOW, "corp_code = %s AND as_of = %s", (corp_code, day), "days")

    def insider_windows_on(self, as_of: date, days: int) -> list[InsiderWindow]:
        return self._get(WINDOW, "as_of = %s AND days = %s", (as_of, days), "corp_code")

    # 자기주식·주식총수
    def put_buybacks(self, rows: Sequence[BuybackEvent], *, loaded_by: str, now: datetime) -> int:
        return self._put(BUYBACK, rows, loaded_by, now)

    def buybacks(self, corp_code: str) -> list[BuybackEvent]:
        return self._get(BUYBACK, "corp_code = %s", (corp_code,), "rcept_no")

    def put_share_counts(self, rows: Sequence[ShareCount], *, loaded_by: str, now: datetime) -> int:
        return self._put(SHARE_COUNT, rows, loaded_by, now)

    def share_counts(self, corp_code: str) -> list[ShareCount]:
        return self._get(SHARE_COUNT, "corp_code = %s", (corp_code,), "period_end, rcept_no")

    def latest_share_count(self, corp_code: str, upto: date | None = None) -> ShareCount | None:
        got: list[ShareCount] = self._get(
            SHARE_COUNT,
            "corp_code = %s AND (%s::date IS NULL OR (rcept_dt IS NOT NULL AND rcept_dt <= %s))",
            (corp_code, upto, upto),
            "period_end DESC, rcept_no DESC",
            1,
        )
        return got[0] if got else None

    def put_largest_holders(
        self, rows: Sequence[LargestHolder], *, loaded_by: str, now: datetime
    ) -> int:
        return self._put(LARGEST, rows, loaded_by, now)

    def latest_largest_holder(
        self, corp_code: str, upto: date | None = None
    ) -> LargestHolder | None:
        got: list[LargestHolder] = self._get(
            LARGEST,
            "corp_code = %s AND (%s::date IS NULL OR (rcept_dt IS NOT NULL AND rcept_dt <= %s))",
            (corp_code, upto, upto),
            "period_end DESC, rcept_no DESC",
            1,
        )
        return got[0] if got else None


# ── 메모리 ─────────────────────────────────────────────────────────────────────────────


def _in_range(d: date, start: date | None, end: date | None) -> bool:
    return (start is None or d >= start) and (end is None or d <= end)


class MemoryFilingsRepo:
    """`FilingsRepo` 의 메모리 구현 — Pg 와 같은 규칙(시험·시뮬레이션·완료 시험)."""

    def __init__(self) -> None:
        self.disclosure_t = MemoryTable(DISCLOSURE)
        self.profile_t = MemoryTable(PROFILE)
        self.prelim_t = MemoryTable(PRELIM)
        self.schedule_t = MemoryTable(SCHEDULE)
        self.event_t = MemoryTable(OVERHANG_EVENT)
        self.adjust_t = MemoryTable(OVERHANG_ADJUST)
        self.state_t = MemoryTable(OVERHANG_STATE)
        self.summary_t = MemoryTable(OVERHANG_SUMMARY)
        self.insider_t = MemoryTable(INSIDER)
        self.major_t = MemoryTable(MAJOR)
        self.window_t = MemoryTable(WINDOW)
        self.buyback_t = MemoryTable(BUYBACK)
        self.share_t = MemoryTable(SHARE_COUNT)
        self.largest_t = MemoryTable(LARGEST)
        # P2 pub_filings.corp_code(읽기만 — 시험·세계가 채운다): {종목코드: (corp_code, 회사명)}
        self.corp_codes: dict[str, tuple[str, str]] = {}

    def __repr__(self) -> str:
        return "MemoryFilingsRepo()"

    @staticmethod
    def _put(t: MemoryTable[Any], rows: Sequence[Any], loaded_by: str, now: datetime) -> int:
        base = _check(loaded_by, now)
        return t.upsert(rows, _ext(t.table, base))

    # 공시 목록
    def upsert_disclosures(
        self, rows: Sequence[DisclosureRow], *, loaded_by: str, now: datetime
    ) -> int:
        return self._put(self.disclosure_t, rows, loaded_by, now)

    def seen(self, rcept_nos: Iterable[str]) -> set[str]:
        return {rc for rc in rcept_nos if (rc,) in self.disclosure_t.rows}

    def disclosure(self, rcept_no: str) -> DisclosureRow | None:
        return self.disclosure_t.get((rcept_no,))

    def disclosures(
        self,
        *,
        start: date,
        end: date,
        stock_code: str | None = None,
        corp_code: str | None = None,
        kinds: Collection[str] | None = None,
        limit: int | None = None,
    ) -> list[DisclosureRow]:
        rows = self.disclosure_t.where(
            lambda r: (
                start <= r.rcept_dt <= end
                and (stock_code is None or r.stock_code == stock_code)
                and (corp_code is None or r.corp_code == corp_code)
                and (kinds is None or r.kind in kinds)
            )
        )
        rows.sort(key=lambda r: r.rcept_no, reverse=True)
        return rows if limit is None else rows[:limit]

    def pending(
        self,
        *,
        kinds: Collection[str] | None = None,
        max_tries: int = 3,
        limit: int = 500,
        now: datetime | None = None,
        wait_min: Sequence[int] = (),
    ) -> list[DisclosureRow]:
        def due(r: DisclosureRow) -> bool:
            if now is None or r.parse_tries == 0 or not wait_min:
                return True
            wait = wait_min[min(r.parse_tries, len(wait_min)) - 1]
            return r.first_seen_at + timedelta(minutes=wait) <= now

        return self.disclosure_t.where(
            lambda r: (
                r.parse_state in ("pending", "failed")
                and r.parse_tries < max_tries
                and (kinds is None or r.kind in kinds)
                and due(r)
            )
        )[:limit]

    def mark_parsed(
        self, rcept_no: str, state: str, *, note: str | None = None, now: datetime
    ) -> int:
        _check_state(state)
        require_aware("now", now)
        stored = self.disclosure_t.rows.get((rcept_no,))
        if stored is None:
            raise KeyError(f"없는 접수번호: {rcept_no}")
        old = stored.row
        tries = old.parse_tries + (1 if state == "failed" else 0)
        stored.row = replace(old, parse_state=state, parse_note=note, parse_tries=tries)
        return tries

    def set_scores(self, scores: Mapping[str, tuple[int, Sequence[str]]]) -> int:
        n = 0
        for rc, (score, matched) in scores.items():
            stored = self.disclosure_t.rows.get((rc,))
            if stored is not None:
                stored.row = replace(stored.row, kw_score=score, kw_matched=tuple(matched))
                n += 1
        return n

    def fin_unloaded(
        self, *, kinds: Collection[str] = ("periodic",), limit: int = 500
    ) -> list[DisclosureRow]:
        return self.disclosure_t.where(lambda r: r.fin_loaded_at is None and r.kind in kinds)[
            :limit
        ]

    def mark_fin_loaded(self, rcept_nos: Iterable[str], *, now: datetime) -> int:
        require_aware("now", now)
        n = 0
        for rc in set(rcept_nos):
            stored = self.disclosure_t.rows.get((rc,))
            if stored is not None:
                stored.row = replace(stored.row, fin_loaded_at=now)
                n += 1
        return n

    def changed_corps(self, since: datetime) -> set[str]:
        require_aware("since", since)
        return {r.corp_code for r in self.disclosure_t.all() if r.first_seen_at >= since}

    def corp_index(self) -> dict[str, tuple[str, str]]:
        return dict(sorted(self.corp_codes.items()))

    # 기업개황
    def upsert_profiles(self, rows: Sequence[CorpProfile], *, loaded_by: str, now: datetime) -> int:
        return self._put(self.profile_t, rows, loaded_by, now)

    def profiles(self, corp_codes: Collection[str] | None = None) -> dict[str, CorpProfile]:
        return {
            p.corp_code: p
            for p in self.profile_t.all()
            if corp_codes is None or p.corp_code in corp_codes
        }

    def profiles_received_since(self, since: datetime) -> set[str]:
        require_aware("since", since)
        out: set[str] = set()
        for st in self.profile_t.rows.values():
            at = st.extra.get("received_at")
            if isinstance(at, datetime) and at >= since:
                out.add(st.row.corp_code)
        return out

    # 잠정실적·일정
    def put_prelims(self, rows: Sequence[PrelimRow], *, loaded_by: str, now: datetime) -> int:
        return self._put(self.prelim_t, rows, loaded_by, now)

    def prelim(self, rcept_no: str) -> PrelimRow | None:
        return self.prelim_t.get((rcept_no,))

    def prelims(self, *, start: date, end: date, corp_code: str | None = None) -> list[PrelimRow]:
        return self.prelim_t.where(
            lambda r: start <= r.rcept_dt <= end and (corp_code is None or r.corp_code == corp_code)
        )

    def replace_schedule(
        self,
        corp_codes: Collection[str],
        rows: Sequence[ScheduleRow],
        *,
        loaded_by: str,
        now: datetime,
    ) -> int:
        base = _check(loaded_by, now)
        corps = set(corp_codes)
        stray = [r.corp_code for r in rows if r.corp_code not in corps]
        if stray:
            raise ValueError(f"바꿀 회사 목록 밖의 일정 행: {stray[:3]}")
        self.schedule_t.remove(lambda r: r.corp_code in corps)
        return self.schedule_t.upsert(rows, _ext(SCHEDULE, base))

    def schedule(
        self, *, start: date, end: date, corp_code: str | None = None
    ) -> list[ScheduleRow]:
        rows = self.schedule_t.where(
            lambda r: (
                start <= r.planned_date <= end and (corp_code is None or r.corp_code == corp_code)
            )
        )
        return sorted(rows, key=lambda r: (r.planned_date, r.corp_code, r.kind))

    # 오버행
    def put_overhang_events(
        self, rows: Sequence[OverhangEvent], *, loaded_by: str, now: datetime
    ) -> int:
        return self._put(self.event_t, rows, loaded_by, now)

    def overhang_events(
        self, corp_code: str | None = None, *, start: date | None = None, end: date | None = None
    ) -> list[OverhangEvent]:
        return self.event_t.where(
            lambda r: (
                (corp_code is None or r.corp_code == corp_code)
                and _in_range(r.rcept_dt, start, end)
            )
        )

    def put_overhang_adjusts(
        self, rows: Sequence[OverhangAdjust], *, loaded_by: str, now: datetime
    ) -> int:
        return self._put(self.adjust_t, rows, loaded_by, now)

    def overhang_adjusts(self, corp_code: str) -> list[OverhangAdjust]:
        return self.adjust_t.where(lambda r: r.corp_code == corp_code)

    def put_overhang_state(
        self,
        corp_code: str,
        as_of: date,
        states: Sequence[OverhangState],
        summary: OverhangSummary | None,
        *,
        loaded_by: str,
        now: datetime,
    ) -> int:
        base = _check(loaded_by, now)
        items = [*states, *([] if summary is None else [summary])]
        _same_corp(corp_code, items)
        _same_day(as_of, items)
        for t in (self.state_t, self.summary_t):
            t.remove(lambda r: r.corp_code == corp_code and r.as_of == as_of)
        n = self.state_t.upsert(states, _ext(OVERHANG_STATE, base))
        if summary is not None:
            n += self.summary_t.upsert([summary], _ext(OVERHANG_SUMMARY, base))
        return n

    @staticmethod
    def _latest(t: MemoryTable[Any], corp_code: str) -> date | None:
        days = [r.as_of for r in t.all() if r.corp_code == corp_code]
        return max(days) if days else None

    def overhang_state(self, corp_code: str, as_of: date | None = None) -> list[OverhangState]:
        day = as_of or self._latest(self.state_t, corp_code)
        return self.state_t.where(lambda r: r.corp_code == corp_code and r.as_of == day)

    def overhang_summary(self, corp_code: str, as_of: date | None = None) -> OverhangSummary | None:
        day = as_of or self._latest(self.summary_t, corp_code)
        return None if day is None else self.summary_t.get((corp_code, day))

    def overhang_summaries(self, as_of: date) -> dict[str, OverhangSummary]:
        return {s.corp_code: s for s in self.summary_t.where(lambda r: r.as_of == as_of)}

    # 내부자·대량보유
    def put_insider_reports(
        self, rows: Sequence[InsiderReport], *, loaded_by: str, now: datetime
    ) -> int:
        return self._put(self.insider_t, rows, loaded_by, now)

    def insider_reports(
        self, corp_code: str, *, start: date | None = None, end: date | None = None
    ) -> list[InsiderReport]:
        return self.insider_t.where(
            lambda r: r.corp_code == corp_code and _in_range(r.rcept_dt, start, end)
        )

    def put_major_holder_reports(
        self, rows: Sequence[MajorHolderReport], *, loaded_by: str, now: datetime
    ) -> int:
        return self._put(self.major_t, rows, loaded_by, now)

    def major_holder_reports(
        self, corp_code: str, *, start: date | None = None, end: date | None = None
    ) -> list[MajorHolderReport]:
        return self.major_t.where(
            lambda r: r.corp_code == corp_code and _in_range(r.rcept_dt, start, end)
        )

    def put_insider_windows(
        self,
        corp_code: str,
        as_of: date,
        rows: Sequence[InsiderWindow],
        *,
        loaded_by: str,
        now: datetime,
    ) -> int:
        base = _check(loaded_by, now)
        _same_corp(corp_code, rows)
        _same_day(as_of, rows)
        self.window_t.remove(lambda r: r.corp_code == corp_code and r.as_of == as_of)
        return self.window_t.upsert(rows, _ext(WINDOW, base))

    def insider_windows(self, corp_code: str, as_of: date | None = None) -> list[InsiderWindow]:
        day = as_of or self._latest(self.window_t, corp_code)
        rows = self.window_t.where(lambda r: r.corp_code == corp_code and r.as_of == day)
        return sorted(rows, key=lambda r: r.days)

    def insider_windows_on(self, as_of: date, days: int) -> list[InsiderWindow]:
        return self.window_t.where(lambda r: r.as_of == as_of and r.days == days)

    # 자기주식·주식총수
    def put_buybacks(self, rows: Sequence[BuybackEvent], *, loaded_by: str, now: datetime) -> int:
        return self._put(self.buyback_t, rows, loaded_by, now)

    def buybacks(self, corp_code: str) -> list[BuybackEvent]:
        return self.buyback_t.where(lambda r: r.corp_code == corp_code)

    def put_share_counts(self, rows: Sequence[ShareCount], *, loaded_by: str, now: datetime) -> int:
        return self._put(self.share_t, rows, loaded_by, now)

    def share_counts(self, corp_code: str) -> list[ShareCount]:
        return self.share_t.where(lambda r: r.corp_code == corp_code)

    def latest_share_count(self, corp_code: str, upto: date | None = None) -> ShareCount | None:
        rows = self.share_t.where(
            lambda r: (
                r.corp_code == corp_code
                and (upto is None or (r.rcept_dt is not None and r.rcept_dt <= upto))
            )
        )
        return max(rows, key=lambda r: (r.period_end, r.rcept_no)) if rows else None

    def put_largest_holders(
        self, rows: Sequence[LargestHolder], *, loaded_by: str, now: datetime
    ) -> int:
        return self._put(self.largest_t, rows, loaded_by, now)

    def latest_largest_holder(
        self, corp_code: str, upto: date | None = None
    ) -> LargestHolder | None:
        rows = self.largest_t.where(
            lambda r: (
                r.corp_code == corp_code
                and (upto is None or (r.rcept_dt is not None and r.rcept_dt <= upto))
            )
        )
        return max(rows, key=lambda r: (r.period_end, r.rcept_no)) if rows else None
