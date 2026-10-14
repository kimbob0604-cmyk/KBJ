"""poller 가 부르는 KIS REST 요청 (경로·TR·파라미터) — GX `services/poller/endpoints.py` 이식(P9
GXI).

경로·TR·파라미터는 GX probe 로 실측한 것을 그대로 쓴다(GX `scripts/probe_common.py` 상수,
`probe_callput.callput_params`, `probe_investor.PAIRS`, `probe_option_list`·`probe_night_board` 의
파라미터 — probe 스크립트는 옮기지 않아 값을 여기에 둔다. TR 번호 정본은
`kbj.data.private.kis.derivatives`).
투자자별 요청(`investor`)은 지웠다(D-P7-5 — 쓰기 주인은 flows.market_minute). 기초자산
시세(display-board-top)만 probe 로 부른 적이 없다 — 파라미터는 KIS 공식 샘플
형식이고 응답 필드는 미실측이라 raw 로만 녹화한다(확인 필요).
"""

from __future__ import annotations

from dataclasses import dataclass

from kbj.data.private.kis.derivatives import (
    TR_CALLPUT,
    TR_FUT_BOARD,
    TR_OPTION_LIST,
    TR_PRICE,
    TR_TOP,
)

# KIS 경로(GX scripts/probe_common.py 상수 그대로 — KIS 공식 open-trading-api 샘플 기준). 호스트는
# 어댑터(kbj.data.private.kis.rest)만 안다 — 여기는 경로뿐
P_CALLPUT = "/uapi/domestic-futureoption/v1/quotations/display-board-callput"
P_OPTION_LIST = "/uapi/domestic-futureoption/v1/quotations/display-board-option-list"
P_FUT_BOARD = "/uapi/domestic-futureoption/v1/quotations/display-board-futures"
P_TOP = "/uapi/domestic-futureoption/v1/quotations/display-board-top"
P_PRICE = "/uapi/domestic-futureoption/v1/quotations/inquire-price"

# 장중 투자자별 시장·업종 조합(GX scripts/probe_investor.py PAIRS, #13). poller 는 부르지 않는다 —
# 쓰기 주인은 flows.market_minute(ADR 0019). 무결측 판정(gex_gaps)이 스트림 이름으로만 쓴다
PAIRS: tuple[tuple[str, str], ...] = (
    ("K2I", "F001"),  # 선물
    ("K2I", "OC01"),  # 콜
    ("K2I", "OP01"),  # 풋
    ("WKM", "OC05"),  # 위클리(월) 콜
    ("WKM", "OP05"),
    ("WKI", "OC04"),  # 위클리(목) 콜
    ("WKI", "OP04"),
)


def callput_params(mtrt: str, cls: str) -> dict[str, str]:
    """전광판 콜/풋 파라미터(GX scripts/probe_callput.py 그대로)."""
    return {
        "FID_COND_MRKT_DIV_CODE": "O",
        "FID_COND_SCR_DIV_CODE": "20503",
        "FID_MRKT_CLS_CODE": "CO",
        "FID_MTRT_CNT": mtrt,
        "FID_MRKT_CLS_CODE1": "PO",
        "FID_COND_MRKT_CLS_CODE": cls,
    }


INVESTOR_PAIRS: tuple[tuple[str, str], ...] = PAIRS  # K2I F001/OC01/OP01, WKM OC05/OP05, WKI …
OPTION_LIST_CLASSES: tuple[str, ...] = ("", "WKM", "WKI")  # 월물·위클리(월)·위클리(목) (#12b)


@dataclass(frozen=True)
class Request:
    path: str
    tr_id: str
    params: tuple[tuple[str, str], ...]

    def as_dict(self) -> dict[str, str]:
        return dict(self.params)


def _req(path: str, tr_id: str, params: dict[str, str]) -> Request:
    return Request(path, tr_id, tuple(params.items()))


def option_list(cls: str) -> Request:
    return _req(
        P_OPTION_LIST,
        TR_OPTION_LIST,
        {
            "FID_COND_SCR_DIV_CODE": "509",
            "FID_COND_MRKT_DIV_CODE": "",
            "FID_COND_MRKT_CLS_CODE": cls,
        },
    )


def callput_board(cls: str, mtrt: str, market: str = "O") -> Request:
    """전광판 콜/풋. 만기값은 월물리스트 6자리 `mtrt_yymm`(#12)."""
    params = callput_params(mtrt, cls) | {"FID_COND_MRKT_DIV_CODE": market}
    return _req(P_CALLPUT, TR_CALLPUT, params)


def futures_board(market: str = "F") -> Request:
    return _req(
        P_FUT_BOARD,
        TR_FUT_BOARD,
        {
            "FID_COND_MRKT_DIV_CODE": market,
            "FID_COND_SCR_DIV_CODE": "20503",
            "FID_COND_MRKT_CLS_CODE": "",
        },
    )


def underlying_top(futures_code: str, market: str = "F") -> Request:
    """기초자산 시세(FHPIF05030000). KIS 공식 샘플의 파라미터 형식 — 미실측(확인 필요)."""
    return _req(
        P_TOP,
        TR_TOP,
        {
            "FID_COND_MRKT_DIV_CODE": market,
            "FID_INPUT_ISCD": futures_code,
            "FID_COND_MRKT_DIV_CODE1": "",
            "FID_COND_SCR_DIV_CODE": "",
            "FID_MTRT_CNT": "",
            "FID_COND_MRKT_CLS_CODE": "",
        },
    )


def single_price(market: str, code: str) -> Request:
    """선물옵션 단건 현재가. 옵션 O(야간 EU), 선물 F(야간 CM) — #11a·#19."""
    return _req(P_PRICE, TR_PRICE, {"FID_COND_MRKT_DIV_CODE": market, "FID_INPUT_ISCD": code})
