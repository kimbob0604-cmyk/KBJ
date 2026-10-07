"""공공데이터포털(관세청·금투협 종합통계) 합성 응답 생성기 — 묶음 C 시험용(고정 시드).

관세청·금투협 종합통계는 **공개 등급**이라 실데이터 fixture 를 넣어도 되지만(DATA_TIERS §4),
아직 키가 없어 실호출을 못 했다. 그래서 응답 **모양**(봉투·열 이름·값 문자열 형식)은 공식 문서
(포털 swagger·기술문서 — `docs/probe_results.md` §1~§3)대로, 값은 이 생성기가 고정 시드로 만든
가상 값이다.
키를 받은 뒤 공개 실데이터 fixture(`tests/fixtures/public/datago/`)로 바꾼다 [확인 필요 — R21].

- 시드 `kbj-p2-c-datago-synthetic-v1`, 파일마다 `random.Random(f"{SEED}:{이름}")` — 한 파일을 고쳐도
  다른 파일 값이 바뀌지 않는다.
- XML 은 첫 줄 뒤 주석, JSON 은 머리 칸 `_source` 가 `SYNTHETIC` 으로 시작한다.
- 문자열 형식의 함정을 일부러 담는다: 금액의 쉼표·앞 공백(10일 잠정치), 숫자형으로 앞자리 0 이 빠진
  HS 코드(15101609 `hsCode`), 시군구 금액 문자열.

    uv run python tests/fixtures/synthetic/datago/make_fixtures.py          # 다시 쓰기
    uv run python tests/fixtures/synthetic/datago/make_fixtures.py --check  # 파일과 같은지
"""

from __future__ import annotations

import json
import random
import sys
from collections.abc import Callable
from pathlib import Path

SEED = "kbj-p2-c-datago-synthetic-v1"
HERE = Path(__file__).resolve().parent
MARK = "SYNTHETIC — 공식 문서 모양의 가상 값(tests/fixtures/synthetic/datago/make_fixtures.py)"

# 관세청 10일 잠정치 열 이름은 kbj.data.public.customs.models.TEN_DAY_COLUMNS — 여기는 열 번호만
_TEN_DAY_COLS = 11


def _rng(name: str) -> random.Random:
    return random.Random(f"{SEED}:{name}")  # noqa: S311 — 합성 fixture 값(보안 용도 아님)


def _xml(items: list[dict[str, str]], total: int | None = None) -> str:
    def item(d: dict[str, str]) -> str:
        cells = "".join(f"<{k}>{v}</{k}>" for k, v in d.items())
        return f"      <item>{cells}</item>\n"

    total_line = f"    <totalCount>{len(items) if total is None else total}</totalCount>\n"
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f"<!-- {MARK} -->\n"
        "<response>\n"
        "  <header><resultCode>00</resultCode><resultMsg>정상 서비스.</resultMsg></header>\n"
        "  <body>\n"
        "    <items>\n"
        f"{''.join(item(d) for d in items)}"
        "    </items>\n"
        f"{total_line}"
        "  </body>\n"
        "</response>\n"
    )


def _money(n: int) -> str:
    return f"{n}"


# ── 관세청 월간 ─────────────────────────────────────────────────────────────────────────────


def customs_item_country() -> str:
    """15100475 — 미국(US) × HS 3개 × 2개월. `year` 는 `YYYY.MM`."""
    r = _rng("customs_item_country")
    items: list[dict[str, str]] = []
    for ym in ("2026.07", "2026.08"):
        for hs, name in (("8542", "전자집적회로"), ("8703", "승용자동차"), ("0303", "냉동 어류")):
            ew, ed = r.randrange(1_000, 90_000), r.randrange(10_000, 9_000_000)
            iw, idl = r.randrange(100, 9_000), r.randrange(1_000, 900_000)
            items.append(
                {
                    "balPayments": _money(ed - idl),
                    "expDlr": _money(ed),
                    "expWgt": _money(ew),
                    "hsCd": hs,
                    "impDlr": _money(idl),
                    "impWgt": _money(iw),
                    "statCd": "US",
                    "statCdCntnKor1": "미국",
                    "statKor": name,
                    "year": ym,
                }
            )
    return _xml(items)


