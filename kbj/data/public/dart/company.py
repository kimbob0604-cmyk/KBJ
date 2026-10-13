"""DART 기업개황(`company.json`) — 결산월·표준산업분류·법인구분·설립일(공개 등급).

승격 원본: P2 `disclosures.company`(induty_code 만 — ET `board/ingest/dart.py:company`:105), ET
`run.py:_dart_ksic`:837. docs/p4_design.md §1.3·§3.3, `pub_filings.corp_profile`(0010).

- 대표자 이름·주소·전화 등 개인·연락 칸은 받아도 **버린다**(행에 넣지 않는다 — 0010 머리말).
- `acc_mt` 는 문자열 `'12'`·`'03'` → 정수 [확인 필요 #34: 형식]. 1~12 밖이면 None + quality
  estimated(지어내지 않는다). `induty_code` 가 숫자가 아니면 None + estimated.
- 013(없는 회사)은 None — 조회 실패(예외)와 다르다.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Final

from kbj.core.fin_rows import CORP_CLS, CorpProfile, is_stock_code
from kbj.core.quality import Quality
from kbj.data.public.dart.disclosures import DartJson
from kbj.data.public.dart.doctext import ParseError, parse_date

__all__ = ["fetch_profile", "parse_profile"]

_INDUTY: Final = re.compile(r"[0-9]{1,6}")


def parse_profile(js: Mapping[str, Any], corp_code: str) -> CorpProfile | None:
    """company.json 본문 → 기업개황(013 이면 None)."""
    if str(js.get("status", "")) == "013":
        return None
    got = str(js.get("corp_code") or corp_code).strip()
    if got != corp_code:
        raise ParseError(f"요청한 회사({corp_code})와 응답 회사({got})가 다르다")
    stock = str(js.get("stock_code") or "").strip() or None
    estimated = False
    if stock is not None and not is_stock_code(stock):
        stock, estimated = None, True
    acc_raw = str(js.get("acc_mt") or "").strip()
    acc = int(acc_raw) if acc_raw.isdigit() else None
    if acc is not None and not 1 <= acc <= 12:
        acc, estimated = None, True
    if acc is None and acc_raw:
        estimated = True
    induty = str(js.get("induty_code") or "").strip() or None
    if induty is not None and not _INDUTY.fullmatch(induty):
        induty, estimated = None, True
    cls = str(js.get("corp_cls") or "").strip() or None
    if cls is not None and cls not in CORP_CLS:
        cls, estimated = None, True
    name = " ".join(str(js.get("corp_name") or "").split()) or None
    return CorpProfile(
        corp_code=corp_code,
        stock_code=stock,
        acc_mt=acc,
        induty_code=induty,
        corp_cls=cls,
        est_dt=parse_date(js.get("est_dt")),
        corp_name=name,
        quality=Quality.ESTIMATED if estimated else Quality.OK,
    )


def fetch_profile(client: DartJson, corp_code: str) -> CorpProfile | None:
    """회사 하나의 기업개황. DART 오류(키·한도·전송)는 그대로 올린다."""
    js = client.get_json("company.json", {"corp_code": corp_code}, use_cache=False)
    return parse_profile(js, corp_code)
