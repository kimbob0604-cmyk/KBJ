"""실전 주문 코드 0(절대 규칙 6 — docs/p8_design.md D-P8-22 ③, P8 WRAP).

KBJ 는 시세·잔고를 읽기만 한다. 규칙 알림·백테스트·저널(P8)이 생겨도 주문으로 이어지는 코드는 없다:

1. `kbj/`·`web/src/`·`config/`·`scripts/` 안에 KIS 주문 TR id 와 주문 경로가 없다. KIS TR id 의
   마지막 글자 `U` 는 쓰기(주문·정정·취소) 거래다 — 국내 현금·신용 주문(`TTTC08xxU`·모의
   `VTTC08xxU`, 새 번호 `TTTC00xxU`), 해외 주문(`TTTT1xxxU`·`JTTT1xxxU`)을 한 꼴로 잡는다.
   목록은 KIS 문서 기준 [확인 필요] — 새 주문 TR 이 같은 꼴이 아니면 이 시험에 더한다.
   주문 경로는 `/trading/order*`.
2. `settings.live_trading` 은 기본 false 이고, 설정 파일 밖 어떤 코드도 그 값으로 갈라지지
   않는다(주문 코드가 없으니 읽을 곳도 없다). `.env.example` 도 비우거나 false.

이 파일 자신은 TR id 를 조각으로 잇는다 — 레포 전체 grep 이 시험 파일에 걸리지 않게.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from kbj.config.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
SCAN = ("kbj", "web/src", "config", "scripts")
SUFFIXES = {".py", ".ts", ".mjs", ".js", ".yaml", ".yml", ".json", ".sh", ".toml"}

ORDER_TR = re.compile(r"\b[JTV]TT[CT]\d{4}" + "U" + r"\b")
ORDER_PATH = re.compile("/trading/" + r"order[-\w]*")


def _files() -> list[Path]:
    out: list[Path] = []
    for top in SCAN:
        for p in (ROOT / top).rglob("*"):
            if p.is_file() and p.suffix in SUFFIXES and "node_modules" not in p.parts:
                out.append(p)
    return out


def test_patterns_catch_known_order_ids() -> None:
    orders = ("TTTC" + "0802U", "VTTC" + "0801U", "TTTC" + "0012U", "TTTT" + "1002U")
    for tr in (*orders, "JTTT" + "1006U"):
        assert ORDER_TR.search(f'tr_id="{tr}"'), tr
    # 읽기 TR(마지막 글자 R)·현재가 TR 은 걸리지 않는다
    for tr in ("TTTC" + "8434R", "FHKST" + "01010100", "VTTC" + "8434R"):
        assert not ORDER_TR.search(tr), tr
    assert ORDER_PATH.search("/uapi/domestic-stock/v1" + "/trading/" + "order-cash")


def test_no_order_tr_or_order_path_in_kbj() -> None:
    hits: list[str] = []
    for p in _files():
        text = p.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if ORDER_TR.search(line) or ORDER_PATH.search(line):
                hits.append(f"{p.relative_to(ROOT)}:{i}")
    assert hits == [], hits


def test_live_trading_is_off_by_default_and_never_branched_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("KBJ_LIVE_TRADING", raising=False)
    assert Settings(_env_file=None).live_trading is False  # pyright: ignore[reportCallIssue]
    readers = [
        str(p.relative_to(ROOT))
        for p in (ROOT / "kbj").rglob("*.py")
        if "live_trading" in p.read_text(encoding="utf-8")
    ]
    assert readers == ["kbj/config/settings.py"]
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    m = re.search(r"^KBJ_LIVE_TRADING=(.*)$", env_example, re.M)
    assert m is not None and m.group(1).strip().lower() in {"", "false"}
