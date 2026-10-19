"""KOSIS 항목 코드 실측(#44) — `config/macro.yaml` 의 KOSIS 시리즈 표마다 최근 1개 시점을
'전체 분류·전체 항목'으로 받아 코드표를 찍는다. 값을 고르지 않는다: 사람이 표를 보고
`item: "<objL1>/<itmId>"` 를 적는다.

    python -m kbj.services.macro_probe          (로컬: bash scripts/local/kbj.sh probe-kosis)

- 키(`KBJ_KOSIS_KEY`)는 찍지 않는다 — 클라이언트가 오류 문구에서도 지운다.
- 표 하나가 실패해도(분류 단계가 둘 이상이라 objL2 가 필요한 표 등) 사유만 찍고 다음 표로 간다.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

from kbj.config.macro import load_macro
from kbj.config.settings import Settings
from kbj.data.http import FetchError
from kbj.data.public.kosis.client import KosisClient, KosisRow
from kbj.services.runtime.heartbeat import connect_redis

ORG = "101"
PRD = {"M": "M", "Q": "Q", "A": "Y"}
MAX_LINES = 60  # 표 하나에 찍는 줄 상한(분류가 많은 표)


def table_lines(rows: Sequence[KosisRow]) -> list[str]:
    """(분류 코드·이름…, 항목 코드·이름, 단위, 시점, 값) 한 줄씩 — 같은 조합은 한 번."""
    out: list[str] = []
    seen: set[tuple[object, ...]] = set()
    for r in rows:
        k = (r.classes, r.itm_id)
        if k in seen:
            continue
        seen.add(k)
        cls = " · ".join(f"C{i}={c}({n})" for i, (c, n) in enumerate(r.classes, start=1))
        out.append(f"  {cls} | itmId={r.itm_id}({r.itm_nm}) | {r.unit} | {r.prd_de}={r.raw_value}")
    return out


def main(argv: Sequence[str] | None = None) -> int:
    del argv
    settings = Settings()
    if settings.kosis_key is None:
        print("KBJ_KOSIS_KEY 가 없다 — bash scripts/local/kbj.sh keys 로 넣는다")
        return 1
    if settings.redis_url is None:
        print("KBJ_REDIS_URL 이 없다")
        return 1
    cfg = load_macro(settings=settings)
    tables: dict[str, list[str]] = {}
    for s in cfg.series:
        if s.source == "KOSIS":
            tables.setdefault(s.dataset.split(":", 1)[1], []).append(f"{s.id}[{s.freq}]")
    kosis = KosisClient.from_settings(settings, connect_redis(settings.redis_url))
    try:
        for tbl, ids in tables.items():
            freq = ids[0].rsplit("[", 1)[1].rstrip("]")
            print(f"▶ {tbl} — 쓰는 시리즈: {', '.join(ids)}")
            try:
                rows = kosis.parameter_data(ORG, tbl, "ALL", "ALL", PRD[freq], newest=1)
            except FetchError as e:
                print(f"  실패: {e.reason[:200]}")
                continue
            lines = table_lines(rows)
            print("\n".join(lines[:MAX_LINES]))
            if len(lines) > MAX_LINES:
                print(f"  … {len(lines) - MAX_LINES}줄 더(생략)")
    finally:
        kosis.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
