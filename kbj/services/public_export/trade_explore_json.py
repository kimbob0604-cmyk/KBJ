"""공개 수출입 탐색 파일(docs/p6_design.md §7, metrics §26·§27·§29 — 묶음 EXP).

- `trade/hs2.json` — HS2 장마다 최근 `PUBLIC_MONTHS`(24)개월 수출·수입 금액·중량·YoY 배열 +
  `regions`(공개 지역 파일이 있는 HS6 색인 — '지역' 탭 목록). 트리 파일마다 `weight_unfit`(중량 기준
  부적합 HS 접두 — config/trade.yaml price_volume).
- `trade/hs/<장>.json` — 그 장의 HS4·HS6 같은 배열(D-P6-18 — 장별로 쪼갠다) + HS4 별 수출 상위
  국가(품목×국가 표의 마지막 달, 상위 `TOP_COUNTRIES`).
- `trade/countries.json`(로그인 `/api/trade/countries` data 와 같은 모양 — 마지막 달),
  `trade/country/<ISO2>.json`(`/api/trade/countries/{cc}/items` 와 같은 모양 — 수출, HS4),
  `trade/region/<hs6>.json`(`/api/trade/regions?hs6=` 와 같은 모양), `trade/surge.json`·
  `trade/items104.json`(`/api/trade/surges`·`/items104` 와 같은 모양).
- 공개판에는 HS10 이 없다(`hs10_login_only: true` — 화면은 '로그인판에서 HS10' 한 줄). 크기 때문이지
  등급 때문이 아니다(관세청 공개 — DATA_TIERS §1).

이 패키지는 엔진을 import 하지 않는다(계약 ⑧) — 접두 값(그 행이 있으면 그 행, 없으면 바로 아래
접두의 합 — 이중 합산 금지)·YoY·비중 정의는 `kbj.engines.trade.explore` 와 같고,
`tests/unit/public_export/test_trade_explore_json.py` 가 두 쪽 값을 대조한다. 읽는 표는 `pub_trade`
월간·hs_dim·surge·item_group_monthly 뿐(SQL 은 `pub_` 스키마만 — `test_sql_schemas`). 품목은 장마다
한 번씩 읽는다(메모리 — 실데이터 행 수 실측 #57). 표가 비면 그 파일 묶음 0·이유 0(빈 파일·가짜 값을
만들지 않는다). 반올림하지 않는다(화면에서만).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any, Final

from kbj.config.trade import load_hs_chapters, load_trade, load_trade_items
from kbj.services.public_export.manifest import BuildOut, PublicFile, Quality, worst_quality
from kbj.services.public_export.pub_read import Query, Reader, Row

__all__ = [
    "PUBLIC_MONTHS",
    "QUERIES",
    "TOP_COUNTRIES",
    "build_trade_explore",
]

SOURCE: Final = "관세청(공공데이터포털)"
PUBLIC_MONTHS: Final = 24
TOP_COUNTRIES: Final = 10
NOTE_USD: Final = "금액은 미화 달러(원화 환산 없음 — 환율 미조정)"
NOTE_PUBLIC: Final = "공개판은 HS2·4·6 최근 24개월 — HS10·더 긴 기간은 로그인판"
_Q_ORDER: Final = {"ok": 0, "stale": 1, "estimated": 2, "invalid": 3}

Q_ITEM_LAST: Final = Query("trade_x_item_last", "SELECT max(ym) AS ym FROM pub_trade.item_monthly")
Q_CHAPTERS: Final = Query(
    "trade_x_chapters",
    "SELECT DISTINCT left(hs, 2) AS ch FROM pub_trade.item_monthly WHERE ym >= %(ym_from)s "
    "ORDER BY ch",
)
Q_ITEMS: Final = Query(
    "trade_x_items",
    "SELECT ym, hs, exp_usd, exp_kg, imp_usd, imp_kg, as_of, quality FROM pub_trade.item_monthly "
    "WHERE ym >= %(ym_from)s AND left(hs, 2) = %(ch)s ORDER BY ym, hs",
)
Q_HS_DIM: Final = Query(
    "trade_x_hs_dim", "SELECT hs, name_ko FROM pub_trade.hs_dim WHERE hs_len <= 6 ORDER BY hs"
)
Q_COUNTRY_LAST: Final = Query(
    "trade_x_country_last", "SELECT max(ym) AS ym FROM pub_trade.country_monthly"
)
Q_COUNTRIES: Final = Query(
    "trade_x_countries",
    "SELECT ym, country, country_name, exp_usd, imp_usd, bal_usd, as_of, quality "
    "FROM pub_trade.country_monthly WHERE ym IN (%(ym)s, %(ym_prev)s) ORDER BY ym, country",
)
Q_IC_LAST: Final = Query(
    "trade_x_item_country_last", "SELECT max(ym) AS ym FROM pub_trade.item_country_monthly"
)
Q_ITEM_COUNTRIES: Final = Query(
    "trade_x_item_countries",
    "SELECT ym, country, hs, exp_usd, exp_kg, as_of, quality FROM pub_trade.item_country_monthly "
    "WHERE ym IN (%(ym)s, %(ym_prev)s) ORDER BY ym, country, hs",
)
Q_SIGUNGU_LAST: Final = Query(
    "trade_x_sigungu_last", "SELECT max(ym) AS ym FROM pub_trade.sigungu_monthly"
)
Q_SIGUNGU: Final = Query(
    "trade_x_sigungu",
    "SELECT ym, sido_cd, sgg_nm, hs6, region_id, exp_usd, as_of, quality "
    "FROM pub_trade.sigungu_monthly WHERE ym IN (%(ym)s, %(ym_prev)s) "
    "ORDER BY ym, hs6, sido_cd, sgg_nm",
)
Q_SURGE_LAST: Final = Query("trade_x_surge_last", "SELECT max(ym) AS ym FROM pub_trade.surge")
Q_SURGE: Final = Query(
    "trade_x_surge",
    "SELECT ym, level, key, flow, rule, yoy, yoy_3m, usd, rank, as_of, quality "
    "FROM pub_trade.surge WHERE ym = %(ym)s ORDER BY level, flow, rule, rank",
)
Q_GROUP_LAST: Final = Query(
    "trade_x_group_last", "SELECT max(ym) AS ym FROM pub_trade.item_group_monthly"
)
Q_GROUPS: Final = Query(
    "trade_x_groups",
    "SELECT ym, group_id, exp_usd, yoy, unit_price, mode, as_of, quality "
    "FROM pub_trade.item_group_monthly WHERE ym = %(ym)s ORDER BY group_id",
)
QUERIES: Final[tuple[Query, ...]] = (
    Q_ITEM_LAST, Q_CHAPTERS, Q_ITEMS, Q_HS_DIM, Q_COUNTRY_LAST, Q_COUNTRIES, Q_IC_LAST,
    Q_ITEM_COUNTRIES, Q_SIGUNGU_LAST, Q_SIGUNGU, Q_SURGE_LAST, Q_SURGE, Q_GROUP_LAST, Q_GROUPS,
)  # fmt: skip
SURGE_ORDER: Final = ("hs2", "hs4", "hs6", "hs10", "country")

_Cell = tuple[Decimal | None, Decimal | None, str]  # (usd, kg, quality)


def prev_ym(ym: str, months: int = 1) -> str:
    i = int(ym[:4]) * 12 + int(ym[5:]) - 1 - months
    return f"{i // 12:04d}-{i % 12 + 1:02d}"


def _dec(v: object) -> Decimal | None:
    if v is None:
        return None
    return v if isinstance(v, Decimal) else Decimal(str(v))


def _int(v: object) -> int | None:
    d = _dec(v)
    return None if d is None else int(d)


def _float(v: object) -> float | None:
    return None if v is None else float(v)  # pyright: ignore[reportArgumentType]


def _yoy(cur: Decimal | None, prev: Decimal | None) -> float | None:
    if cur is None or prev is None or prev == 0:
        return None
    return float(cur / prev) - 1.0


def _share(part: Decimal | None, total: Decimal | None) -> float | None:
    if part is None or total is None or total == 0:
        return None
    return float(part / total)


def _worst(qs: Iterable[object]) -> Quality:
    return worst_quality([str(q) for q in qs])


def _last(read: Reader, q: Query) -> str | None:
    rows = read(q, {})
    return rows[0]["ym"] if rows and rows[0]["ym"] is not None else None


def _sum_known(vals: Sequence[Decimal | None]) -> tuple[Decimal | None, bool]:
    got = [v for v in vals if v is not None]
    if not got:
        return None, True
    return sum(got, Decimal(0)), len(got) < len(vals)


class _Month:
    """한 달 접두 → (usd, kg, quality) — 엔진 `explore.Month.value` 와 같은 규칙."""

    def __init__(self, cells: Mapping[str, _Cell]) -> None:
        self._cells = dict(cells)
        self._memo: dict[str, _Cell | None] = {}

    def codes(self) -> list[str]:
        return list(self._cells)

    def below(self, p: str) -> list[str]:
        return [c for c in self._cells if c.startswith(p) and c != p]

    def value(self, p: str) -> _Cell | None:
        if p in self._memo:
            return self._memo[p]
        out = self._cells.get(p)
        if out is None:
            kids = sorted({c[: len(p) + 2] for c in self.below(p)})
            parts = [v for v in (self.value(k) for k in kids) if v is not None]
            if parts:
                usd, g1 = _sum_known([x[0] for x in parts])
                kg, g2 = _sum_known([x[1] for x in parts])
                q = _worst(x[2] for x in parts)
                if (g1 or g2 or usd is None) and q == "ok":
                    q = "estimated"
                out = (usd, kg, q)
        self._memo[p] = out
        return out


def _months_index(rows: Iterable[Row], usd: str, kg: str) -> dict[str, _Month]:
    by: dict[str, dict[str, _Cell]] = {}
    for r in rows:
        if r["quality"] == "invalid":
            continue
        cell = by.setdefault(r["ym"], {})
        if r["hs"] in cell:
            raise ValueError(f"같은 달 같은 HS 행이 두 번 있다: {r['ym']} {r['hs']}")
        cell[r["hs"]] = (_dec(r[usd]), _dec(r[kg]), str(r["quality"]))
    return {ym: _Month(c) for ym, c in by.items()}


def _series(idx: Mapping[str, _Month], hs: str, months: Sequence[str]) -> dict[str, list[Any]]:
    def usd(m: str) -> Decimal | None:
        v = idx[m].value(hs) if m in idx else None
        return None if v is None else v[0]

    def kg(m: str) -> Decimal | None:
        v = idx[m].value(hs) if m in idx else None
        return None if v is None else v[1]

    return {
        "usd": [_int(usd(m)) for m in months],
        "kg": [_int(kg(m)) for m in months],
        "yoy": [_yoy(usd(m), usd(prev_ym(m, 12))) for m in months],
    }


def _codes(idx: Mapping[str, _Month], level: int, parent: str) -> list[str]:
    out: set[str] = set()
    for mo in idx.values():
        for c in mo.below(parent) if parent else mo.codes():
            if len(c) >= level:
                out.add(c[:level])
    return sorted(out)


def _envelope_stamp(rows: Sequence[Row]) -> tuple[datetime, Quality]:
    at: datetime = max(r["as_of"] for r in rows)
    return at, _worst(r["quality"] for r in rows)


# ── 품목(장별) ─────────────────────────────────────────────────────────────────────────────


def _code_entry(
    flows: Mapping[str, Mapping[str, _Month]], hs: str, months: Sequence[str], name: str | None
) -> dict[str, Any]:
    return {
        "hs": hs,
        "name": name,
        "exp": _series(flows["exp"], hs, months),
        "imp": _series(flows["imp"], hs, months),
        "has_children": any(mo.below(hs) for f in flows.values() for mo in f.values()),
    }


def _items(
    read: Reader,
    names: Mapping[str, str],
    top: Mapping[str, list[dict[str, Any]]],
    regions: list[dict[str, Any]],
) -> tuple[list[PublicFile], list[str]]:
    last = _last(read, Q_ITEM_LAST)
    if last is None:
        return [], []
    months = [prev_ym(last, k) for k in range(PUBLIC_MONTHS - 1, -1, -1)]
    unfit = list(load_trade().price_volume.weight_unfit)
    ym_from = prev_ym(months[0], 12)
    chapters = [r["ch"] for r in read(Q_CHAPTERS, {"ym_from": ym_from})]
    files: list[PublicFile] = []
    hs2: list[dict[str, Any]] = []
    hs2_rows: list[Row] = []
    for ch in chapters:
        rows = read(Q_ITEMS, {"ym_from": ym_from, "ch": ch})
        if not rows:
            continue
        flows = {
            "exp": _months_index(rows, "exp_usd", "exp_kg"),
            "imp": _months_index(rows, "imp_usd", "imp_kg"),
        }
        shown = [r for r in rows if r["ym"] >= months[0]]
        if not shown:
            continue
        hs2.append(_code_entry(flows, ch, months, names.get(ch)))
        hs2_rows += shown
        codes = [*_codes(flows["exp"], 4, ch), *_codes(flows["exp"], 6, ch)]
        codes += [
            c
            for c in [*_codes(flows["imp"], 4, ch), *_codes(flows["imp"], 6, ch)]
            if c not in codes
        ]
        at, q = _envelope_stamp(shown)
        files.append(
            PublicFile(
                name=f"trade/hs/{ch}.json",
                source=SOURCE,
                as_of=at,
                quality=q,
                data={
                    "chapter": ch,
                    "name": names.get(ch),
                    "months": months,
                    "hs10_login_only": True,
                    "weight_unfit": unfit,
                    "codes": [_code_entry(flows, c, months, names.get(c)) for c in sorted(codes)],
                    "countries": {k: v for k, v in top.items() if k.startswith(ch)},
                },
                notes=(NOTE_USD, NOTE_PUBLIC),
            )
        )
    if not hs2:
        return [], []
    at, q = _envelope_stamp(hs2_rows)
    files.insert(
        0,
        PublicFile(
            name="trade/hs2.json",
            source=SOURCE,
            as_of=at,
            quality=q,
            data={
                "months": months,
                "hs10_login_only": True,
                "weight_unfit": unfit,
                "codes": hs2,
                "regions": regions,
            },
            notes=(NOTE_USD, NOTE_PUBLIC),
        ),
    )
    return files, []


# ── 국가·품목×국가 ─────────────────────────────────────────────────────────────────────────


def _countries(read: Reader) -> tuple[list[PublicFile], dict[str, str]]:
    last = _last(read, Q_COUNTRY_LAST)
    if last is None:
        return [], {}
    rows = [
        r
        for r in read(Q_COUNTRIES, {"ym": last, "ym_prev": prev_ym(last, 12)})
        if r["quality"] != "invalid"
    ]
    by = {(r["ym"], r["country"]): r for r in rows}
    cur = [r for r in rows if r["ym"] == last]
    if not cur:
        return [], {}
    total = sum((d for r in cur if (d := _dec(r["exp_usd"])) is not None), Decimal(0))
    out: list[dict[str, Any]] = []
    for r in cur:
        p = by.get((prev_ym(last, 12), r["country"]))
        e, i = _dec(r["exp_usd"]), _dec(r["imp_usd"])
        out.append(
            {
                "country": r["country"],
                "country_name": r["country_name"],
                "exp_usd": _int(e),
                "imp_usd": _int(i),
                "bal_usd": _int(r["bal_usd"]),
                "exp_yoy": _yoy(e, _dec(p["exp_usd"]) if p else None),
                "imp_yoy": _yoy(i, _dec(p["imp_usd"]) if p else None),
                "exp_share": _share(e, total),
                "quality": r["quality"],
            }
        )
    out.sort(key=lambda x: (-(x["exp_usd"] if x["exp_usd"] is not None else -1), x["country"]))
    at, q = _envelope_stamp(cur)
    f = PublicFile(
        name="trade/countries.json",
        source=SOURCE,
        as_of=at,
        quality=q,
        data={"ym": last, "rows": out},
        notes=(NOTE_USD,),
    )
    return [f], {r["country"]: r["country_name"] for r in cur}


def _node(
    idx: Mapping[str, _Month], hs: str, ym: str, total: Decimal | None, name: str | None
) -> dict[str, Any] | None:
    mo = idx.get(ym)
    v = mo.value(hs) if mo else None
    if v is None:
        return None
    pm = idx.get(prev_ym(ym, 12))
    pv = pm.value(hs) if pm else None
    usd, kg, q = v
    return {
        "hs": hs,
        "name": name,
        "usd": _int(usd),
        "kg": _int(kg),
        "yoy": _yoy(usd, pv[0] if pv else None),
        "share": _share(usd, total),
        "unit_price": None if usd is None or kg is None or kg <= 0 else float(usd / kg),
        "has_children": bool(mo.below(hs)) if mo else False,
        "quality": q,
    }


def _item_countries(
    read: Reader, names: Mapping[str, str], cnames: Mapping[str, str]
) -> tuple[list[PublicFile], dict[str, list[dict[str, Any]]]]:
    """`trade/country/<ISO2>.json` + 장 파일에 실을 HS4 별 상위 국가."""
    last = _last(read, Q_IC_LAST)
    if last is None:
        return [], {}
    rows = read(Q_ITEM_COUNTRIES, {"ym": last, "ym_prev": prev_ym(last, 12)})
    by_cc: dict[str, list[Row]] = {}
    for r in rows:
        by_cc.setdefault(r["country"], []).append(r)
    files: list[PublicFile] = []
    per_hs4: dict[str, list[tuple[str, Decimal | None, float | None, str]]] = {}
    for cc, rs in sorted(by_cc.items()):
        idx = _months_index(rs, "exp_usd", "exp_kg")
        level = 4 if _codes(idx, 4, "") else 2
        codes = _codes(idx, level, "")
        vals = {c: idx[last].value(c) if last in idx else None for c in codes}
        total, _ = _sum_known([v[0] if v else None for v in vals.values()])
        nodes = [n for c in codes if (n := _node(idx, c, last, total, names.get(c))) is not None]
        for n in nodes:
            if level == 4:
                per_hs4.setdefault(n["hs"], []).append((cc, _dec(n["usd"]), n["yoy"], n["quality"]))
        cur = [r for r in rs if r["ym"] == last]
        if not nodes or not cur:
            continue
        nodes.sort(key=lambda x: (-(x["usd"] if x["usd"] is not None else -1), x["hs"]))
        at, q = _envelope_stamp(cur)
        files.append(
            PublicFile(
                name=f"trade/country/{cc}.json",
                source=SOURCE,
                as_of=at,
                quality=q,
                data={
                    "country": cc,
                    "country_name": cnames.get(cc),
                    "ym": last,
                    "flow": "exp",
                    "rows": nodes,
                },
                notes=(NOTE_USD,),
            )
        )
    top: dict[str, list[dict[str, Any]]] = {}
    for hs4, lst in per_hs4.items():
        total, _ = _sum_known([u for _, u, _, _ in lst])
        lst.sort(key=lambda t: (-(t[1] if t[1] is not None else Decimal(-1)), t[0]))
        top[hs4] = [
            {
                "country": cc,
                "country_name": cnames.get(cc),
                "usd": _int(u),
                "yoy": y,
                "share": _share(u, total),
                "quality": q,
            }
            for cc, u, y, q in lst[:TOP_COUNTRIES]
        ]
    return files, top


# ── 지역 ───────────────────────────────────────────────────────────────────────────────────


def _region_key(r: Row) -> tuple[str, str]:
    rid = r["region_id"]
    return (rid if rid else f"{r['sido_cd']}:{r['sgg_nm']}", r["hs6"])


def _regions(read: Reader) -> list[PublicFile]:
    last = _last(read, Q_SIGUNGU_LAST)
    if last is None:
        return []
    rows = [
        r
        for r in read(Q_SIGUNGU, {"ym": last, "ym_prev": prev_ym(last, 12)})
        if r["quality"] != "invalid"
    ]
    prev = {_region_key(r): r for r in rows if r["ym"] != last}
    by: dict[str, list[Row]] = {}
    for r in rows:
        if r["ym"] == last:
            by.setdefault(r["hs6"], []).append(r)
    files: list[PublicFile] = []
    for hs6, cur in sorted(by.items()):
        total, _ = _sum_known([_dec(r["exp_usd"]) for r in cur])
        out: list[dict[str, Any]] = []
        for r in cur:
            p = prev.get(_region_key(r))
            e = _dec(r["exp_usd"])
            out.append(
                {
                    "region_id": r["region_id"],
                    "sido_cd": r["sido_cd"],
                    "sgg_nm": r["sgg_nm"],
                    "hs6": hs6,
                    "exp_usd": _int(e),
                    "exp_yoy": _yoy(e, _dec(p["exp_usd"]) if p else None),
                    "share": _share(e, total),
                    "quality": r["quality"],
                }
            )
        out.sort(
            key=lambda x: (
                -(x["exp_usd"] if x["exp_usd"] is not None else -1), x["sido_cd"], x["sgg_nm"],
            )
        )  # fmt: skip
        at, q = _envelope_stamp(cur)
        files.append(
            PublicFile(
                name=f"trade/region/{hs6}.json",
                source=SOURCE,
                as_of=at,
                quality=q,
                data={"hs6": hs6, "sido": None, "ym": last, "rows": out},
                notes=(NOTE_USD, "비중 = 같은 HS6 안 시군구 수출 비중"),
            )
        )
    return files


# ── 급등·104품목 ───────────────────────────────────────────────────────────────────────────


def _surge(read: Reader, names: Mapping[str, str], cnames: Mapping[str, str]) -> list[PublicFile]:
    last = _last(read, Q_SURGE_LAST)
    if last is None:
        return []
    rows = read(Q_SURGE, {"ym": last})
    if not rows:
        return []
    rows = sorted(
        rows, key=lambda r: (SURGE_ORDER.index(r["level"]), r["flow"], r["rule"], r["rank"])
    )
    cfg = load_trade().surge
    at, q = _envelope_stamp(rows)
    data = {
        "ym": last,
        "level": None,
        "min_usd": cfg.min_usd,
        "rows": [
            {
                "level": r["level"],
                "key": r["key"],
                "name": (cnames if r["level"] == "country" else names).get(r["key"]),
                "flow": r["flow"],
                "rule": r["rule"],
                "yoy": _float(r["yoy"]),
                "yoy_3m": _float(r["yoy_3m"]),
                "usd": _int(r["usd"]),
                "rank": r["rank"],
                "quality": r["quality"],
            }
            for r in rows
        ],
    }
    return [
        PublicFile(
            name="trade/surge.json",
            source=SOURCE,
            as_of=at,
            quality=q,
            data=data,
            notes=(NOTE_USD,),
        )
    ]


def _items104(read: Reader, names: Mapping[str, str]) -> list[PublicFile]:
    last = _last(read, Q_GROUP_LAST)
    if last is None:
        return []
    rows = read(Q_GROUPS, {"ym": last})
    if not rows:
        return []
    mode = "top_n" if any(r["mode"] == "top_n" for r in rows) else "fixed"
    rows = [r for r in rows if r["mode"] == mode]
    fixed = {g.id: g.name for g in load_trade_items().groups}

    def name(gid: str) -> str:
        if gid in fixed:
            return fixed[gid]
        if gid.startswith("hs"):
            return names.get(gid[2:]) or f"HS {gid[2:]}"
        return gid

    total, _ = _sum_known([_dec(r["exp_usd"]) for r in rows])
    out = [
        {
            "group_id": r["group_id"],
            "name": name(r["group_id"]),
            "exp_usd": _int(r["exp_usd"]),
            "yoy": _float(r["yoy"]),
            "unit_price": _float(r["unit_price"]),
            "share": _share(_dec(r["exp_usd"]), total),
            "quality": r["quality"],
        }
        for r in rows
    ]
    out.sort(key=lambda x: (-(x["exp_usd"] if x["exp_usd"] is not None else -1), x["group_id"]))
    at, q = _envelope_stamp(rows)
    note = (
        "자동 선택 — 직전 12개월 수출 상위 HS4(매달 바뀔 수 있다)"
        if mode == "top_n"
        else "자체 고정 HS4 목록(config/knowledge/trade_items.yaml)"
    )
    return [
        PublicFile(
            name="trade/items104.json",
            source=SOURCE,
            as_of=at,
            quality=q,
            data={"ym": last, "mode": mode, "auto_selected": mode == "top_n", "rows": out},
            notes=(NOTE_USD, note),
        )
    ]


def build_trade_explore(read: Reader, *, now: datetime) -> BuildOut:
    """공개 탐색 파일(표가 비면 그 묶음 파일 0·이유 0). 기간은 저장된 마지막 기준 월에서 센다
    (시계와 무관 — 결정적)."""
    del now
    names: dict[str, str] = dict(load_hs_chapters().chapters)
    names |= {r["hs"]: r["name_ko"] for r in read(Q_HS_DIM, {})}
    country_files, cnames = _countries(read)
    ic_files, top = _item_countries(read, names, cnames)
    region_files = _regions(read)
    # 공개판 '지역' 탭의 HS6 목록(hs2.json 에 싣는다 — 지역 파일은 HS6 마다 하나라 색인이 없다)
    index = [
        {
            "hs6": str(f.data["hs6"]),
            "name": names.get(str(f.data["hs6"])),
            "ym": f.data["ym"],
            "rows": len(f.data["rows"]),
        }
        for f in region_files
    ]
    item_files, skipped = _items(read, names, top, index)
    builders: Sequence[Callable[[], list[PublicFile]]] = (
        lambda: _surge(read, names, cnames),
        lambda: _items104(read, names),
    )
    files = [*item_files, *country_files, *ic_files, *region_files]
    for b in builders:
        files += b()
    return BuildOut(files=tuple(files), skipped=tuple(skipped))