def customs_items() -> str:
    """15101609 — `hsCode` 가 숫자형이라 앞자리 0 이 빠진 행(`106191000`·`303`)을 담는다."""
    r = _rng("customs_items")
    items: list[dict[str, str]] = []
    for hs, name in (
        ("106191000", "기타 포유동물"),
        ("8542", "전자집적회로"),
        ("303", "냉동 어류"),
    ):
        ed, idl = r.randrange(10_000, 9_000_000), r.randrange(1_000, 900_000)
        items.append(
            {
                "balPayments": _money(ed - idl),
                "expDlr": _money(ed),
                "expWgt": _money(r.randrange(1_000, 90_000)),
                "hsCode": hs,
                "impDlr": _money(idl),
                "impWgt": _money(r.randrange(100, 9_000)),
                "statKor": name,
                "year": "2026.08",
            }
        )
    return _xml(items)


def customs_countries() -> str:
    """15101612 — 중량 없음·건수 있음."""
    r = _rng("customs_countries")
    items: list[dict[str, str]] = []
    for cd, name in (("CN", "중국"), ("US", "미국"), ("VN", "베트남")):
        ed, idl = r.randrange(1_000_000, 90_000_000), r.randrange(1_000_000, 90_000_000)
        items.append(
            {
                "balPayments": _money(ed - idl),
                "expCnt": _money(r.randrange(1_000, 90_000)),
                "expDlr": _money(ed),
                "impCnt": _money(r.randrange(1_000, 90_000)),
                "impDlr": _money(idl),
                "statCd": cd,
                "statCdCntnKor1": name,
                "year": "2026.08",
            }
        )
    return _xml(items)


def customs_sigungu() -> str:
    """15134343 — 시도 41(경기) × HS6 330499. 값은 모두 문자열(쉼표 포함)."""
    r = _rng("customs_sigungu")
    items: list[dict[str, str]] = []
    for ym in ("2026.07", "2026.08"):
        for sgg in ("가상시", "합성군"):
            ed, idl = r.randrange(10_000, 9_000_000), r.randrange(1_000, 900_000)
            items.append(
                {
                    "cmtrBlncAmt": f"{ed - idl:,}",
                    "expCnt": f"{r.randrange(10, 900):,}",
                    "expUsdAmt": f"{ed:,}",
                    "hsSgn": "330499",
                    "impCnt": f"{r.randrange(1, 90):,}",
                    "impUsdAmt": f"{idl:,}",
                    "korePrlstNm": "기타 미용·화장품",
                    "priodTitle": ym,
                    "sggNm": sgg,
                }
            )
    return _xml(items)


def customs_ten_day() -> str:
    """15157908(수출 주요품목별) — 2개월 × 3행(~10일·~20일·말일 누계). 금액은 천 달러,
    쉼표와 앞 공백이 붙은 문자열(`" 13,886,115"`)."""
    r = _rng("customs_ten_day")
    items: list[dict[str, str]] = []
    for ym, last in (("202608", 31), ("202609", 30)):
        parts = [r.randrange(100_000, 900_000) for _ in range(_TEN_DAY_COLS - 1)]
        for seg, end in ((1, 10), (2, 20), (3, last)):
            scaled = [p * seg for p in parts]
            total = sum(scaled) + r.randrange(1_000_000, 3_000_000) * seg  # 00 = 전체(기타 포함)
            row = {"priodDt": f"01~{end:02d}", "priodMon": ym, "priodYear": ym[:4]}
            row["itemUsdAmt00"] = f" {total:,}"
            for i, v in enumerate(scaled, start=1):
                row[f"itemUsdAmt{i:02d}"] = f" {v:,}"
            items.append(row)
    return _xml(items)


