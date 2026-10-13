"""종목↔HS 매핑 해석·검증(순수 — metrics §28, docs/p6_design.md §4.5·D-P6-14).

- `map_rows`: 매핑표(`kbj.config.trade.TradeMap` — 공개 `config/knowledge/trade_map.yaml` 또는 VM
  local 파일)를 `pub_trade.stock_map` 행(`StockMapRow`)으로 편다. 품목 행 region_id = '', 지역 행
  hs = HS6·flow = exp.
- `map_version`: 판 이름 = `v<version>-<내용 sha256 앞 10자리>`(같은 내용 → 같은 판, 시계와 무관).
- `cross_check`: 파일 사이 대조(형식·한 파일 안의 맞음은 `kbj.config.trade` 해석기가 이미 본다) —
  매핑 HS2 장이 `hs_chapters.yaml` 에 있는가, 지역 ID 가 `trade_regions.yaml` 에 있는가.
  `db_check`: DB 대조(VM — `verify_trade`) — 종목코드가 상장사 목록에 있는가, HS 접두가 수집된
  `pub_trade.hs_dim` 에 있는가(그 접두로 시작하는 코드가 하나라도 있으면 있음) [실측 필요 #56].
- `resolve`: 공개 + local 병합 — 같은 종목이 둘 다에 있으면 **local 우선**(그 종목의 공개 줄은
  통째로 버린다 — 줄 단위로 섞으면 가중치 합 ≤ 1 이 깨질 수 있다) + 경고 한 줄. 공개 내보내기는
  이 함수를 쓰지 않는다(공개 줄만 읽는다 — D-P6-14).

설정 읽기는 하지 않는다(⑨ — 해석된 설정 객체를 인자로 받는다).
"""

from __future__ import annotations

import bisect
import hashlib
import json
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from kbj.config.trade import HsChapters, TradeMap, TradeRegions
from kbj.core.quality import Quality
from kbj.core.trade_rows import MapOrigin, StockMapRow

__all__ = [
    "MAP_SOURCE",
    "Problem",
    "Resolved",
    "cross_check",
    "db_check",
    "map_rows",
    "map_version",
    "resolve",
]

MAP_SOURCE: Final = "KBJ:trade_map"  # 자체 큐레이션(외부 출처 아님)


@dataclass(frozen=True)
class Problem:
    """검증 문제 한 건 — 값이 아니라 칸·코드만(공개 로그에 남겨도 되는 정보)."""

    code: str
    kind: str  # chapter_missing·region_missing·code_missing·hs_missing
    detail: str

    def line(self) -> str:
        return f"{self.code}: {self.kind} — {self.detail}"


def map_version(tm: TradeMap) -> str:
    """판 이름 — 해석된 내용(정렬된 JSON)의 sha256 앞 10자리."""
    body = json.dumps(tm.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    return f"v{tm.version}-{hashlib.sha256(body.encode()).hexdigest()[:10]}"


def map_rows(
    tm: TradeMap, *, origin: MapOrigin, version: str, as_of: datetime
) -> list[StockMapRow]:
    """매핑표 → `StockMapRow`(기본 키 오름차순). 근거는 {kind, note, rcept_no?} 그대로."""
    out: list[StockMapRow] = []
    for s in tm.stocks:
        ev = tuple({k: v for k, v in e.model_dump().items() if v is not None} for e in s.evidence)
        common = {
            "code": s.code,
            "name": s.name,
            "origin": origin,
            "reviewed": s.reviewed,
            "map_version": version,
            "source": MAP_SOURCE,
            "as_of": as_of,
            "quality": Quality.OK,
            "evidence": ev,
        }
        for i in s.items:
            out.append(
                StockMapRow(
                    hs=i.hs, flow=i.flow, region_id="", weight=i.weight,
                    countries=tuple(i.countries), **common,  # pyright: ignore[reportArgumentType]
                )
            )  # fmt: skip
        for r in s.regions:
            out.append(
                StockMapRow(
                    hs=r.hs6, flow="exp", region_id=r.region_id, weight=r.weight,
                    countries=(), **common,  # pyright: ignore[reportArgumentType]
                )
            )  # fmt: skip
    return sorted(out, key=lambda r: (r.code, r.hs, r.flow, r.region_id))


def cross_check(tm: TradeMap, *, chapters: HsChapters, regions: TradeRegions) -> list[Problem]:
    """파일 사이 대조(CI·`trade.map_load`). 빈 목록이면 통과."""
    known_ch = set(chapters.chapters)
    known_rg = regions.ids()
    out: list[Problem] = []
    for s in tm.stocks:
        for i in s.items:
            if i.hs[:2] not in known_ch:
                out.append(Problem(s.code, "chapter_missing", f"HS {i.hs} 의 장 {i.hs[:2]}"))
        for r in s.regions:
            if r.hs6[:2] not in known_ch:
                out.append(Problem(s.code, "chapter_missing", f"HS {r.hs6} 의 장 {r.hs6[:2]}"))
            if r.region_id not in known_rg:
                out.append(Problem(s.code, "region_missing", r.region_id))
    return out


def db_check(
    rows: Iterable[StockMapRow], *, listed: Collection[str], hs_codes: Collection[str]
) -> list[Problem]:
    """DB 대조(VM). listed = 상장사 종목코드, hs_codes = `pub_trade.hs_dim` 의 코드(모든 자릿수).
    HS 접두는 그것으로 시작하는 코드가 하나라도 있으면 있다고 본다."""
    hs_sorted = sorted(hs_codes)
    seen_codes: set[str] = set()
    out: list[Problem] = []
    for r in rows:
        if r.code not in seen_codes:
            seen_codes.add(r.code)
            if r.code not in listed:
                out.append(Problem(r.code, "code_missing", "상장사 목록에 없음"))
        if not _has_prefix(hs_sorted, r.hs):
            out.append(Problem(r.code, "hs_missing", f"HS {r.hs}"))
    return out


def _has_prefix(sorted_codes: Sequence[str], prefix: str) -> bool:
    i = bisect.bisect_left(sorted_codes, prefix)
    return i < len(sorted_codes) and sorted_codes[i].startswith(prefix)


@dataclass(frozen=True)
class Resolved:
    """종목마다 쓸 매핑 줄(local 우선) + 경고."""

    lines: Mapping[str, tuple[StockMapRow, ...]]
    warnings: tuple[str, ...]

    def codes(self) -> list[str]:
        return sorted(self.lines)


def resolve(rows: Iterable[StockMapRow]) -> Resolved:
    """public·local 행 → 종목별 줄. 같은 종목이 둘 다에 있으면 local 줄만(경고)."""
    by: dict[str, dict[str, list[StockMapRow]]] = {}
    for r in rows:
        by.setdefault(r.code, {}).setdefault(r.origin, []).append(r)
    lines: dict[str, tuple[StockMapRow, ...]] = {}
    warns: list[str] = []
    for code in sorted(by):
        got = by[code]
        if "local" in got:
            if "public" in got:
                warns.append(f"{code}: 공개·local 매핑이 둘 다 있다 — local 우선")
            pick = got["local"]
        else:
            pick = got["public"]
        lines[code] = tuple(sorted(pick, key=lambda r: (r.hs, r.flow, r.region_id)))
    return Resolved(lines=lines, warnings=tuple(warns))
