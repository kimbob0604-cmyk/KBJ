"""`/api/themes*` — 페이지 9 테마·밸류체인(ADR 0039, docs/metrics.md §31). 로그인 VM, DB 만
읽는다(계약 ⑩).

- `GET /api/themes` — 대테마 분류(섹션·밸류체인별). 공개 등급 칸만 — 공개판은 같은 모양의 공개 파일
  `themes.json`(public.export)을 읽는다(이 API 를 부르지 않는다).
- `GET /api/themes/strength?period=1|5|20` — 대테마·밸류체인 강도 순위(로그인 — 시세 기반).
- `GET /api/themes/{id}` — 대테마 하나의 네이버 테마·편입 종목·사유·종목별 등락(로그인).

캐시 영역 `themes`(cache.py — 테마 두 작업 + 보드). 쿼리·경로 검증 실패는 422(입력값을 되돌리지
않는다).
"""

from __future__ import annotations

from typing import Annotated, Final

from fastapi import Path, Request, Response

from kbj.services.api.deps import TTL_DEFAULT_S, cached_json, get_state
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.theme import ThemeCatalog, ThemeDetail, ThemeStrength
from kbj.services.api.readers.theme import theme_catalog, theme_detail, theme_strength
from kbj.services.api.routes._common import PeriodQ, data_router

router = data_router("themes")
DOMAIN: Final = "themes"
THEME_ID_PATTERN: Final = r"^[a-z0-9][a-z0-9_]{0,63}$"


@router.get("", response_model=Envelope[ThemeCatalog])
def catalog(request: Request) -> Response:
    st = get_state(request)
    return cached_json(
        request, domain=DOMAIN, ttl_s=TTL_DEFAULT_S, build=lambda: theme_catalog(st.read)
    )


@router.get("/strength", response_model=Envelope[ThemeStrength])
def strength(request: Request, period: PeriodQ = 1) -> Response:
    st = get_state(request)
    return cached_json(
        request,
        domain=DOMAIN,
        ttl_s=TTL_DEFAULT_S,
        build=lambda: theme_strength(st.read, period=period),
    )


@router.get("/{theme_id}", response_model=Envelope[ThemeDetail])
def detail(request: Request, theme_id: Annotated[str, Path(pattern=THEME_ID_PATTERN)]) -> Response:
    st = get_state(request)
    return cached_json(
        request,
        domain=DOMAIN,
        ttl_s=TTL_DEFAULT_S,
        build=lambda: theme_detail(st.read, theme_id=theme_id),
    )
