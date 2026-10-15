"""합성 테마 마스터 — stock-dashboard `data/master/` 파일 일곱과 같은 **모양**의 고정 시드 합성
데이터.

근거: ADR 0039(테마 마스터 받아 쓰기), docs/metrics.md §31. 공개 레포라 실제 stock-dashboard 마스터
내용(네이버 유래 테마 이름·편입·사유·업종, 사용자가 만든 대테마 이름)은 넣지 않는다 — 이름은 전부
'합성'·'가상', 종목코드는 합성 대역(`900000`~`902990` 10 간격 = 신고가 골든 합성 300종목, `93xxxx` =
이 생성기 전용)이다. `random.Random(f"{seed}:{이름}")` 으로 결정적으로 만든다(같은 시드 → 같은
바이트).

쓰는 법
    from tests.fixtures.synthetic.theme_master import build_files
    files = build_files()                      # {파일 이름: 바이트} — 기본 설정 하한을 넘는 크기
    files = build_files(n_stocks=2100, drop_members=0.5)   # 편입을 절반으로(감소율 거부 시험)

크기(기본): 대테마 36(섹션 6·밸류체인 4 — 일부에만), 원본 테마 180(대테마에 걸림·제외 7·어디에도
없음 — 경고로 남는다), 종목 2,100(보통주·우선주·리츠), 편입 약 3,600줄(원본 테마마다 20).
"""

from __future__ import annotations

import csv
import io
import json
import random
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Final

from kbj.core.time import KST

__all__ = [
    "ANCHOR_CODES",
    "BUILT_AT",
    "SECTIONS",
    "SEED",
    "build_files",
    "theme_ids",
]

SEED: Final = 20261015
BUILT_AT: Final = datetime(2026, 10, 11, 6, 3, 10, tzinfo=KST)  # 일요일 갱신(합성)
# 신고가 골든 합성 300종목(tests/golden/board — 합성종목000~299, 코드 900000·900010·…·902990)
ANCHOR_CODES: Final[tuple[str, ...]] = tuple(f"{900000 + 10 * i:06d}" for i in range(300))
SECTIONS: Final[tuple[str, ...]] = ("기술", "에너지", "소비", "산업", "의료", "금융")
_PER_SECTION: Final[tuple[int, ...]] = (9, 8, 6, 5, 4, 4)  # 합 36
_CHAINS: Final[tuple[str, ...]] = ("CHAIN_A", "CHAIN_B", "CHAIN_C", "CHAIN_D")
_GROUPS: Final[tuple[tuple[str, str, tuple[str, ...]], ...]] = (
    ("g_semi", "가상 반도체", ("가상업종01",)),
    ("g_hw", "가상 하드웨어", ("가상업종02", "가상업종03")),
    ("g_sw", "가상 소프트웨어", ("가상업종04",)),
    ("g_energy", "가상 에너지", ("가상업종05", "가상업종06")),
    ("g_consumer", "가상 소비재", ("가상업종07",)),
    ("g_industrial", "가상 산업재", ("가상업종08", "가상업종09")),
    ("g_health", "가상 헬스케어", ("가상업종10",)),
    ("g_fin", "가상 금융", ("가상업종11",)),
)
_N_NAVER: Final = 180
_EXCLUDED: Final[tuple[int, ...]] = (171, 172, 173, 174, 175, 176, 177)


def theme_ids() -> list[str]:
    """대테마 id(원천 순서) — `syn_<섹션 번호>_<번호>`."""
    return [f"syn_{s}_{k}" for s, n in enumerate(_PER_SECTION, start=1) for k in range(1, n + 1)]


def _rng(seed: int, name: str) -> random.Random:
    return random.Random(f"{seed}:{name}")  # noqa: S311 — 합성 데이터(암호 용도 아님)


def _csv(header: Sequence[str], rows: Sequence[Sequence[object]]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue().encode("utf-8")


def _json(obj: Any) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, indent=1) + "\n").encode("utf-8")


def _stocks(seed: int, n: int, anchor_names: dict[str, str]) -> list[list[str]]:
    r = _rng(seed, "stocks")
    codes = list(ANCHOR_CODES) + [f"{930000 + i:06d}" for i in range(max(0, n - len(ANCHOR_CODES)))]
    rows: list[list[str]] = []
    for i, code in enumerate(codes[:n]):
        g = _GROUPS[i % len(_GROUPS)]
        kind = "common"
        parent = ""
        if i >= len(ANCHOR_CODES) and i % 40 == 7:
            kind, parent = "preferred", codes[i - 1]
        elif i >= len(ANCHOR_CODES) and i % 97 == 11:
            kind = "reit"
        name = anchor_names.get(code, f"가상종목{i:04d}")
        if kind == "preferred":
            name = f"{name}우"
        rows.append([
            code, name, "KOSPI" if r.random() < 0.35 else "KOSDAQ", kind, parent,
            g[2][i % len(g[2])], g[0], r.choice(("A", "B", "C", "")), "",
        ])  # fmt: skip
    return rows


