#!/usr/bin/env python3
"""flowlab selftest 용 합성 네이버 응답 생성기 (KBJ P1, U3).

원본 tests.py 는 러너에서 받은 네이버 trend API 원문(실제 종목 4거래일)과 siseJson
2행을 그대로 박아 두었다. 네이버 응답은 공개 레포에 넣지 않으므로 **같은 모양**
(키·문자열 형식 "+7,600"·"29,250"·"25.81%"·중첩 compareToPreviousPrice·행 수)의
합성 응답으로 바꾼다. 값은 가상이고 종목코드도 가상(999990)이다.

숫자를 먼저 정하고 그것을 네이버 문자열 형식으로 바꾼다. 문자열로 바꾸기 **전**의
숫자는 expect 에 따로 적어 둔다 — 시험은 파서 출력과 이 숫자를 견준다(파서 출력을
베끼지 않는다). 시험의 정성 조건(1행 기관·외인 순매수, 4행 기관 순매도, 1행 하락,
3행 상승, 날짜 20260916·15·14·11)은 그대로 성립하도록 만든다.

    python flowlab/fixtures/synthetic/make_synthetic.py      (legacy/etf_traker 에서)
"""
from __future__ import annotations

import json
import pathlib
import random

SEED = 20261007
OUT = pathlib.Path(__file__).resolve().parent / "naver_samples.json"
CODE = "999990"
DATES = ["20260916", "20260915", "20260914", "20260911"]   # 내림차순(원 응답과 같음)


def signed(n: int) -> str:
    """네이버 순매수 수량 형식: 양수는 '+', 천 단위 쉼표."""
    return f"{n:+,}"


def main() -> None:
    rnd = random.Random(SEED)
    # 전일비(행 i 의 전일 = 행 i+1)를 먼저 정한다. 정성 조건: 1행 하락, 3행 상승.
    diffs = [-rnd.randrange(1, 12) * 50, rnd.choice((-1, 1)) * rnd.randrange(1, 12) * 50,
             rnd.randrange(1, 12) * 50, rnd.choice((-1, 1)) * rnd.randrange(1, 12) * 50]
    closes = [0, 0, 0, 0]
    closes[3] = 40000 + rnd.randrange(0, 40) * 50       # 가장 오래된 날(0911)
    for i in (2, 1, 0):
        closes[i] = closes[i + 1] + diffs[i]
    assert diffs[0] < 0 < diffs[2]

    rows, trend = [], []
    for i, d in enumerate(DATES):
        inst = rnd.randrange(20_000, 90_000) * (-1 if i == 3 else 1)
        frgn = rnd.randrange(5_000, 400_000) * (1 if i != 1 else rnd.choice((1, -1)))
        indiv = -(inst + frgn) + rnd.randrange(-3_000, 3_000)
        hold = round(31.0 + rnd.uniform(-0.2, 0.2), 2)
        vol = rnd.randrange(800_000, 2_500_000)
        chg_pct = round(diffs[i] / (closes[i] - diffs[i]) * 100, 2)
        code, text, name = (("2", "상승", "RISING") if diffs[i] > 0 else
                            ("5", "하락", "FALLING") if diffs[i] < 0 else ("3", "보합", "EVEN"))
        trend.append({
            "itemCode": CODE, "bizdate": d,
            "foreignerPureBuyQuant": signed(frgn), "foreignerHoldRatio": f"{hold:.2f}%",
            "organPureBuyQuant": signed(inst), "individualPureBuyQuant": signed(indiv),
            "closePrice": f"{closes[i]:,}", "compareToPreviousClosePrice": f"{diffs[i]:,}",
            "compareToPreviousPrice": {"code": code, "text": text, "name": name},
            "accumulatedTradingVolume": f"{vol:,}"})
        rows.append(dict(date=d, close=closes[i], diff=diffs[i], chg_pct=chg_pct,
                         inst=inst, frgn=frgn, hold_ratio=hold, volume=vol))
    assert rows[0]["inst"] > 0 and rows[0]["frgn"] > 0 and rows[3]["inst"] < 0

    sise_rows = []
    for d in ("20260903", "20260904"):
        c = (sise_rows[-1][4] if sise_rows else rnd.randrange(700, 900) * 50) \
            + rnd.randrange(-6, 7) * 50
        sise_rows.append([d, c - 100, c + 150, c - 200, c, rnd.randrange(300_000, 1_500_000),
                          round(rnd.uniform(29.0, 31.0), 2)])
    sise = ("[['날짜', '시가', '고가', '저가', '종가', '거래량', '외국인소진율'],\n"
            + ",\n".join(json.dumps(r) for r in sise_rows) + "]")

    out = dict(
        note=f"합성 — flowlab/fixtures/synthetic/make_synthetic.py (seed={SEED}). 가상 종목·가상 값",
        trend=trend, sise=sise,
        expect=dict(rows=rows, sise_frgn_rate=[r[6] for r in sise_rows],
                    sise_dates=[r[0] for r in sise_rows]))
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"→ {OUT}")


if __name__ == "__main__":
    main()
