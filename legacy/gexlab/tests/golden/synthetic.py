"""SYNTHETIC raw_messages 내보내기 — raw 녹화 골든 도구(scripts/make_golden.py `raw`)의 시험 입력.

**실제 녹화가 아니다.** 2026-09-29 기준 라이브 녹화가 아직 없다(Phase 1 소크 전 — KIS 앱키
공유로 대기). 웹소켓 체결 프레임은 `tests.fakes.ws_server.synth_record` 로 설정 컬럼 순서대로
합성했고, REST 본문은 합성 fixture(tests/fixtures/kis — scripts/make_synthetic_fixtures.py)에서
몇 행만 옮겼다. 제어 프레임의 복호화
키·IV, 암호화 프레임 본문, 체결통보 구독 키(HTS ID — 행 key·header.tr_key)는 가짜 값이다(자르는
쪽이 가리는지 보려고 넣었다). 행 key 는 ws-gateway `_meta` 대로 — 제어 프레임은 tr_key, 암호화
프레임은 빈 문자열.

- 줄 형식은 DB 내보내기(`SELECT row_to_json(r) FROM raw_messages r …`)와 같다 — digest 열도 있다
  (자르는 쪽은 버린다). 줄 순서는 일부러 시각 순이 아니다
- 창 `WINDOW_START`~`WINDOW_END`(2026-09-28 09:00 ~ 09-29 01:00 KST) 앞·끝·뒤에 행이 하나씩 있다 —
  시작 시각 행은 들고 끝 시각 행은 빠진다
- 커밋된 골든 tests/golden/raw/synthetic_20260928.* 은 이것을 그대로 자른 것이다. 다시 만들기:

    uv run python -m tests.golden.synthetic /tmp/raw_export.jsonl
    uv run python -m scripts.make_golden raw --from-jsonl /tmp/raw_export.jsonl \\
        --start 2026-09-28T09:00:00+09:00 --end 2026-09-29T01:00:00+09:00 \\
        --name synthetic_20260928 --write
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from data.kis.rest import MINUTE_TR
from scripts.probe_common import (
    TR_CALLPUT,
    TR_FUT_BOARD,
    TR_INVESTOR,
    TR_OPTION_LIST,
    TR_PRICE,
    TR_TOP,
)
from tests.fakes.kis_server import load_fixture
from tests.fakes.ws_server import PINGPONG_TEXT, synth_frame, synth_record

KST = ZoneInfo("Asia/Seoul")
NAME = "synthetic_20260928"
WINDOW_START = datetime(2026, 9, 28, 9, 0, tzinfo=KST)
WINDOW_END = datetime(2026, 9, 29, 1, 0, tzinfo=KST)
DAY = (date(2026, 9, 28), "day")
NIGHT = (date(2026, 9, 29), "night")  # 09-28 밤은 다음 거래일 귀속
UNTAGGED: tuple[date | None, str | None] = (None, None)
FAKE_AES_KEY = "0123456789abcdef0123456789abcdef"  # SYNTHETIC — 실제 키가 아니다
FAKE_AES_IV = "fedcba9876543210"
FAKE_CIPHER = "U2FsdGVkX1+c3ludGhldGljLWNpcGhlcg=="  # SYNTHETIC 암호문 자리
FAKE_HTS_ID = "synthhts01"  # SYNTHETIC 체결통보 구독 키(HTS ID) 자리

Tag = tuple[date | None, str | None]


def _kst(d: int, h: int, m: int, s: int = 0, us: int = 0) -> datetime:
    return datetime(2026, 9, d, h, m, s, us, tzinfo=KST)


def _row(at: datetime, tag: Tag, source: str, tr_id: str, key: str, body: object) -> dict[str, Any]:
    text = body if isinstance(body, str) else None
    payload = None if isinstance(body, str) else body
    digest = hashlib.sha256(f"{source}|{tr_id}|{key}|{json.dumps(body)}".encode()).hexdigest()
    return {
        "ts": at.astimezone(UTC).isoformat(),
        "trade_date": None if tag[0] is None else tag[0].isoformat(),
        "session": tag[1],
        "source": source,
        "tr_id": tr_id,
        "key": key,
        "payload": payload,
        "payload_text": text,
        "lossy": False,
        "digest": "\\x" + digest,
    }


def _control(tr_id: str, tr_key: str, msg1: str, output: dict[str, str] | None = None) -> str:
    body: dict[str, Any] = {"rt_cd": "0", "msg_cd": "OPSP0000", "msg1": msg1}
    if output is not None:
        body["output"] = output
    return json.dumps({"header": {"tr_id": tr_id, "tr_key": tr_key, "encrypt": "N"}, "body": body})


def _fut(tr_id: str, hhmmss: str, price: str, qty: str, cum: str) -> list[str]:
    return synth_record(
        tr_id,
        futs_shrn_iscd="A01612",
        bsop_hour=hhmmss,
        futs_prpr=price,
        last_cnqn=qty,
        acml_vol=cum,
        futs_bidp1=price,
        futs_askp1=f"{float(price) + 0.05:.2f}",
        hts_otst_stpl_qty="231456",
        shnu_cntg_smtn="40000",
        seln_cntg_smtn="38000",
    )


def _opt(tr_id: str, code: str, hhmmss: str, price: str) -> list[str]:
    return synth_record(
        tr_id,
        optn_shrn_iscd=code,
        bsop_hour=hhmmss,
        optn_prpr=price,
        last_cnqn="3",
        acml_vol="1200",
        optn_bidp1=price,
        optn_askp1=f"{float(price) + 0.01:.2f}",
        hts_otst_stpl_qty="883",
        delta="0.4120",
        gama="0.0712",
        hts_ints_vltl="11.6494",
    )


def _board_body() -> dict[str, Any]:
    """전광판(WKM 260904) 합성 fixture 앞 2행씩 + 행사가가 숫자가 아닌 행 하나(행 단위 검증)."""
    full = load_fixture("callput_wkm_260904.json")
    bad = dict(full["output1"][0]) | {"acpr": "abc"}
    return {
        "rt_cd": full["rt_cd"],
        "output1": [*full["output1"][:2], bad],
        "output2": full["output2"][:2],
    }


def synthetic_export() -> list[dict[str, Any]]:
    """내보내기 행들(줄 순서 그대로). 시각 순이 아니다 — 자르는 쪽이 정렬한다."""
    ws, rest = "kis_ws", "kis_rest"
    price = load_fixture("price_options.json")["items"][0]["body"]
    investor = load_fixture("investor.json")["pairs"]["K2I/F001"]
    option_list = load_fixture("option_list.json")["by_class"]["WKM"]
    fut_board = load_fixture("futures_board.json")
    minute = load_fixture("minute_day.json")
    top = {
        "rt_cd": "0",
        "msg_cd": "MCA00000",
        "msg1": "정상처리 되었습니다.",
        "output1": {"bstp_nmix_prpr": "1094.47"},  # SYNTHETIC 지수 수준
        "output2": [],
    }
    rows = [
        # 창 밖(앞) — 빠진다
        _row(_kst(28, 8, 59, 59), UNTAGGED, rest, TR_FUT_BOARD, "futures_board|F", fut_board),
        # 주간 — 웹소켓
        _row(WINDOW_START, DAY, ws, "PINGPONG", "", PINGPONG_TEXT),  # 시작 시각 — 든다
        _row(
            _kst(28, 9, 0, 0, 250000),
            DAY,
            ws,
            "H0IFCNT0",
            "A01612",
            _control("H0IFCNT0", "A01612", "SUBSCRIBE SUCCESS"),
        ),
        _row(
            _kst(28, 9, 0, 0, 300000),
            DAY,
            ws,
            "H0IFCNI0",
            FAKE_HTS_ID,
            _control(
                "H0IFCNI0",
                FAKE_HTS_ID,
                "SUBSCRIBE SUCCESS",
                {"iv": FAKE_AES_IV, "key": FAKE_AES_KEY},
            ),
        ),
        _row(
            _kst(28, 9, 0, 1, 100000),
            DAY,
            ws,
            "H0IFCNT0",
            "A01612",
            synth_frame(
                "H0IFCNT0",
                _fut("H0IFCNT0", "090001", "1092.50", "2", "1502"),
                _fut("H0IFCNT0", "090001", "1092.55", "1", "1503"),
            ),
        ),
        _row(
            _kst(28, 9, 0, 1, 200000),
            DAY,
            ws,
            "H0IOCNT0",
            "B01610AA2",
            synth_frame("H0IOCNT0", _opt("H0IOCNT0", "B01610AA2", "090001", "2.90")),
        ),
        _row(_kst(28, 9, 0, 2), DAY, ws, "H0IFCNI0", "", f"1|H0IFCNI0|001|{FAKE_CIPHER}"),
        _row(_kst(28, 9, 0, 3), DAY, ws, "unknown", "", "garbage-frame"),
        _row(_kst(28, 9, 0, 4), DAY, ws, "H0ZZZZZ0", "", "0|H0ZZZZZ0|001|a^b^c"),
        _row(
            _kst(28, 9, 0, 5),
            DAY,
            ws,
            "H0IFCNT0",
            "A01612",
            synth_frame("H0IFCNT0", _fut("H0IFCNT0", "090005", "1092.60", "1", "1504")[:-3]),
        ),
        # 주간 — REST (합성 fixture 본문)
        _row(_kst(28, 9, 0, 30), DAY, rest, TR_CALLPUT, "board:WKM:260904|x", _board_body()),
        _row(_kst(28, 9, 0, 31), DAY, rest, TR_PRICE, "fill1:M:202610:1092.50:C|x", price),
        _row(_kst(28, 9, 0, 32), DAY, rest, TR_FUT_BOARD, "futures_board|F", fut_board),
        _row(_kst(28, 9, 0, 33), DAY, rest, TR_INVESTOR, "investor:K2I/F001|x", investor),
        _row(_kst(28, 9, 0, 34), DAY, rest, TR_OPTION_LIST, "option_list:WKM|x", option_list),
        _row(_kst(28, 9, 0, 35), DAY, rest, TR_TOP, "underlying|A01612", top),
        _row(_kst(28, 9, 0, 36), DAY, rest, "FHZZZ0000000", "unknown|x", {"rt_cd": "0"}),
        _row(_kst(28, 16, 0, 5), DAY, rest, MINUTE_TR, "minute:F:A01612|x", minute),
        # 야간(09-29 귀속) — 체결 시각 00~06시·24~30시 표기 둘 다(설계 §2 ws-gateway, 미실측)
        _row(
            _kst(28, 18, 0, 1),
            NIGHT,
            ws,
            "H0MFCNT0",
            "A01612",
            synth_frame("H0MFCNT0", _fut("H0MFCNT0", "180001", "1093.00", "1", "12")),
        ),
        _row(
            _kst(28, 18, 0, 2),
            NIGHT,
            ws,
            "H0EUCNT0",
            "B01610AA2",
            synth_frame("H0EUCNT0", _opt("H0EUCNT0", "B01610AA2", "180002", "2.50")),
        ),
        _row(
            _kst(29, 0, 30),
            NIGHT,
            ws,
            "H0MFCNT0",
            "A01612",
            synth_frame(
                "H0MFCNT0",
                _fut("H0MFCNT0", "003000", "1091.00", "1", "900"),
                _fut("H0MFCNT0", "243000", "1091.05", "1", "901"),
            ),
        ),
        _row(_kst(29, 0, 30, 1), NIGHT, "krx", "drv_opt_bydd_trd", "20260925", {"OutBlock_1": []}),
        # 창 끝 시각·뒤 — 빠진다
        _row(WINDOW_END, NIGHT, ws, "PINGPONG", "", PINGPONG_TEXT),
        _row(_kst(29, 1, 0, 5), NIGHT, ws, "PINGPONG", "", PINGPONG_TEXT),
    ]
    return sorted(rows, key=lambda r: (r["tr_id"], r["ts"]))  # 시각 순이 아닌 고정 순서


def export_text() -> str:
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in synthetic_export())


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m tests.golden.synthetic <out.jsonl>")
    Path(sys.argv[1]).write_text(export_text(), encoding="utf-8")
