"""KOSIS 논리 데이터셋(공개 — 국내 통계) — 작업 등록부·선점·카탈로그용(설계 §2·§6.7).

저장 표 `pub_macro.series`(마이그레이션 0011, P5). 수집 작업 `macro.monthly`(09:10 @ month, P5).
표 이름은 공개 웹 페이지에서 확인했고 항목 ID·계절조정 표는 [실측 필요](probe_results §5.4).
orgId 는 모두 101(통계청). 국제통계 표는 로그인 [제안] — 여기 두지 않는다.
"""

from __future__ import annotations

from typing import Final

from kbj.data.spec import DatasetSpec, Tier

SOURCE: Final = "KOSIS"
LIMITER: Final = "kosis"
ORG_ID: Final = "101"
_PUBLISHED: Final = "월간 — 공표일은 통계마다 다르다(산업활동동향 월말, 고용동향 중순) [실측 필요]"


def _kosis(tbl_id: str, *, notes: str) -> DatasetSpec:
    return DatasetSpec(
        id=f"{SOURCE}:{tbl_id}",
        source=SOURCE,
        dataset=tbl_id,
        tier=Tier.PUBLIC,
        limiter=LIMITER,
        budget=None,  # 일 한도 미공표 [실측 필요]
        published=_PUBLISHED,
        as_of_kind="month",
        store="pub_macro.series",
        notes=f"orgId {ORG_ID}. {notes}",
    )


DATASETS: Final[tuple[DatasetSpec, ...]] = (
    _kosis(
        "DT_1C8015",
        notes="경기종합지수(2020=100)(10차) — 선행·동행 순환변동치, 항목 ID [실측 필요]",
    ),
    _kosis("DT_1C8016", notes="경기종합지수 구성지표 시계열(10차)"),
    _kosis("DT_1JH20201", notes="전산업생산지수(원지수) — 계절조정 표·항목 [실측 필요]"),
    _kosis(
        "DT_1DA7001S", notes="성별 경제활동인구 총괄 — 고용률·실업률, 계절조정 여부 [실측 필요]"
    ),
    _kosis("DT_1DA7002S", notes="연령별 경제활동인구 총괄"),
    _kosis(
        "DT_1J22003",
        notes="소비자물가지수(2020=100) — ECOS 901Y009(공개 판정 전) 대신 공개 경로",
    ),
)
