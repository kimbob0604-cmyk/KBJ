"""ETF 테마 분류 — KBJ 정본 `kbj.engines.etf.types` 를 다시 내보내는 shim(P3 묶음 E3, D-P3-13).

규칙(EXCLUDE·RULES·BRANDS·ISSUER_OF_BRAND)과 함수(strip_brand·brand_of·issuer_of·is_excluded·
is_active·classify·tag)는 그대로 kbj 로 옮겼다(두 벌 금지 — CLAUDE.md §3). 이 파일의 이름은 legacy
tracker·verify·report 가 `import themes as TH` 로 그대로 쓴다. 바뀐 것은 ISSUER_OF_BRAND['UNICORN']
의 한자 오타 한 글자뿐(docs/adr/0010).
"""
from kbj.engines.etf.types import (  # noqa: F401
    BRANDS,
    EXCLUDE,
    ISSUER_OF_BRAND,
    RULES,
    brand_of,
    classify,
    is_active,
    is_excluded,
    issuer_of,
    strip_brand,
    tag,
)
