"""
랭킹 표 — 사용자가 쓰던 엑셀을 그대로 만든다.

KBJ 정본(docs/p3_design.md §1.3). ET `board/engine/rankings.py` 를 그대로 옮기고 state 파일 읽기만
인자로 뺐다 — `build(asof, cfg, log)` 가 하던 계산은 `build_rankings(universe, prev_rankings, cfg,
taxonomy, …)` 다(전일 rankings·분류 사전·universe 를 부르는 쪽이 넘긴다). 표시용 금액 문자열은
ET `engine/facts.py:eok` 와 같은 규칙(`eok`).

만드는 표
  1. 섹터별 금일 상승률 순위 + 각 섹터 1~5등 종목
  2. 같은 형식의 7거래일 버전
  3. 종목 랭킹 두 개 (기간수익률 순 / 거래량 3일·1달 순)와 그 교집합

출력 계약은 docs/RANKINGS-CONTRACT.md 에 있고, 엑셀·대시보드·텔레그램 셋이
그 파일만 읽는다.

## 왜 LLM 을 쓰지 않는가

이 표는 전부 결정론적 계산이다. 정렬하고 세고 나누는 것뿐이다. 여기에 LLM 을
넣으면 느리고 비싸고 무엇보다 **틀린다**. CLAUDE.md 2장 3번이 금지한 바로 그것이다.
LLM 이 실제로 필요한 자리는 종목→섹터 배정(classify/) 하나뿐이고, 그건 하루치
계산이 아니라 1회성 사전 구축이다.

## 수치가 두 벌인 이유

`ret` 는 사람이 읽는 문자열, `ret_raw` 는 float 다. 엑셀 조건부 서식과 화면
정렬은 float 가 있어야 동작하고, 서술과 텔레그램은 문자열을 그대로 옮겨야
반올림이 끼어들지 않는다 (CLAUDE.md 7장 문체 규칙).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

__all__ = [
    "DEFAULTS",
    "ROWS_PER_BOARD",
    "SECTOR_PERIODS",
    "STOCK_BOARDS",
    "TOP_PER_SECTOR",
    "UNMAPPED",
    "build_rankings",
    "eok",
    "format_cell",
    "mark_cross",
    "mark_rank_delta",
    "sector_board",
    "stock_board",
]

UNMAPPED = "미분류"

# 섹터 표를 만들 기간. key 는 universe 레코드의 필드명이다.
SECTOR_PERIODS = [
    dict(key="1d", field="chg_pct", label="금일", ret_label="금일 상승률"),
    dict(key="7d", field="ret_7d", label="7거래일", ret_label="7거래일 상승률"),
]

# 종목 표 두 개. 사용자 엑셀의 좌우 표와 열 구성이 같다.
STOCK_BOARDS = [
    dict(
        key="ret_1w",
        title="1w 주가수익률 순위",
        sort_by="ret_5d",
        desc=True,
        columns=[
            ("mktcap", "시가총액(억)", "amount"),
            ("chg_pct", "1d", "pct"),
            ("ret_5d", "1w", "pct"),
            ("ret_21d", "1m", "pct"),
            ("vol_3d_1m", "거래량 3일/1달", "ratio"),
        ],
    ),
    dict(
        key="vol_3d_1m",
        title="거래량 3일/1달 순위",
        sort_by="vol_3d_1m",
        desc=True,
        columns=[
            ("mktcap", "시가총액(억)", "amount"),
            ("chg_pct", "1d", "pct"),
            ("ret_5d", "1w", "pct"),
            ("ret_10d", "2w", "pct"),
            ("vol_3d_1m", "거래량 3일/1달", "ratio"),
        ],
    ),
]

TOP_PER_SECTOR = 5
ROWS_PER_BOARD = 30

# 랭킹에서 뺄 하한. 없으면 시총 50억짜리 종목이 상승률 상위를 독식해
# 표가 쓸모없어진다. 값은 config/settings.yaml 의 rankings 로 뺀다.
# 섹터 순위에 남길 최소 구성종목 수. 3종목짜리 섹터의 시총가중 등락률은
# 48종목짜리와 같은 표에 놓을 수 없다 — 한 종목이 통째로 순위를 만든다.
# 2026-08-28 보드에서 '컴퓨터'(3종목)가 금일 1위로 올라온 것이 그 형태였다.
# 지우지 않고 '표본 부족'으로 따로 빼서 몇 개가 빠졌는지 알려 준다.
DEFAULTS = dict(min_mktcap_eok=300.0, min_turnover_eok=1.0, min_sector_members=5)


def _cfg(cfg):
    return {**DEFAULTS, **(cfg.get("rankings") or {})}


def eok(v):
    """억원. 1조 이상은 조로 접는다. 레퍼런스가 '1.28조' / '3,240억' 둘 다 쓴다.

    ET `board/engine/facts.py:eok`:37 과 같은 규칙(랭킹 표의 금액 칸).
    """
    if v is None:
        return None
    if abs(v) >= 10000:
        return f"{v / 10000:,.2f}조"
    return f"{v:,.0f}억"


# ─────────────────────────── 표시용 렌더 ───────────────────────────
def _fmt(kind, v):
    """kind 별 표시 문자열. None 은 None 으로 둔다 — 0 으로 채우지 않는다."""
    if v is None:
        return None
    if kind == "pct":
        return f"{v:+.2f}%"
    if kind == "ratio":
        return f"{v:,.0f}%"
    if kind == "amount":
        return eok(v)
    return f"{v:,.0f}"


# 공개 이름(legacy shim 이 예전 이름 `_fmt` 로 다시 내보낸다)
format_cell = _fmt


# ─────────────────────────── 섹터 표 ───────────────────────────
def _sector_ret(items, field):
    """섹터 등락률. 시가총액 가중 평균.

    단순 평균으로 하면 시총 100억짜리 하나가 섹터 전체를 흔든다. 가중치가
    하나도 없으면 단순 평균으로 떨어지고, 그 사실은 board 의 weighting 에 남는다.
    """
    pairs = [(x[field], x.get("mktcap") or 0) for x in items if x.get(field) is not None]
    if not pairs:
        return None, "none"
    tw = sum(w for _, w in pairs)
    if tw > 0:
        return sum(v * w for v, w in pairs) / tw, "mktcap"
    return sum(v for v, _ in pairs) / len(pairs), "equal"


def sector_board(rows, spec, limit=TOP_PER_SECTOR, cfg=None) -> dict[str, Any]:
    field = spec["field"]
    groups = {}
    for x in rows:
        groups.setdefault(x.get("sector") or UNMAPPED, []).append(x)

    min_n = _cfg(cfg or {})["min_sector_members"]
    out, thin, weighting = [], [], "mktcap"
    for name, items in groups.items():
        if len(items) < min_n and name != UNMAPPED:
            thin.append(dict(name=name, n=len(items)))
            continue
        ret, how = _sector_ret(items, field)
        if how == "equal":
            weighting = "equal"
        top = sorted((x for x in items if x.get(field) is not None), key=lambda x: -x[field])[
            :limit
        ]
        # 값을 모르는 종목은 어느 쪽에도 넣지 않는다.
        vals = [x.get(field) for x in items]
        known = [v for v in vals if v is not None]
        up = sum(1 for v in known if v > 0)
        dn = sum(1 for v in known if v < 0)
        out.append(
            dict(
                name=name,
                ret=_fmt("pct", ret),
                ret_raw=None if ret is None else round(ret, 2),
                n=len(items),
                breadth=dict(
                    up=up,
                    flat=len(known) - up - dn,
                    down=dn,
                    total=len(known),
                    unknown=len(vals) - len(known),
                ),
                top=[
                    dict(
                        rank=i + 1,
                        code=x["code"],
                        name=x["name"],
                        ret=_fmt("pct", x[field]),
                        ret_raw=round(x[field], 2),
                        mktcap=eok(x.get("mktcap")),
                        mktcap_raw=x.get("mktcap"),
                    )
                    for i, x in enumerate(top)
                ],
            )
        )
    # 등락률이 없는 섹터는 맨 뒤로. 순위는 정렬 후에 매긴다.
    out.sort(key=lambda s: (s["ret_raw"] is None, -(s["ret_raw"] or 0)))
    for i, s in enumerate(out):
        s["rank"] = i + 1
    return dict(
        key=spec["key"],
        label=spec["label"],
        ret_label=spec["ret_label"],
        weighting=weighting,
        sectors=out,
        min_members=min_n,
        thin_sectors=sorted(thin, key=lambda t: -t["n"]),
    )


# ─────────────────────────── 종목 표 ───────────────────────────
def stock_board(rows, spec, limit=ROWS_PER_BOARD) -> dict[str, Any]:
    key = spec["sort_by"]
    cand = [x for x in rows if x.get(key) is not None]
    cand.sort(key=lambda x: -x[key] if spec["desc"] else x[key])
    out = []
    for i, x in enumerate(cand[:limit]):
        cells, raw = {}, {}
        for ck, _label, kind in spec["columns"]:
            v = x.get(ck)
            raw[ck] = v
            cells[ck] = _fmt(kind, v)
        out.append(
            dict(
                rank=i + 1, code=x["code"], name=x["name"], cross=False, cells=cells, cells_raw=raw
            )
        )
    return dict(
        key=spec["key"],
        title=spec["title"],
        sort_by=key,
        columns=[dict(key=k, label=lb, kind=kd) for k, lb, kd in spec["columns"]],
        rows=out,
    )


def mark_rank_delta(boards, prev):
    """섹터 순위에 전일 대비 변동을 붙인다.

    순위만 있으면 '오늘 반도체가 3등' 까지만 안다. 어제 12등이었는지 2등이었는지에
    따라 같은 3등이 전혀 다른 이야기다. 전일 `rankings.json` 의 같은 표에서 같은
    이름을 찾아 뺀다.

    **어제 표에 없던 섹터는 0 이 아니라 None 이다.** 구성종목 수가 하한을 넘나들면
    섹터가 표에 들어왔다 빠졌다 하는데, 그걸 '변동 없음' 으로 적으면 없던 사실을
    단정하는 것이다(규칙 1). 화면은 None 을 '–' 로 둔다.

    부호는 **올라간 쪽이 양수**다. 순위 숫자는 작아지는 게 좋은 방향이라 그대로
    빼면 부호가 뒤집힌다.
    """
    prev_by_key = {}
    for b in (prev or {}).get("sector_boards") or []:
        prev_by_key[b.get("key")] = {
            s.get("name"): s.get("rank")
            for s in b.get("sectors") or []
            if s.get("rank") is not None
        }
    n = 0
    for b in boards:
        pmap = prev_by_key.get(b.get("key")) or {}
        b["has_prev_rank"] = bool(pmap)
        for s in b.get("sectors") or []:
            was = pmap.get(s["name"])
            s["rank_prev"] = was
            s["rank_delta"] = None if was is None else was - s["rank"]
            if was is not None:
                n += 1
    return n


def mark_cross(boards):
    """두 표에 모두 등장한 종목을 표시한다.

    수익률 상위이면서 거래량도 며칠째 붙어 있는 종목이 진짜 시그널이다.
    한쪽에만 뜬 건 하루짜리 튐이거나 이미 지나간 것이다. 텔레그램은 이 교집합만
    보낸다 — 그게 전체 표를 다시 보내는 것보다 나은 유일한 지점이다.
    """
    if len(boards) < 2:
        return []
    sets = [{r["code"] for r in b["rows"]} for b in boards]
    cross = set.intersection(*sets)
    for b in boards:
        for r in b["rows"]:
            r["cross"] = r["code"] in cross
    return sorted(cross)


# ─────────────────────────── 조립 ───────────────────────────
def build_rankings(
    universe: Mapping[str, Any] | None,
    prev_rankings: Mapping[str, Any] | None,
    cfg: Mapping[str, Any],
    taxonomy: Mapping[str, Any] | None,
    *,
    asof: str,
    generated_at: str,
    newhigh: Mapping[str, Any] | None = None,
    sectors: Mapping[str, Any] | None = None,
    market: Mapping[str, Any] | None = None,
    log: Callable[[str], object] | None = None,
) -> dict[str, Any]:
    """그날 rankings.json — ET `rankings.build`:217 의 계산 그대로, 파일 읽기만 인자로.

    universe       그날 universe.json(payload)
    newhigh        그날 newhigh.json(전일 날짜 `prev_asof`)
    sectors        그날 sectors.json(`taxonomy_layer1`)
    market         market.json(`missing`) — kbj 에는 없다(시장 화면은 kbj.engines.market)
    prev_rankings  전일 rankings.json. **newhigh 의 prev_asof 가 있을 때만** 대조한다(ET 와 같다)
    taxonomy       1층 분류 사전(config/knowledge/sectors.yaml)
    generated_at   주입한 시계의 시각(ISO) — 벽시계를 읽지 않는다
    """
    uni = universe or {}
    nhj = newhigh or {}
    sec = sectors or {}
    rows_all = uni.get("stocks") or []
    c = _cfg(cfg)

    # 유동성 하한. **값을 모르는 것과 하한 미달을 구분한다.**
    # `or 0` 으로 두면 시총을 못 받은 종목이 전부 '하한 미달'로 빠지고,
    # 그 사실이 "유동성 하한으로 N종목 제외" 한 줄에 섞여 안 보인다.
    # 2026-08-27 에 표 네 장이 통째로 빈 것이 정확히 이 형태였다.
    rows, thin, unknown = [], 0, 0
    for x in rows_all:
        cap, tv = x.get("mktcap"), x.get("turnover")
        if cap is None or tv is None:
            unknown += 1
        elif cap >= c["min_mktcap_eok"] and tv >= c["min_turnover_eok"]:
            rows.append(x)
        else:
            thin += 1
    dropped = thin

    tax = taxonomy or {}
    target = tax.get("meta", {}).get("taxonomy", "board48")
    # 목표 분류가 아니라 **실제로 붙어 있는 분류**를 보고한다. 목표를 그대로 적으면
    # 아직 분류를 안 돌린 날에도 board48 이라고 나와서 표가 왜 성긴지 알 수 없다.
    actual = sec.get("taxonomy_layer1") or "unknown"
    known = {s["name"] for s in tax.get("sectors") or []}
    unmapped = sum(1 for x in rows if (x.get("sector") or UNMAPPED) not in known)

    # **오류와 설계상 제외를 한 목록에 담으면 규칙 6 이 뒤집힌다.**
    # 매일 똑같이 나오는 'ETF 제외' '유동성 하한' 옆에 진짜 실패가 같은 빨간색으로
    # 끼면 눈이 그 줄을 넘긴다. 실제로 520101 의 corp_code 실패가 아홉 줄 가운데
    # 하나로 며칠 동안 그대로 있었다. 그래서 두 갈래로 나눠 싣는다.
    #   missing — 물어봤는데 답을 못 받았거나, 돌아야 할 것이 안 돈 것
    #   scope   — 우리가 정한 기준으로 걸러낸 것. 숫자가 튀면 봐야 하지만
    #             그 자체는 정상 동작이다
    # 둘 다 화면에 싣는다. 감추는 것이 아니라 색을 나누는 것이다.
    missing = list((market or {}).get("missing") or [])
    scope = []
    if uni.get("snapshot_stale"):
        missing.append(uni["snapshot_stale"])
    # `notes` 만 싣는다. `diagnostics`(엔진 자기검사 — 산출물 정합성, 단위 의심)는
    # 고치는 사람에게 갈 말이라 배너에 올리지 않는다. 버리지는 않는다 —
    # universe.json 과 run_log 에 그대로 있고 `--check` 가 그것을 읽는다.
    missing.extend(uni.get("notes") or [])
    # 펀드 필터가 실제로 돌았는지 보여 준다. 안 보이면 도는지 알 수 없다.
    ex = uni.get("funds_excluded")
    if ex:
        scope.append(f"ETF·ETN {ex} — 신고가 보드는 주식만 봅니다")
    else:
        missing.append("ETF·ETN 제외 기록 없음 — 필터가 돌지 않았을 수 있습니다")
    if unmapped:
        # 분류가 아직 안 붙은 것과, 붙었는데 신뢰도가 낮아 미분류로 남은 것은
        # 원인도 처방도 다르다. 같은 문구를 쓰면 --classify 를 이미 돌린 사람에게
        # 또 돌리라고 하게 된다 (2026-08-28 보드가 그랬다).
        if actual != target:
            missing.append(
                f"섹터 미배정 {unmapped:,}종목 — 목표 분류는 {target} 인데 실제로 붙어 "
                f"있는 분류는 {actual} 입니다. `python3 -m board.run --classify` 로 "
                "배정하세요"
            )
        else:
            # 신뢰도 0.7 미만을 미분류로 두는 것은 규칙 5 를 지킨 결과다. 실패가 아니다.
            scope.append(
                f"섹터 미배정 {unmapped:,}종목 — 분류({target})는 돌았고, 신뢰도가 "
                "기준 미만이라 미분류로 남은 종목입니다. "
                "`board/knowledge/sector_map.yaml` 에 사유가 있으니 손으로 고치면 됩니다"
            )
    if dropped:
        scope.append(
            f"유동성 하한(시총 {c['min_mktcap_eok']:,.0f}억 · "
            f"거래대금 {c['min_turnover_eok']:,.0f}억)으로 {dropped:,}종목 제외"
        )
    if unknown:
        missing.append(
            f"시가총액 또는 거래대금을 못 받은 종목 {unknown:,}개는 랭킹에서 뺐습니다 "
            f"— 하한 미달이 아니라 **값을 모르는 것**입니다. 소스 응답을 확인하세요"
        )

    sboards = [sector_board(rows, spec, cfg=cfg) for spec in SECTOR_PERIODS]
    thin_secs = {t["name"]: t["n"] for b in sboards for t in b["thin_sectors"]}
    if thin_secs:
        top = sorted(thin_secs.items(), key=lambda kv: -kv[1])[:5]
        scope.append(
            f"구성종목 {sboards[0]['min_members']}개 미만 섹터 {len(thin_secs)}개는 "
            "순위에서 뺐습니다 — 한 종목이 섹터 등락률을 통째로 만들어 다른 섹터와 "
            "비교가 안 됩니다 (" + ", ".join(f"{k} {v}종목" for k, v in top) + ")"
        )
    # 전일 표와 대조해 순위 변동을 붙인다. 전일 파일이 없으면 조용히 넘어가고
    # 화면은 변동 칸을 '–' 로 둔다 — 없는 비교를 0 으로 적지 않는다.
    prev_asof = nhj.get("prev_asof")
    n_delta = mark_rank_delta(sboards, prev_rankings if prev_asof else None)

    kboards = [stock_board(rows, spec) for spec in STOCK_BOARDS]
    cross = mark_cross(kboards)

    out = dict(
        as_of=asof,
        prev_asof=nhj.get("prev_asof"),
        market="KR",
        source=uni.get("source") or "unknown",
        generated_at=generated_at,
        taxonomy=actual,
        taxonomy_target=target,
        universe=len(rows),
        universe_all=len(rows_all),
        thresholds=c,
        missing=missing,
        scope=scope,
        dropped_thin=thin,
        dropped_unknown=unknown,
        sector_boards=sboards,
        stock_boards=kboards,
        cross_codes=cross,
    )
    (log or _quiet)(
        f"  랭킹 — 섹터표 {len(sboards)} · 종목표 {len(kboards)} · "
        f"교집합 {len(cross)}종목 · 대상 {len(rows):,}/{len(rows_all):,} · "
        f"전일 순위 대조 {n_delta}건"
    )
    return out


def _quiet(_msg: str) -> None:
    return None
