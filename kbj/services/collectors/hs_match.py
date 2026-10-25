"""HS 코드 대조 CLI — 월별 금액(입력)과 관세청 공식 통계가 맞는 HS 조합을 찾는다(매핑표 판 3 검증).

    docker compose exec -T scheduler python -m kbj.services.collectors.hs_match < targets.json

입력(표준 입력, JSON — 레포 밖 파일): {"targets": [{"rid", "dir": exp|imp, "kind": national|sigungu,
"partners": [ISO2…], "regions": [[시도코드, 시군구 이름]…], "months": {"YYYY-MM": 달러…},
"candidates": [HS 접두…]}]}

- national: 후보의 HS4 마다 품목별(15101609, 상대국이 있으면 15100475 국가마다)을 한 번 받아 10자리
  계열을 모은다 → 합이 입력과 맞는 10자리 조합.
- sigungu: 후보 HS6(+ 같은 HS4 안 전국에서 수출이 있는 HS6, HS4 마다 최대 `SIBLINGS`) × 시도마다
  시군구별(15134343)을 받아 입력 시군구 합 계열 → 맞는 HS6 조합.
- 맞음 = 달마다 |합 − 입력| ≤ max(2달러, `TOL`×입력) 인 달이 (달 수 − 1) 이상. 0 인 계열은
  조합에서 뺀다.

출력(표준 출력): 한 줄에 하나 — `R012 match 8542313000+8542319000 err=0.00% alt=1` /
`R013 nomatch best=… err=12.3%` / `R014 no_data …`. 금액은 내지 않는다(코드·오차만). 같은 요청은
한 번만 부른다(캐시). 공공데이터 하루 한도는 전송 계층이 센다 — 닫히면 남은 대상은 `skipped`.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import IO, Any, Final, Protocol

from kbj.data.public.customs.models import sgg_base

__all__ = ["Series", "Source", "Target", "find_subsets", "main", "match_target"]

TOL: Final = 0.005  # 달마다 허용 상대 오차(정정·판독 오차)
ABS_TOL: Final = 2.0  # 달러
SIBLINGS: Final = 8  # 시군구 — HS4 안 형제 HS6 최대 수
MAX_NODES: Final = 2_000_000  # 조합 탐색 상한
MAX_SOLUTIONS: Final = 5

Series = dict[str, dict[str, float]]  # 코드 → {YYYY-MM: 달러}


class Source(Protocol):
    def items(self, hs4: str, start: str, end: str, flow: str) -> Series: ...
    def item_country(self, hs4: str, country: str, start: str, end: str, flow: str) -> Series: ...
    def sigungu(self, sido: str, hs6: str, start: str, end: str) -> dict[str, dict[str, float]]: ...


@dataclass(frozen=True)
class Target:
    rid: str
    flow: str
    kind: str
    months: dict[str, float]
    candidates: tuple[str, ...]
    partners: tuple[str, ...] = ()
    regions: tuple[tuple[str, str], ...] = ()

    @staticmethod
    def parse(d: Mapping[str, Any]) -> Target:
        return Target(
            rid=str(d["rid"]),
            flow="imp" if d.get("dir") == "imp" else "exp",
            kind=str(d["kind"]),
            months={str(k): float(v) for k, v in d["months"].items()},
            candidates=tuple(str(c) for c in d["candidates"]),
            partners=tuple(str(p) for p in d.get("partners") or ()),
            regions=tuple((str(a), str(b)) for a, b in d.get("regions") or ()),
        )


@dataclass
class Result:
    rid: str
    status: str  # match | nomatch | no_data | skipped
    codes: tuple[str, ...] = ()
    err: float | None = None
    alts: list[tuple[str, ...]] = field(default_factory=list)
    note: str = ""
    live: tuple[str, ...] = ()  # 값 있는 계열 코드(줄여 찍기용)

    def line(self) -> str:
        e = "" if self.err is None else f" err={self.err * 100:.2f}%"
        if self.status == "match":
            alt = f" alt={len(self.alts)}" if self.alts else ""
            more = "".join(f" | {_short(a, self.live)}" for a in self.alts)
            return f"{self.rid} match {_short(self.codes, self.live)}{e}{alt}{more}"
        if self.status == "nomatch":
            best = _short(self.codes, self.live) or "-"
            return f"{self.rid} nomatch best={best}{e} {self.note}".rstrip()
        return f"{self.rid} {self.status} {self.note}".rstrip()

    def json_line(self) -> str:
        """`--jsonl` 한 줄 — 코드를 줄이지 않고 전부(매핑표 반영용). 금액은 싣지 않는다."""
        err = None if self.err is None else round(self.err, 6)
        d = {"rid": self.rid, "status": self.status, "codes": list(self.codes), "err": err,
             "alts": [list(a) for a in self.alts], "note": self.note}  # fmt: skip
        return json.dumps(d, ensure_ascii=False)


def _ok(total: float, want: float) -> bool:
    return abs(total - want) <= max(ABS_TOL, TOL * abs(want))


def _err(total: Mapping[str, float], want: Mapping[str, float]) -> float:
    return max(
        (abs(total.get(m, 0.0) - w) / w if w else (0.0 if not total.get(m) else 1.0))
        for m, w in want.items()
    )


def find_subsets(series: Series, want: Mapping[str, float]) -> list[tuple[str, ...]]:
    """합이 `want` 와 맞는 코드 조합(값이 모두 0 인 코드는 뺀다). 달마다 한 달까지 어긋남을 허용."""
    months = sorted(want)
    need = len(months) - 1 if len(months) > 2 else len(months)
    codes = sorted(
        (c for c, s in series.items() if any(s.get(m, 0.0) for m in months)),
        key=lambda c: -sum(series[c].get(m, 0.0) for m in months),
    )
    vec = [[series[c].get(m, 0.0) for m in months] for c in codes]
    target = [want[m] for m in months]
    out: list[tuple[str, ...]] = []
    nodes = 0

    def walk(i: int, acc: list[float], pick: list[int]) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > MAX_NODES or len(out) >= MAX_SOLUTIONS:
            return
        if pick and sum(_ok(a, t) for a, t in zip(acc, target, strict=True)) >= need:
            out.append(tuple(sorted(codes[k] for k in pick)))
        over = sum(1 for a, t in zip(acc, target, strict=True) if a - t > max(ABS_TOL, TOL * t))
        if over > len(months) - need or i >= len(codes):
            return
        for k in range(i, len(codes)):
            nxt = [a + v for a, v in zip(acc, vec[k], strict=True)]
            walk(k + 1, nxt, [*pick, k])

    walk(0, [0.0] * len(months), [])
    return out


def _prefix_groups(series: Series, want: Mapping[str, float]) -> list[tuple[str, ...]]:
    """접두(4·6·8자리)가 같은 계열 전부의 합이 맞는 경우 — 품목이 소호·호 전체일 때."""
    months = sorted(want)
    need = len(months) - 1 if len(months) > 2 else len(months)
    live = [c for c, s in series.items() if any(s.get(m, 0.0) for m in months)]
    out: list[tuple[str, ...]] = []
    for n in (4, 6, 8):
        groups: dict[str, list[str]] = {}
        for c in live:
            if len(c) > n:
                groups.setdefault(c[:n], []).append(c)
        for cs in groups.values():
            tot = [sum(series[c].get(m, 0.0) for c in cs) for m in months]
            if (
                len(cs) > 1
                and sum(_ok(a, want[m]) for a, m in zip(tot, months, strict=True)) >= need
            ):
                out.append(tuple(sorted(cs)))
    return out


def _short(codes: Sequence[str], live: Sequence[str]) -> str:
    """어떤 접두의 값 있는 코드 전부(넷 이상)면 '접두*', 아니면 전부 적는다."""
    if len(codes) > 3:
        pre = codes[0]
        for c in codes[1:]:
            while not c.startswith(pre):
                pre = pre[:-1]
        if set(codes) == {c for c in live if c.startswith(pre)}:
            return f"{pre}*"
    return "+".join(codes)


def _best(series: Series, want: Mapping[str, float]) -> tuple[tuple[str, ...], float] | None:
    """맞는 조합이 없을 때 — 하나·전체 중 오차가 가장 작은 것(사람이 볼 실마리)."""
    if not series:
        return None
    cands: list[tuple[str, ...]] = [(c,) for c in series] + [tuple(sorted(series))]

    def total(cs: Iterable[str]) -> dict[str, float]:
        return {m: sum(series[c].get(m, 0.0) for c in cs) for m in want}

    scored = sorted((_err(total(cs), want), cs) for cs in cands)
    return scored[0][1], scored[0][0]


def _same_place(api_name: str, name: str) -> bool:
    """API 시군구 이름 ↔ 매핑 이름 — 앞의 시도('충청남도 ')를 떼고 같거나, 구가 붙은 시
    ('수원시 영통구')면 같은 곳."""
    a, b = sgg_base(api_name).replace(" ", ""), sgg_base(name).replace(" ", "")
    return a == b or (b.endswith("시") and a.startswith(b))


def _add(into: Series, got: Series) -> None:
    for c, s in got.items():
        dst = into.setdefault(c, {})
        for m, v in s.items():
            dst[m] = dst.get(m, 0.0) + v


def match_target(t: Target, src: Source) -> Result:
    months = sorted(t.months)
    start, end = months[0], months[-1]
    series: Series = {}
    if t.kind == "national":
        for hs4 in sorted({c[:4] for c in t.candidates}):
            if t.partners:
                for cc in t.partners:
                    _add(series, src.item_country(hs4, cc, start, end, t.flow))
            else:
                _add(series, src.items(hs4, start, end, t.flow))
    else:
        hs6s = {c[:6] for c in t.candidates if len(c) >= 6}
        for hs4 in sorted({c[:4] for c in t.candidates}):
            nat = src.items(hs4, start, end, "exp")
            live = sorted({c[:6] for c, s in nat.items() if len(c) >= 6 and any(s.values())})
            hs6s.update(live[:SIBLINGS])
        names = {(a, b) for a, b in t.regions}
        seen: set[str] = set()
        for hs6 in sorted(hs6s):
            for sido in sorted({a for a, _ in names}):
                rows = src.sigungu(sido, hs6, start, end)
                for sgg, s in rows.items():
                    seen.add(sgg)
                    if any(a == sido and _same_place(sgg, b) for a, b in names):
                        _add(series, {hs6: s})
        if seen and not series:
            return Result(
                t.rid, "no_data", note="시군구 이름 불일치: " + ",".join(sorted(seen)[:12])
            )
    series = {c: {m: v for m, v in s.items() if m in t.months} for c, s in series.items()}
    if not any(any(s.values()) for s in series.values()):
        return Result(t.rid, "no_data", note=f"후보 {len(t.candidates)}개 — 값 없음")
    sols = _prefix_groups(series, t.months) + find_subsets(series, t.months)
    sols = list(dict.fromkeys(sols))
    if sols:

        def total(cs: Sequence[str]) -> dict[str, float]:
            return {m: sum(series[c].get(m, 0.0) for c in cs) for m in t.months}

        ranked = sorted(sols, key=lambda cs: (_err(total(cs), t.months), len(cs)))
        live = tuple(sorted(c for c, v in series.items() if any(v.values())))
        best = ranked[0]
        return Result(t.rid, "match", best, _err(total(best), t.months), ranked[1:], live=live)
    b = _best(series, t.months)
    if b is None:
        return Result(t.rid, "no_data")
    return Result(t.rid, "nomatch", b[0], b[1], note=f"계열 {len(series)}개")


# ── 운영 원천(관세청 API) ────────────────────────────────────────────────────────────────


def _ym(p: str | None) -> str | None:
    return None if p is None else f"{p[:4]}-{p[4:6]}"


def _f(v: Decimal | None) -> float:
    return float(v) if v is not None else 0.0


class ApiSource:  # pragma: no cover — 운영(키·Redis)
    """`CustomsClient` 위의 캐시 원천."""

    def __init__(self, client: Any) -> None:
        self.c = client
        self.cache: dict[tuple[Any, ...], Any] = {}

    @staticmethod
    def _rows(rows: Iterable[Any], flow: str) -> Series:
        """10자리 행만(합계·상위 자릿수 행은 뺀다)."""
        out: Series = {}
        for r in rows:
            if r.hs and len(r.hs) == 10 and (m := _ym(r.period)):
                v = _f(r.exp_usd if flow == "exp" else r.imp_usd)
                out.setdefault(r.hs, {})[m] = out.get(r.hs, {}).get(m, 0.0) + v
        return out

    # 품목별은 수집기처럼 HS2 장 단위로 받는다(trade.yaml items_mode by_chapter) → HS4 로 거른다
    def items(self, hs4: str, start: str, end: str, flow: str) -> Series:
        k = ("i", hs4[:2], start, end)
        if k not in self.cache:
            self.cache[k] = self.c.items(start.replace("-", ""), end.replace("-", ""), hs=hs4[:2])
        return {c: s for c, s in self._rows(self.cache[k], flow).items() if c.startswith(hs4)}

    def item_country(self, hs4: str, country: str, start: str, end: str, flow: str) -> Series:
        k = ("c", hs4[:2], country, start, end)
        if k not in self.cache:
            self.cache[k] = self.c.item_country(
                start.replace("-", ""), end.replace("-", ""), country, hs=hs4[:2]
            )
        return {c: s for c, s in self._rows(self.cache[k], flow).items() if c.startswith(hs4)}

    def sigungu(self, sido: str, hs6: str, start: str, end: str) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        # 2026-07 개편: 광주 29·전남 46 은 그 전 신고분, 그 뒤는 12(같은 시군구 이름)
        spans = [(sido, start, end)]
        if sido in ("29", "46") and end >= "2026-07":
            spans = [(sido, start, min(end, "2026-06")), ("12", max(start, "2026-07"), end)]
        for sd, a, b in spans:
            if a > b:
                continue
            k = ("s", sd, hs6, a, b)
            if k not in self.cache:
                self.cache[k] = self.c.sigungu(a.replace("-", ""), b.replace("-", ""), sd, hs6)
            for r in self.cache[k]:
                if (m := _ym(r.period)) is not None:
                    dst = out.setdefault(r.sigungu_name, {})
                    dst[m] = dst.get(m, 0.0) + _f(r.exp_usd)
        return out


def _production() -> Source:  # pragma: no cover — 운영
    from kbj.config.settings import Settings
    from kbj.data.datago import DatagoTransport
    from kbj.data.public.customs.client import CustomsClient
    from kbj.services.collectors.customs import DATASET_IDS
    from kbj.services.runtime.heartbeat import connect_redis

    s = Settings()
    if s.datago_key is None or s.redis_url is None:
        raise RuntimeError("KBJ_DATAGO_KEY·KBJ_REDIS_URL 이 필요하다")
    t = DatagoTransport.for_datasets(s.datago_key, connect_redis(s.redis_url), DATASET_IDS)
    return ApiSource(CustomsClient(t))


def main(
    argv: Sequence[str] | None = None,
    *,
    inp: IO[str] | None = None,
    out: IO[str] | None = None,
    source: Callable[[], Source] | None = None,
) -> int:
    i: IO[str] = inp if inp is not None else sys.stdin
    o: IO[str] = out if out is not None else sys.stdout
    as_json = "--jsonl" in (argv or ())
    try:
        targets = [Target.parse(d) for d in json.load(i)["targets"]]
    except (ValueError, KeyError, TypeError) as e:
        o.write(f"오류: 입력 JSON — {type(e).__name__}\n")
        return 2
    src = (source or _production)()
    counts: dict[str, int] = {}
    stop: str | None = None
    for t in targets:
        if stop is not None:
            r = Result(t.rid, "skipped", note=stop)
        else:
            try:
                r = match_target(t, src)
            except Exception as e:  # 한도·형식 — 사유는 형식 이름만(키를 싣지 않는다)
                name = type(e).__name__
                if name in ("BudgetExhausted", "DatagoQuotaExceeded"):
                    stop = f"하루 한도({name}) — 내일 같은 명령"
                    r = Result(t.rid, "skipped", note=stop)
                else:
                    r = Result(t.rid, "error", note=name)
        counts[r.status] = counts.get(r.status, 0) + 1
        o.write((r.json_line() if as_json else r.line()) + "\n")
        o.flush()
    o.write("# " + " ".join(f"{k}={v}" for k, v in sorted(counts.items())) + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