def build_files(
    *,
    seed: int = SEED,
    built_at: datetime = BUILT_AT,
    n_stocks: int = 2100,
    per_theme: int = 20,
    drop_members: float = 0.0,
    anchor_names: dict[str, str] | None = None,
) -> dict[str, bytes]:
    """파일 이름 → 바이트(stock-dashboard data/master/ 와 같은 이름·칸). `drop_members` = 편입 줄을
    이 비율만큼 뺀다(감소율 거부 시험)."""
    names = anchor_names or {}
    stocks = _stocks(seed, n_stocks, names)
    codes = [s[0] for s in stocks]
    anchors = [c for c in codes if c in set(ANCHOR_CODES)]
    others = [c for c in codes if c not in set(ANCHOR_CODES)]

    # 원본 테마(번호 1~180)와 편입
    rm = _rng(seed, "members")
    naver: list[dict[str, Any]] = []
    members: list[list[object]] = []
    for no in range(1, _N_NAVER + 1):
        k_anchor = min(len(anchors), max(1, int(per_theme * 0.7)))
        picked = rm.sample(anchors, k_anchor) + rm.sample(
            others, min(len(others), per_theme - k_anchor)
        )
        picked = sorted(set(picked))
        for code in picked:
            members.append([no, code, f"합성 편입 사유 {no}-{code[-3:]}"])
        naver.append({
            "no": no, "name": f"합성 원본 테마 {no:03d}",
            "description": f"합성 설명 {no:03d} — 실데이터 아님",
            "count": len(picked) + rm.randint(0, 4), "in_master": len(picked),
        })  # fmt: skip
    if drop_members > 0:
        keep = int(len(members) * (1.0 - drop_members))
        members = members[:keep]

    # 대테마(섹션 6 · 36개) — 원본 테마 1~170 을 3~6개씩(조금 겹치게)
    rt = _rng(seed, "themes")
    themes: list[dict[str, Any]] = []
    placed = list(range(1, 171))
    cursor = 0
    ids = theme_ids()
    for i, tid in enumerate(ids):
        sec = SECTIONS[int(tid.split("_")[1]) - 1]
        k = 3 + (i % 4)
        nos = [placed[(cursor + j) % len(placed)] for j in range(k)]
        cursor += k - (1 if i % 6 == 0 else 0)  # 가끔 한 칸 겹친다 — 원본 테마 하나가 대테마 둘에
        t: dict[str, Any] = {
            "id": tid,
            "name": f"{sec} 합성 테마 {tid.rsplit('_', 1)[1]}",
            "section": sec,
        }
        if i % 5 in (0, 2) and len([x for x in themes if "valuechain" in x]) < 14:
            t["valuechain"] = _CHAINS[rt.randrange(len(_CHAINS))]
        t["naver"] = [{"no": no, "name": f"합성 원본 테마 {no:03d}"} for no in sorted(set(nos))]
        themes.append(t)
    excluded = [{"no": no, "name": f"합성 원본 테마 {no:03d}"} for no in _EXCLUDED]

    meta = {
        "_note": "합성 — tests/fixtures/synthetic/theme_master.py(실데이터 아님)",
        "built_at": built_at.isoformat(),
        "source": "synthetic",
        "total": len(stocks),
    }
    return {
        "meta.json": _json(meta),
        "themes.json": _json({"_note": "합성", "themes": themes, "excluded_naver": excluded}),
        "naver_themes.json": _json({"_note": "합성", "themes": naver}),
        "naver_theme_members.csv": _csv(("theme_no", "code", "reason"), members),
        "stocks.csv": _csv(
            (
                "code",
                "name",
                "market",
                "type",
                "parent_code",
                "industry",
                "industry_group",
                "tier",
                "former_names",
            ),
            stocks,
        ),
        "industry_groups.json": _json(
            {
                "_note": "합성",
                "groups": [{"id": g, "name": n, "industries": list(ind)} for g, n, ind in _GROUPS],
            }
        ),
        "type_overrides.csv": _csv(
            ("code", "type", "reason"),
            [[others[11], "reit", "합성 — 이름에 리츠가 없는 리츠"]] if len(others) > 11 else [],
        ),
    }
