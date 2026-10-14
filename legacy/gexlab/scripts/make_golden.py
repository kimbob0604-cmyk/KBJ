"""골든 테스트 입력·기대값 만들기 — KBJ P7: 전부 KBJ 로 옮겼다(이 스크립트는 다시 내보내기·넘기기만).

- `core`·`chain-subset`(FND-C): `uv run python -m kbj.services.engine.gex_golden core|chain-subset …`
  (골든 tests/golden/gex/core/, 입력 tests/fixtures/synthetic/gex/validation/ — KBJ 레포 루트 기준)
- `raw`·`raw-parse`(ING): `uv run python -m kbj.services.recorder.golden raw|raw-parse …`
  (녹화 골든 tests/golden/gex/raw/, 비교 tests/golden/gex/test_raw_golden.py, DB 는 KBJ
  `prv_gex.raw_messages` — `KBJ_DATABASE_URL`)

옛 호출 `uv run python -m scripts.make_golden <모드> …` 은 모드에 따라 위 KBJ CLI 로 넘긴다. 이름도
KBJ 모듈에서 다시 내보낸다(두 벌 금지 — CLAUDE.md §3). 옮긴 시험은 MIGRATION.md 'P7'.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

from kbj.services.engine import gex_golden
from kbj.services.engine.gex_golden import (  # noqa: F401 — 다시 내보내기
    BOARD_FIELDS,
    CORE_FIXTURES,
    CORE_GOLDEN,
    CORE_INPUTS,
    FILL_FIELDS,
    NIGHT_DERIVED,
    REGEN_CORE,
    SUBSET,
    TOLERANCES,
    AroundAtm,
    CoreInput,
    Derived,
    Fixed,
    GoldenSchemaError,
    InputError,
    chain_subset,
    check_schema,
    compare_golden,
    core_golden,
    dump_chain,
    failure_report,
    shift_snapshot,
    tolerance,
)
from kbj.services.recorder import golden as raw_golden
from kbj.services.recorder.golden import (  # noqa: F401 — 다시 내보내기
    NOTICE_TR,
    RAW_DIR,
    RAW_MAX_ROWS,
    RAW_ONLY_TRS,
    REDACTED,
    REST_PARSERS,
    SECRET_PATTERNS,
    WS_SECRET_OUTPUT,
    DbRawSource,
    RawRow,
    RawWindow,
    SecretFound,
    cut_raw,
    is_notice_tr,
    parse_message,
    raw_regenerate,
    raw_snapshot,
    read_jsonl,
    recording_text,
    sanitize,
    secret_hits,
    unredacted_key_iv,
)


def main(argv: Sequence[str] | None = None) -> int:
    """모드에 따라 KBJ CLI 로 넘긴다(core·chain-subset → gex_golden, 나머지 → recorder.golden)."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] in (["core"], ["chain-subset"]):
        return gex_golden.main(args)
    return raw_golden.main(args)


if __name__ == "__main__":
    raise SystemExit(main())