# ── 금투협 종합통계(15094809) JSON ────────────────────────────────────────────────────────────

_DAYS = ("20260930", "20261001", "20261002")  # 가상 기준일 세 개


def _envelope(items: list[dict[str, str]]) -> str:
    js = {
        "_source": MARK,
        "response": {
            "header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE."},
            "body": {
                "numOfRows": 10000,
                "pageNo": 1,
                "totalCount": len(items),
                "items": {"item": items},
            },
        },
    }
    return json.dumps(js, ensure_ascii=False, indent=2) + "\n"


def kofia_credit() -> str:
    r = _rng("kofia_credit")
    keys = (
        "crdTrFingWhl",
        "crdTrFingScrs",
        "crdTrFingKosdaq",
        "crdTrLndrWhl",
        "crdTrLndrScrs",
        "crdTrLndrKosdaq",
        "sbscCapLn",
        "dpsgScrtMogFing",
    )
    items = []
    for d in _DAYS:
        row = {"basDt": d}
        row.update({k: str(r.randrange(10**9, 3 * 10**13)) for k in keys})
        items.append(row)
    return _envelope(items)


def kofia_capital() -> str:
    r = _rng("kofia_capital")
    items = []
    for d in _DAYS:
        items.append(
            {
                "basDt": d,
                "invrDpsgAmt": str(r.randrange(4 * 10**13, 7 * 10**13)),
                "onbdDrvPrdTrRcAdvAmt": str(r.randrange(10**12, 10**13)),
                "toCstRpchCndBndSlgBal": str(r.randrange(5 * 10**13, 9 * 10**13)),
                "brkTrdUcolMny": str(r.randrange(10**11, 10**12)),
                "brkTrdUcolMnyVsOppsTrdAmt": str(r.randrange(10**9, 10**11)),
                "ucolMnyVsOppsTrdRlImpt": f"{r.uniform(1, 15):.2f}",
            }
        )
    return _envelope(items)


def kofia_fund() -> str:
    r = _rng("kofia_fund")
    items = [
        {
            "basDt": "20261002",
            "ctg": "주식형",
            "tstMthdCtg": "공모",
            "nPptTotAmt": str(r.randrange(10**13, 10**14)),
        }
    ]
    return _envelope(items)


def kofia_cma() -> str:
    r = _rng("kofia_cma")
    items = []
    for d in _DAYS[:2]:
        for tgt, who in (("MMF", "개인"), ("RP", "법인")):
            items.append(
                {
                    "basDt": d,
                    "mngInvTgt": tgt,
                    "invrCtg": who,
                    "scrtCmpyCnt": str(r.randrange(5, 30)),
                    "actCnt": str(r.randrange(10**5, 10**7)),
                    "actBal": str(r.randrange(10**12, 10**14)),
                }
            )
    return _envelope(items)


FIXTURES: dict[str, Callable[[], str]] = {
    "customs_item_country.xml": customs_item_country,
    "customs_items.xml": customs_items,
    "customs_countries.xml": customs_countries,
    "customs_sigungu.xml": customs_sigungu,
    "customs_ten_day_exp_item.xml": customs_ten_day,
    "kofia_credit.json": kofia_credit,
    "kofia_capital.json": kofia_capital,
    "kofia_fund.json": kofia_fund,
    "kofia_cma.json": kofia_cma,
}


def build() -> dict[str, str]:
    """파일 이름 → 내용(UTF-8 문자열)."""
    return {name: make() for name, make in FIXTURES.items()}


def main(argv: list[str]) -> int:
    check = "--check" in argv
    stale: list[str] = []
    for name, text in build().items():
        path = HERE / name
        if check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                stale.append(name)
        else:
            path.write_text(text, encoding="utf-8")
    if stale:
        print("생성기 출력과 다르다: " + ", ".join(stale))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
