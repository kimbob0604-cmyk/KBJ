"""운용사 어댑터 목록 — 9곳(docs/p3_design.md §1.5·D-P3-12).

ET `etf_tracker_v9/collectors.py` 의 내장 4곳(`Kodex`·`Tiger`·`TimeFolio`·`Sol`)과 `adapters/` 5곳
(`Ace`·`Hanaro`·`KoAct`·`Plus`·`Rise`). **네이버 TOP10 폴백(`NaverTop10`)은 옮기지 않는다**(ADR 0001
U4 — 네이버 스크래핑 금지). 전용 어댑터가 없는 운용사(키움·하나·우리 등)의 구성종목은 P3 에서 비고,
대체 후보는 KIS `FHKST121600C0` [실측 필요].

ET 의 `adapters/` 폴더 자동 등록(`_load_plugins` — import 실패를 출력만 하고 넘어감)은 두지 않는다.
목록은 이 파일 하나다(빠지면 시험이 잡는다).
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from datetime import date
from typing import Final

from kbj.data.private.etf_issuers.ace import Ace
from kbj.data.private.etf_issuers.base import IssuerAdapter, IssuerHttp, today_kst
from kbj.data.private.etf_issuers.hanaro import Hanaro
from kbj.data.private.etf_issuers.koact import KoAct
from kbj.data.private.etf_issuers.kodex import Kodex
from kbj.data.private.etf_issuers.plus import Plus
from kbj.data.private.etf_issuers.rise import Rise
from kbj.data.private.etf_issuers.sol import Sol
from kbj.data.private.etf_issuers.tiger import Tiger
from kbj.data.private.etf_issuers.timefolio import TimeFolio

__all__ = ["ADAPTERS", "KEYS", "build"]

ADAPTERS: Final = (Kodex, Tiger, TimeFolio, Sol, Ace, Hanaro, KoAct, Plus, Rise)
KEYS: Final[tuple[str, ...]] = tuple(a.KEY for a in ADAPTERS)


def build(
    http: IssuerHttp,
    keys: Collection[str] | None = None,
    *,
    today: Callable[[], date] = today_kst,
) -> list[IssuerAdapter]:
    """어댑터 인스턴스(목록 순서). keys 를 주면 그것만 — 모르는 키는 ValueError."""
    if keys is not None:
        unknown = sorted(set(keys) - set(KEYS))
        if unknown:
            raise ValueError(f"모르는 운용사 키: {unknown}")
    out: list[IssuerAdapter] = []
    for cls in ADAPTERS:
        if keys is not None and cls.KEY not in keys:
            continue
        out.append(Rise(http, today=today) if cls is Rise else cls(http))
    return out
