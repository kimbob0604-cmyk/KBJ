"""수출 104품목 엔진(순수 — 계약 ⑨, docs/p6_design.md §4.4, D-P6-9, metrics §29).

- `fixed`: `config/knowledge/trade_items.yaml` 묶음(자체 이름 + HS 접두 목록)을 그대로
  합산 — 접두 값은
  `explore.Month.value`(이중 합산 금지) 규칙. 묶음 사이에 접두가 겹치면(한 쪽이 다른 쪽의 접두)
  설정 오류 `GroupOverlap`(같은 금액을 두 묶음이 센다).
- `top_n`: 기준 월까지 직전 `months`(12)개월 수출액 합 상위 HS4 n 개(동률은 코드 순). 매달 바뀔 수
  있어 화면이 '자동 선택' 꼬리표를 붙인다. 묶음 id = `hs<HS4>`, 이름 = HS 이름(없으면 'HS <코드>').
  설정 파일이 비면(`groups: []`) 엔진이 `top_n` 으로 고른다(D-P6-9).
- 행: 묶음·달마다 수출·수입 금액·중량, 수출 YoY(전년 같은 달), 수출 단가(달러/kg). 그 달 값이 하나도
  없는 묶음은 행을 만들지 않는다(0 으로 채우지 않는다). 접두 일부만 값이 있으면 `estimated`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Final, Literal

from kbj.config.trade import ItemGroup
from kbj.core.quality import Quality
from kbj.core.trade_rows import ItemGroupMonthRow, prev_ym
from kbj.engines.trade.explore import Amount, ItemIndex, worst, yoy
from kbj.engines.trade.price_volume import unit_price

__all__ = [
    "SOURCE",
    "Group",
    "GroupOverlap",
    "check_groups",
    "group_rows",
    "groups_from_config",
    "top_n_groups",
]

SOURCE: Final = "KBJ:trade.derive"
Mode = Literal["fixed", "top_n"]


class GroupOverlap(ValueError):
    """묶음 사이 접두가 겹친다(metrics §29 — 설정 오류)."""


@dataclass(frozen=True)
class Group:
    id: str
    name: str
    hs: tuple[str, ...]


def groups_from_config(groups: Sequence[ItemGroup]) -> list[Group]:
    out = [Group(id=g.id, name=g.name, hs=tuple(g.hs)) for g in groups]
    check_groups(out)
    return out


def check_groups(groups: Sequence[Group]) -> None:
    """id 중복·접두 겹침(같은 묶음 안 포함)이면 `GroupOverlap`."""
    seen: set[str] = set()
    owner: list[tuple[str, str]] = []
    for g in groups:
        if g.id in seen:
            raise GroupOverlap(f"묶음 id 가 겹친다: {g.id}")
        seen.add(g.id)
        for p in g.hs:
            for q, gid in owner:
                if p.startswith(q) or q.startswith(p):
                    raise GroupOverlap(f"묶음 접두가 겹친다: {gid}:{q} · {g.id}:{p}")
            owner.append((p, g.id))


def top_n_groups(
    exp: ItemIndex, names: Mapping[str, str], *, ym: str, n: int, months: int
) -> list[Group]:
    """기준 월까지 직전 months 개월 수출 합 상위 HS4 n 개(동률 코드 순).

    한 달이라도 값이 있는 HS4 만."""
    window = [prev_ym(ym, k) for k in range(months)]
    totals: list[tuple[Decimal, str]] = []
    for hs4 in exp.codes_at(4):
        vals = [exp.usd(hs4, m) for m in window]
        got = [v for v in vals if v is not None]
        if got:
            totals.append((sum(got, Decimal(0)), hs4))
    totals.sort(key=lambda t: (-t[0], t[1]))
    return [
        Group(id=f"hs{hs4}", name=names.get(hs4) or f"HS {hs4}", hs=(hs4,)) for _, hs4 in totals[:n]
    ]


def _group_amount(idx: ItemIndex, g: Group, ym: str) -> tuple[Amount | None, bool]:
    """(묶음 합, 접두 일부 빠짐)."""
    parts = [idx.value(p, ym) for p in g.hs]
    got = [a for a in parts if a is not None]
    if not got:
        return None, True
    usd_vals = [a.usd for a in got if a.usd is not None]
    kg_vals = [a.kg for a in got if a.kg is not None]
    usd = sum(usd_vals, Decimal(0)) if usd_vals else None
    kg = sum(kg_vals, Decimal(0)) if kg_vals else None
    gap = len(got) < len(parts) or len(usd_vals) < len(got)
    return Amount(usd=usd, kg=kg, quality=worst(a.quality for a in got) or Quality.OK), gap


def group_rows(
    groups: Sequence[Group],
    exp: ItemIndex,
    imp: ItemIndex,
    *,
    months: Sequence[str],
    mode: Mode,
    as_of: datetime,
) -> list[ItemGroupMonthRow]:
    """묶음 × 달 행(값이 하나도 없는 칸은 만들지 않는다)."""
    check_groups(groups)
    out: list[ItemGroupMonthRow] = []
    for g in groups:
        for ym in months:
            e, gap_e = _group_amount(exp, g, ym)
            i, gap_i = _group_amount(imp, g, ym)
            eu = e.usd if e else None
            iu = i.usd if i else None
            if eu is None and iu is None:
                continue
            e_prev, gap_p = _group_amount(exp, g, prev_ym(ym, 12))
            q = worst([a.quality for a in (e, i) if a is not None]) or Quality.OK
            if (gap_e or gap_i) and q is Quality.OK:
                q = Quality.ESTIMATED
            out.append(
                ItemGroupMonthRow(
                    ym=ym,
                    group_id=g.id,
                    exp_usd=eu,
                    exp_kg=e.kg if e else None,
                    imp_usd=iu,
                    imp_kg=i.kg if i else None,
                    yoy=None if gap_e or gap_p or e_prev is None else yoy(eu, e_prev.usd),
                    unit_price=unit_price(eu, e.kg if e else None),
                    mode=mode,
                    source=SOURCE,
                    as_of=as_of,
                    quality=q,
                )
            )
    return out
