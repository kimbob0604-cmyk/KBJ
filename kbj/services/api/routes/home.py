"""`/api/home` — 첫 화면 카드 묶음(docs/metrics.md §32, ADR 0043). 리더 본문은 `readers/home.py`.

캐시 키는 묶음이 읽는 영역(`HOME_DOMAINS`) 버전을 이어 붙인 것 — 한 영역 작업이 새 값을 쓰면 묶음도
바로 낡는다. 오류 카드가 있는 본문은 캐시에 두지 않는다(다음 요청이 다시 만든다). 장중 30초·그 밖
300초.
"""

from __future__ import annotations

from fastapi import Request, Response

from kbj.services.api.deps import cached_json, get_state, live_ttl
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.home import Home
from kbj.services.api.readers.home import HOME_DOMAINS, home, home_has_error
from kbj.services.api.routes._common import data_router

router = data_router("home")


@router.get("", response_model=Envelope[Home])
def home_route(request: Request) -> Response:
    """첫 화면 카드 14장 — 카드마다 상태(ok·no_data·error)·원천·기준 시각·품질·메모·페이지 번호."""
    st = get_state(request)
    return cached_json(
        request,
        domain=HOME_DOMAINS,
        ttl_s=live_ttl(st),
        build=lambda: home(st.read),
        cacheable=lambda m: not home_has_error(m),
    )
