"""공개 투자 콜 기록(`calls/`) — 읽기·검사·고친 흔적·성과 계산 — 순수 계산.

ADR 0042, docs/metrics.md §32. 콜 하나 = 마크다운 파일 하나
(`calls/<YYYY>/<YYYY-MM-DD>-<코드>-<슬러그>.md`). 머리(YAML front matter)에 종목·방향·
진입/목표 시총·틀렸다고 볼 조건을, 본문에 논리·밸류에이션·갱신을 쓴다. 레포가 공개라서 커밋
시각이 "그때 그렇게 판단했다"는 기록이 된다 — 그래서 한 번 낸 콜의 판단 칸은 고치지 않는다.

- `parse_call(rel_path, text)`: 파일 하나 → `CallRecord` 또는 문제 목록(값은 지어내지 않는다).
- `check_call(record, today)`: 칸끼리의 모순(롱인데 목표 < 진입 등)·미래 날짜.
- `immutable_changes(old, new)`: 기준 버전과 비교해 바뀌면 안 되는 칸이 바뀌었는지.
- `call_mark(...)`: 진입·평가 시총(+벤치마크)으로 콜 수익률·상대 성과·목표 진척(시총 기준).

계약 ⑨ — 파일 시스템·git·저장소를 모른다(입력은 경로 문자열·본문 문자열·날짜·수).
문제 문구에는 칸 이름·사유만 넣는다(값 전체를 되풀이하지 않는다).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Final, Literal

import yaml

from kbj.core.quality import Quality

__all__ = [
    "BENCHMARKS",
    "BODY_LOCKED_HEADINGS",
    "IMMUTABLE_FIELDS",
    "MAX_BACKDATE_DAYS",
    "CallMark",
    "CallProblem",
    "CallRecord",
    "call_mark",
    "check_call",
    "check_new_call",
    "duplicate_ids",
    "immutable_changes",
    "parse_call",
]

Direction = Literal["long", "short"]
Market = Literal["KR", "HK", "US"]
Status = Literal["open", "closed"]

MARKETS: Final[tuple[str, ...]] = ("KR", "HK", "US")
DIRECTIONS: Final[tuple[str, ...]] = ("long", "short")
STATUSES: Final[tuple[str, ...]] = ("open", "closed")
CURRENCY: Final[Mapping[str, str]] = {"KR": "KRW", "HK": "HKD", "US": "USD"}
BENCHMARKS: Final[Mapping[str, tuple[str, ...]]] = {
    "KR": ("KOSPI", "KOSDAQ", "KOSPI200"),
    "HK": ("HSI", "HSTECH"),
    "US": ("SPX", "NDX"),
}
CODE_PATTERN: Final[Mapping[str, re.Pattern[str]]] = {
    "KR": re.compile(r"^[0-9A-Z]{6}$"),  # KRX 단축코드(신규 영문 혼합 코드 포함)
    "HK": re.compile(r"^\d{4,5}$"),
    "US": re.compile(r"^[A-Z][A-Z.]{0,5}$"),
}
FILE_RE: Final = re.compile(
    r"^calls/(?P<year>\d{4})/(?P<day>\d{4}-\d{2}-\d{2})-(?P<code>[0-9A-Za-z.]+)-(?P<slug>[0-9a-z][0-9a-z-]*)\.md$"
)
FRONT_RE: Final = re.compile(r"\A---\r?\n(?P<head>.*?)\r?\n---\r?\n?(?P<body>.*)\Z", re.DOTALL)

REQUIRED: Final[tuple[str, ...]] = (
    "id",
    "opened",
    "market",
    "code",
    "name",
    "direction",
    "horizon_months",
    "sector",
    "entry_mcap_eok",
    "entry_mcap_as_of",
    "entry_mcap_source",
    "target_mcap_eok",
    "benchmark",
    "kill_criteria",
    "status",
)
OPTIONAL: Final[tuple[str, ...]] = (
    "valuation_method",
    "catalysts",
    "closed",
    "close_mcap_eok",
    "close_reason",
)
# 한 번 낸 뒤 바꾸면 안 되는 칸(판단 자체). status·종료 칸은 open→closed 한 번만 채운다.
IMMUTABLE_FIELDS: Final[tuple[str, ...]] = (
    "id",
    "opened",
    "market",
    "code",
    "direction",
    "horizon_months",
    "entry_mcap_eok",
    "entry_mcap_as_of",
    "target_mcap_eok",
    "benchmark",
    "kill_criteria",
)
CLOSE_FIELDS: Final[tuple[str, ...]] = ("closed", "close_mcap_eok", "close_reason")
# 본문에서 `## 갱신` 앞까지(논리·밸류에이션)는 고치지 않는다. 갱신은 그 아래에 날짜와 함께 덧붙인다.
BODY_LOCKED_HEADINGS: Final[tuple[str, ...]] = ("## 논리",)
UPDATE_HEADING: Final = "## 갱신"
MAX_BACKDATE_DAYS: Final = (
    3  # 새 콜의 opened 는 검사일보다 이만큼까지만 앞설 수 있다(소급 기록 막기)
)


@dataclass(frozen=True, slots=True)
class CallProblem:
    """콜 파일 하나의 문제. `field` 는 칸 이름(없으면 파일 전체)."""

    path: str
    field: str | None
    reason: str

    def __str__(self) -> str:
        where = f"{self.path}:{self.field}" if self.field else self.path
        return f"{where} — {self.reason}"


@dataclass(frozen=True, slots=True)
class CallRecord:
    """콜 하나. 시총 단위는 억(통화는 시장 기준 — KRW·HKD·USD)."""

    path: str
    id: str
    opened: date
    market: Market
    code: str
    name: str
    direction: Direction
    horizon_months: int
    sector: str
    entry_mcap_eok: Decimal
    entry_mcap_as_of: date
    entry_mcap_source: str
    target_mcap_eok: Decimal
    benchmark: str
    kill_criteria: tuple[str, ...]
    status: Status
    valuation_method: str | None = None
    catalysts: tuple[str, ...] = ()
    closed: date | None = None
    close_mcap_eok: Decimal | None = None
    close_reason: str | None = None
    locked_body: str = ""  # `## 갱신` 앞 본문(고치지 않는 부분 — 공백 정리 후)
    has_logic: bool = False
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def currency(self) -> str:
        return CURRENCY[self.market]

    @property
    def implied_move(self) -> Decimal:
        """목표 시총 / 진입 시총 − 1 (콜이 기대한 변화율, 소수)."""
        return self.target_mcap_eok / self.entry_mcap_eok - 1


@dataclass(frozen=True, slots=True)
class CallMark:
    """콜의 평가 시점 성과(docs/metrics.md §32). 값을 모르면 None — 0 으로 채우지 않는다."""

    call_return: Decimal | None  # 방향 반영(숏은 하락이 +)
    stock_return: Decimal | None  # 방향 반영 전 시총 변화율
    benchmark_return: Decimal | None
    relative: Decimal | None  # 방향 반영 상대 성과(롱: 종목−벤치, 숏: 벤치−종목)
    target_progress: Decimal | None  # (평가 − 진입) / (목표 − 진입)
    target_hit: bool | None
    quality: Quality


# ── 읽기 ───────────────────────────────────────────────────────────────


def parse_call(rel_path: str, text: str) -> CallRecord | list[CallProblem]:
    """`calls/` 아래 파일 하나를 읽는다. 문제가 하나라도 있으면 문제 목록을 돌려준다."""
    probs: list[CallProblem] = []

    def bad(fld: str | None, reason: str) -> None:
        probs.append(CallProblem(rel_path, fld, reason))

    m_file = FILE_RE.match(rel_path)
    if m_file is None:
        bad(None, "파일 위치·이름은 calls/<YYYY>/<YYYY-MM-DD>-<코드>-<소문자-슬러그>.md")
    m = FRONT_RE.match(text)
    if m is None:
        bad(None, "맨 앞에 --- 로 감싼 YAML 머리가 없다")
        return probs
    try:
        head = yaml.safe_load(m.group("head"))
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        line = f" (머리 {mark.line + 1}번째 줄)" if mark is not None else ""
        bad(None, f"YAML 문법 오류{line}")
        return probs
    if not isinstance(head, dict):
        bad(None, "YAML 머리가 매핑이 아니다")
        return probs

    for k in head:
        if k not in REQUIRED and k not in OPTIONAL:
            bad(str(k), "모르는 칸(오타?)")
    for k in REQUIRED:
        if head.get(k) in (None, "", []):
            bad(k, "필수 칸이 비었다")

    def text_of(k: str) -> str | None:
        v = head.get(k)
        if v is None:
            return None
        if isinstance(v, (str, int)) and not isinstance(v, bool):
            s = str(v).strip()
            return s or None
        bad(k, "문자열이어야 한다")
        return None

    def date_of(k: str) -> date | None:
        v = head.get(k)
        if v is None:
            return None
        if isinstance(v, date):
            return v
        if isinstance(v, str):
            try:
                return date.fromisoformat(v.strip())
            except ValueError:
                pass
        bad(k, "날짜는 YYYY-MM-DD")
        return None

    def money_of(k: str) -> Decimal | None:
        v = head.get(k)
        if v is None:
            return None
        if isinstance(v, bool) or not isinstance(v, (int, float, str)):
            bad(k, "숫자(억 단위)여야 한다")
            return None
        try:
            d = Decimal(str(v).replace(",", "").strip())
        except InvalidOperation:
            bad(k, "숫자(억 단위)여야 한다")
            return None
        if not d.is_finite() or d <= 0:
            bad(k, "0 보다 큰 수여야 한다")
            return None
        return d

    def list_of(k: str) -> tuple[str, ...]:
        v = head.get(k)
        if v is None:
            return ()
        if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
            bad(k, "빈 줄 없는 문자열 목록이어야 한다")
            return ()
        return tuple(x.strip() for x in v)

    cid = text_of("id")
    opened = date_of("opened")
    market = text_of("market")
    code = text_of("code")
    name = text_of("name")
    direction = text_of("direction")
    sector = text_of("sector")
    entry = money_of("entry_mcap_eok")
    entry_as_of = date_of("entry_mcap_as_of")
    entry_source = text_of("entry_mcap_source")
    target = money_of("target_mcap_eok")
    benchmark = text_of("benchmark")
    kill = list_of("kill_criteria")
    status = text_of("status")
    vmethod = text_of("valuation_method")
    catalysts = list_of("catalysts")
    closed = date_of("closed")
    close_mcap = money_of("close_mcap_eok")
    close_reason = text_of("close_reason")

    horizon_raw = head.get("horizon_months")
    horizon: int | None = None
    if horizon_raw is not None:
        if (
            isinstance(horizon_raw, int)
            and not isinstance(horizon_raw, bool)
            and 1 <= horizon_raw <= 60
        ):
            horizon = horizon_raw
        else:
            bad("horizon_months", "1~60 사이 정수(개월)")

    if market is not None and market not in MARKETS:
        bad("market", f"{'·'.join(MARKETS)} 중 하나")
        market = None
    if direction is not None and direction not in DIRECTIONS:
        bad("direction", "long 또는 short")
        direction = None
    if status is not None and status not in STATUSES:
        bad("status", "open 또는 closed")
        status = None
    if market is not None and code is not None and not CODE_PATTERN[market].match(code):
        bad("code", f"{market} 종목코드 형식이 아니다(국장 6자리는 따옴표로 감싼다 — 앞자리 0)")
    if market is not None and benchmark is not None and benchmark not in BENCHMARKS[market]:
        bad("benchmark", f"{market} 은 {'·'.join(BENCHMARKS[market])} 중 하나")

    if m_file is not None:
        stem = rel_path.rsplit("/", 1)[-1][:-3]
        if cid is not None and cid != stem:
            bad("id", "파일 이름(확장자 뺀 것)과 같아야 한다")
        if opened is not None:
            if m_file.group("day") != opened.isoformat():
                bad("opened", "파일 이름의 날짜와 같아야 한다")
            if m_file.group("year") != f"{opened.year:04d}":
                bad("opened", "연도 폴더와 같아야 한다")
        if code is not None and m_file.group("code") != code:
            bad("code", "파일 이름의 코드와 같아야 한다")

    body = m.group("body")
    locked = _locked_body(body)
    has_logic = any(_has_heading(body, h) for h in BODY_LOCKED_HEADINGS)
    if not has_logic:
        bad(None, f"본문에 '{BODY_LOCKED_HEADINGS[0]}' 단락이 없다")

    if probs:
        return probs
    assert cid and opened and market and code and name and direction and sector  # noqa: S101 — 위에서 검사
    assert entry and entry_as_of and entry_source and target and benchmark and status  # noqa: S101
    assert horizon is not None  # noqa: S101
    return CallRecord(
        path=rel_path,
        id=cid,
        opened=opened,
        market=market,  # type: ignore[arg-type]
        code=code,
        name=name,
        direction=direction,  # type: ignore[arg-type]
        horizon_months=horizon,
        sector=sector,
        entry_mcap_eok=entry,
        entry_mcap_as_of=entry_as_of,
        entry_mcap_source=entry_source,
        target_mcap_eok=target,
        benchmark=benchmark,
        kill_criteria=kill,
        status=status,  # type: ignore[arg-type]
        valuation_method=vmethod,
        catalysts=catalysts,
        closed=closed,
        close_mcap_eok=close_mcap,
        close_reason=close_reason,
        locked_body=locked,
        has_logic=has_logic,
        raw=dict(head),
    )


def _has_heading(body: str, heading: str) -> bool:
    return any(line.rstrip() == heading for line in body.splitlines())


def _locked_body(body: str) -> str:
    """`## 갱신` 앞까지의 본문 — 줄 끝 공백·빈 줄 수 차이는 무시한다(내용 비교용)."""
    out: list[str] = []
    for line in body.splitlines():
        if line.rstrip() == UPDATE_HEADING:
            break
        out.append(line.rstrip())
    return "\n".join(x for x in out if x).strip()


# ── 검사 ───────────────────────────────────────────────────────────────


def check_call(rec: CallRecord, today: date) -> list[CallProblem]:
    """칸끼리의 모순. `today` 는 주입한다(KST 날짜)."""
    probs: list[CallProblem] = []

    def bad(fld: str | None, reason: str) -> None:
        probs.append(CallProblem(rec.path, fld, reason))

    if rec.opened > today:
        bad("opened", "미래 날짜다")
    if rec.entry_mcap_as_of > rec.opened:
        bad("entry_mcap_as_of", "콜 날짜보다 늦은 시총을 진입값으로 쓸 수 없다")
    if (rec.opened - rec.entry_mcap_as_of).days > 7:
        bad("entry_mcap_as_of", "진입 시총이 콜 날짜보다 7일 넘게 오래됐다")
    if rec.direction == "long" and rec.target_mcap_eok <= rec.entry_mcap_eok:
        bad("target_mcap_eok", "롱인데 목표 시총이 진입 시총 이하다")
    if rec.direction == "short" and rec.target_mcap_eok >= rec.entry_mcap_eok:
        bad("target_mcap_eok", "숏인데 목표 시총이 진입 시총 이상이다")

    filled = [k for k in CLOSE_FIELDS if getattr(rec, k) is not None]
    if rec.status == "closed":
        for k in CLOSE_FIELDS:
            if getattr(rec, k) is None:
                bad(k, "닫힌 콜은 종료 칸(closed·close_mcap_eok·close_reason)을 모두 채운다")
        if rec.closed is not None:
            if rec.closed < rec.opened:
                bad("closed", "콜 날짜보다 이르다")
            if rec.closed > today:
                bad("closed", "미래 날짜다")
    elif filled:
        for k in filled:
            bad(k, "열린 콜에는 종료 칸을 쓰지 않는다(status: closed 로 닫을 때 함께 쓴다)")
    return probs


def immutable_changes(old: CallRecord, new: CallRecord) -> list[CallProblem]:
    """기준 버전(`old`) 대비 바뀌면 안 되는 것이 바뀌었는지. 같은 경로끼리 비교한다."""
    probs: list[CallProblem] = []

    def bad(fld: str | None, reason: str) -> None:
        probs.append(CallProblem(new.path, fld, reason))

    for k in IMMUTABLE_FIELDS:
        if getattr(old, k) != getattr(new, k):
            bad(k, "한 번 낸 콜의 판단 칸은 고치지 않는다(새 콜을 내거나 '## 갱신' 에 적는다)")
    if old.locked_body != new.locked_body:
        bad(
            None,
            "'## 갱신' 앞 본문(논리·밸류에이션)은 고치지 않는다 — 갱신 아래에 날짜와 함께 덧붙인다",
        )
    if old.status == "closed":
        if new.status != "closed":
            bad("status", "닫힌 콜을 다시 열 수 없다(새 콜을 낸다)")
        for k in CLOSE_FIELDS:
            if getattr(old, k) != getattr(new, k):
                bad(k, "닫힌 콜의 종료 칸은 고치지 않는다")
    return probs


def check_new_call(rec: CallRecord, today: date) -> list[CallProblem]:
    """기준 버전에 없던(새로 낸) 콜 — 소급 기록 막기."""
    if (today - rec.opened) > timedelta(days=MAX_BACKDATE_DAYS):
        return [
            CallProblem(
                rec.path,
                "opened",
                f"새 콜의 날짜가 검사일보다 {MAX_BACKDATE_DAYS}일 넘게 이르다(소급 기록)",
            )
        ]
    return []


def duplicate_ids(records: Iterable[CallRecord]) -> list[CallProblem]:
    seen: dict[str, str] = {}
    out: list[CallProblem] = []
    for r in records:
        if r.id in seen:
            out.append(CallProblem(r.path, "id", f"id 가 {seen[r.id]} 와 겹친다"))
        else:
            seen[r.id] = r.path
    return out


# ── 성과 ───────────────────────────────────────────────────────────────


def call_mark(
    rec: CallRecord,
    mark_mcap_eok: Decimal | None,
    *,
    mark_quality: Quality = Quality.OK,
    bench_entry: Decimal | None = None,
    bench_mark: Decimal | None = None,
    bench_quality: Quality = Quality.OK,
) -> CallMark:
    """평가 시총(닫힌 콜은 `close_mcap_eok` 를 넘긴다)으로 성과를 계산한다 — docs/metrics.md §32.

    - 종목 변화율 r = 평가 / 진입 − 1. 콜 수익률 = r(롱) · −r(숏).
    - 벤치 변화율 b = 벤치 평가 / 벤치 진입 − 1. 상대 성과 = r − b(롱) · b − r(숏).
    - 목표 진척 = (평가 − 진입) / (목표 − 진입) — 1 이상이면 도달(방향과 무관하게 같은 식).
    - 품질: 평가값·벤치값 품질 중 가장 나쁜 것(invalid 값은 계산하지 않는다 → None).
    """
    sign = Decimal(1) if rec.direction == "long" else Decimal(-1)
    q = _worst(
        [mark_quality]
        + ([bench_quality] if bench_entry is not None or bench_mark is not None else [])
    )

    if mark_mcap_eok is None or not mark_quality.usable or mark_mcap_eok <= 0:
        return CallMark(None, None, None, None, None, None, Quality.INVALID)

    r = mark_mcap_eok / rec.entry_mcap_eok - 1
    b: Decimal | None = None
    if (
        bench_entry is not None
        and bench_mark is not None
        and bench_quality.usable
        and bench_entry > 0
    ):
        b = bench_mark / bench_entry - 1
    progress = (mark_mcap_eok - rec.entry_mcap_eok) / (rec.target_mcap_eok - rec.entry_mcap_eok)
    return CallMark(
        call_return=sign * r,
        stock_return=r,
        benchmark_return=b,
        relative=None if b is None else sign * (r - b),
        target_progress=progress,
        target_hit=progress >= 1,
        quality=q,
    )


_ORDER: Final[Sequence[Quality]] = (Quality.OK, Quality.STALE, Quality.ESTIMATED, Quality.INVALID)


def _worst(qs: Sequence[Quality]) -> Quality:
    return max(qs, key=_ORDER.index) if qs else Quality.OK
