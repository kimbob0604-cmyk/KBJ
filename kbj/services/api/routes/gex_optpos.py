"""`/api/gex/option-positions` — 옵션 행사가별 투자자 추정 포지션(metrics §34, ADR 0044 — 로그인).

`/api/gex/*` 아래지만 캐시 영역은 `gex_optpos`(작업 `gex.option_positions`·`gex.krx_derivatives` —
GEX 엔진 사이클 시각에 묶으면 장 밖 KRX OI 적재가 버전을 못 바꾼다). 라우트 파일을 따로 둔 것은
`routes/gex.py`(GEX 엔진 영역)와 캐시 영역이 달라서다.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Final, Literal

from fastapi import Query, Request, Response

from kbj.services.api.deps import cached_json, get_state, live_ttl
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.gex_optpos import OptionPositions
from kbj.services.api.readers.gex_option_positions import option_positions
from kbj.services.api.routes._common import data_router

router = data_router("gex")
DOMAIN: Final = "gex_optpos"


@router.get("/option-positions", response_model=Envelope[OptionPositions])
def option_positions_route(
    request: Request,
    investor: Annotated[Literal["frgn", "prsn", "orgn"], Query()] = "frgn",
    day: Annotated[date | None, Query(alias="date")] = None,
) -> Response:
    st = get_state(request)
    return cached_json(
        request, domain=DOMAIN, ttl_s=live_ttl(st),
        build=lambda: option_positions(st.read, investor=investor, day=day),
    )  # fmt: skip
