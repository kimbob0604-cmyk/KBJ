"""raw 녹화 골든을 실제 raw_messages 에서 자른다 (scripts/make_golden.py `raw --from-db`).

합성 내보내기(tests/golden/synthetic.py — 실제 녹화 아님)를 recorder 와 같은
`PostgresSink.write_raw` 로 넣고, DB 에서 자른 녹화가 JSONL 에서 자른 것(커밋된
tests/golden/raw/synthetic_20260928.*)과 바이트까지 같은지 본다 — jsonb 왕복·digest·UTC 시각
뒤에도. 거르기·상한도 SQL 로 (PLAN §6.3 골든).

컨테이너는 tests/integration/conftest.py 가 띄우고 지운다. Docker 가 없으면 건너뛴다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from data.store import PostgresSink
from db.migrate import migrate
from scripts.make_golden import (
    RAW_DIR,
    DbRawSource,
    InputError,
    RawRow,
    RawWindow,
    cut_raw,
    main,
)
from services.recorder.envelope import RawEnvelope
from tests.golden.synthetic import NAME, WINDOW_END, WINDOW_START, synthetic_export
from tests.integration.conftest import PgContainer

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def dsn(timescale: PgContainer) -> str:
    url = timescale.dsn(timescale.fresh_database("golden"))
    migrate(url)
    rows = [RawRow.model_validate(d) for d in synthetic_export()]
    envelopes = [
        RawEnvelope(
            received_at=r.ts,
            source=r.source,
            tr_id=r.tr_id,
            key=r.key,
            payload=r.payload if r.payload is not None else r.payload_text or "",
            trade_date=r.trade_date,
            session=r.session,
        )
        for r in rows
    ]
    with PostgresSink(url, service="recorder") as pg:
        pg.write_raw(envelopes)
        pg.write_raw(envelopes)  # 다시 써도 digest 키라 한 행
    return url


def test_db_cut_equals_the_committed_recording(
    dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", dsn)
    out = tmp_path / "golden"
    args = ["raw", "--from-db", "--start", WINDOW_START.isoformat(), "--end"]
    args += [WINDOW_END.isoformat(), "--name", NAME, "--out-dir", str(out), "--write"]
    assert main(args) == 0
    rec = f"{NAME}.jsonl"
    assert (out / rec).read_bytes() == (RAW_DIR / rec).read_bytes()
    got = json.loads((out / f"{NAME}.parsed.json").read_text(encoding="utf-8"))
    want = json.loads((RAW_DIR / f"{NAME}.parsed.json").read_text(encoding="utf-8"))
    assert (got["cut"].pop("from"), want["cut"].pop("from")) == ("db", "jsonl")
    assert got == want


def test_db_filters_and_row_cap_run_in_sql(dsn: str) -> None:
    source = DbRawSource.from_url(dsn)
    night = source.read(RawWindow(WINDOW_START, WINDOW_END, ("kis_ws",), ("H0MFCNT0",)))
    assert [(r.tr_id, r.session) for r in night] == [("H0MFCNT0", "night")] * 2
    capped = source.read(RawWindow(WINDOW_START, WINDOW_END, max_rows=3))
    assert len(capped) == 4  # 넘쳤는지 알 만큼만(상한 + 1)
    with pytest.raises(InputError, match="3개를 넘는다"):
        cut_raw(capped, RawWindow(WINDOW_START, WINDOW_END, max_rows=3))


def test_db_errors_do_not_echo_the_connection_string(dsn: str) -> None:
    wrong = dsn.rsplit("/", 1)[0] + "/no_such_db"
    with pytest.raises(InputError) as e:
        DbRawSource.from_url(wrong).read(RawWindow(WINDOW_START, WINDOW_END))
    assert str(e.value) == "raw_messages 를 읽지 못했다: OperationalError"
