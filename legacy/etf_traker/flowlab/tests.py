"""네트워크 없이 도는 파서·계산 단위 검증.

    python3 -m flowlab selftest

실데이터 경로(네이버)로 나갈 수 없는 환경에서도 파싱·절단·환산 규칙이
깨지지 않았는지 여기서 잡는다.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from . import config as C, flows, naver, prior
from .eventstudy import _bucket

# 투자자별 trend API 응답과 siseJson 응답.
#
# 원본은 **러너에서 실제로 받은 원문을 그대로 옮긴 것**이었다(.github/workflows/frgn-probe.yml).
# 옛 픽스처는 내가 지어낸 HTML 이었고, 그래서 네이버가 화면을 갈아엎어 실제
# 응답에 표가 사라진 뒤에도 이 시험은 계속 통과했다. 139종목이 전부 빈 채로
# 몇 주를 지나간 이유가 그것이다. 픽스처는 **받아 본 것과 같은 모양**이어야 한다.
#
# [KBJ P1] 공개 레포에는 네이버 원문을 넣지 않는다(U3). 그 원문과 키·문자열 형식
# ("+85,620"·"40,550"·"31.13%"·중첩 compareToPreviousPrice)·행 수가 같은 합성 응답을
# fixtures/synthetic/make_synthetic.py(시드 고정)가 만든다. 기대값은 생성기가 문자열로
# 바꾸기 **전**의 숫자(_X)다 — 파서 출력을 베낀 것이 아니다.
_SYN = json.loads((Path(__file__).resolve().parent / "fixtures" / "synthetic"
                   / "naver_samples.json").read_text(encoding="utf-8"))
TREND_FIXTURE = _SYN["trend"]
SISE_FIXTURE = _SYN["sise"]
_X = _SYN["expect"]
_R = _X["rows"]          # 행 순서 = TREND_FIXTURE 순서(최신 → 과거)


def _naver_src() -> str:
    """수집기 원문. '어느 주소를 쓰는가' 를 시험으로 못 박기 위해 읽는다."""
    return (Path(__file__).resolve().parent / "naver.py").read_text(encoding="utf-8")


def _eq(name, got, want, tol=1e-6):
    ok = (abs(got - want) <= tol) if isinstance(want, (int, float)) else (got == want)
    return name, ok, "" if ok else f"got={got!r} want={want!r}"


def cases() -> list[tuple[str, bool, str]]:
    out = []

    px = naver.parse_sise(SISE_FIXTURE)
    out.append(_eq("siseJson 행 수", len(px), 2))
    out.append(_eq("siseJson 날짜 정규화", px["date"].iloc[0], "2026-09-03"))
    out.append(_eq("siseJson 외국인소진율", float(px["frgn_rate"].iloc[1]), _X["sise_frgn_rate"][1]))
    out.append(_eq("siseJson 오름차순", px["date"].is_monotonic_increasing, True))

    fl = naver.parse_trend(TREND_FIXTURE)
    out.append(_eq("trend 행 수", len(fl), 4))
    out.append(_eq("trend 날짜 정규화", fl["date"].iloc[0], "2026-09-16"))
    out.append(_eq("trend 내림차순", fl["date"].is_monotonic_decreasing, True))
    out.append(_eq("trend 쉼표 제거", float(fl["volume"].iloc[0]), float(_R[0]["volume"])))
    # 부호는 순매수/순매도를 가르는 값이라 반드시 보존돼야 한다
    out.append(_eq("trend 부호 보존 (기관 매수)", float(fl["inst_net"].iloc[0]), float(_R[0]["inst"])))
    out.append(_eq("trend 부호 보존 (기관 매도)", float(fl["inst_net"].iloc[3]), float(_R[3]["inst"])))
    out.append(_eq("trend 부호 보존 (외인 매수)", float(fl["frgn_net"].iloc[0]), float(_R[0]["frgn"])))
    out.append(_eq("trend 보유율 % 제거", float(fl["frgn_rate"].iloc[0]), _R[0]["hold_ratio"]))
    # 등락률은 응답에 없고 종가·전일비로 계산한다 (종가 − 전일비 = 전일 종가)
    out.append(_eq("trend 등락률 계산", float(fl["chg_pct"].iloc[0]), _R[0]["chg_pct"]))
    out.append(_eq("trend 등락률 계산 (상승)", float(fl["chg_pct"].iloc[2]), _R[2]["chg_pct"]))
    # 응답에 없는 값은 지어내지 않는다
    out.append(_eq("trend 보유주수는 NaN", bool(fl["frgn_hold"].isna().all()), True))
    # 빈 응답·쓰레기에서 행을 만들어 내지 않는다
    out.append(_eq("trend 빈 응답 0행", len(naver.parse_trend([])), 0))
    out.append(_eq("trend None 0행", len(naver.parse_trend(None)), 0))
    out.append(_eq("trend 날짜 없는 행 버림",
                   len(naver.parse_trend([{"closePrice": "1,000"}])), 0))
    out.append(_eq("trend 빈 응답도 열은 갖춘다",
                   list(naver.parse_trend([]).columns),
                   ["date", "close", "chg_pct", "volume",
                    "inst_net", "frgn_net", "frgn_hold", "frgn_rate"]))

    # ── 죽은 주소로 돌아가지 않는다 ──────────────────────────────
    # 이 버그의 본체는 파서가 아니라 **없어진 엔드포인트**였다.
    # finance.naver.com/item/frgn.naver 는 302 로 Next.js 화면으로 넘어가고
    # 그 응답에는 tr/td 가 하나도 없다. 수집기가 그리로 돌아가면 139종목이
    # 다시 조용히 빈다. 픽스처 시험만으로는 그 회귀가 안 잡힌다.
    src = _naver_src()
    out.append(_eq("죽은 frgn.naver 로 수집하지 않는다",
                   "item/frgn.naver" in src, False))
    out.append(_eq("trend API 를 쓴다", "TREND_URL" in src, True))

    # 페이지 확대 — 과거 날짜일수록 더 받아야 한다
    p_recent = naver._pages_needed("2026-09-04", today="2026-09-07")
    p_old = naver._pages_needed("2026-06-15", today="2026-09-07")
    out.append(_eq("_pages_needed 최소 2페이지", p_recent >= 2, True))
    out.append(_eq("_pages_needed 과거일수록 확대", p_old > p_recent, True))

    # as_of 절단 — 미래 거래일이 창에 남으면 안 된다
    raw = fl.sort_values("date", ascending=False).reset_index(drop=True)
    cut = raw[raw["date"] <= "2026-09-15"]
    out.append(_eq("as_of 절단 후 행 수", len(cut), 3))
    out.append(_eq("as_of 절단 후 최종일", cut["date"].iloc[0], "2026-09-15"))

    # 금액 환산 — 순매매량 × 그날 종가, 억원
    m = flows.metrics(raw, mktcap_eok=10000.0, turnover_eok=310.0)
    # 기대값은 픽스처 숫자로 **여기서 따로 계산한다** — metrics 가 내놓은 값을
    # 그대로 베끼면 시험이 아무것도 못 잡는다.
    want_inst1 = _R[0]["inst"] * _R[0]["close"] / 1e8
    out.append(_eq("기관 1일 억원", m["inst_1d_eok"], round(want_inst1, 1), 0.05))
    # 창이 5일인데 픽스처는 4거래일뿐이라 4일이 다 들어간다
    want_net4 = sum((r["inst"] + r["frgn"]) * r["close"] for r in _R) / 1e8
    out.append(_eq("합계 4일(창5) 억원", m["net_5d_eok"], round(want_net4, 1), 0.05))
    out.append(_eq("창 부족 표기", m["days_5d"], 4))
    out.append(_eq("강도 bp", m["intensity_5d_bp"],
                   round(m["net_5d_eok"] / 10000.0 * 10000, 1), 0.05))
    out.append(_eq("당일 집중도 %", m["concentration_1d_pct"],
                   round(m["net_1d_eok"] / 310.0 * 100, 1), 0.05))
    out.append(_eq("외인율 변화 (창 부족 시 None)", m["frgn_rate_chg_5d_pp"], None))

    # 등급 판정
    out.append(_eq("등급 쌍끌이", flows._grade(10.0, 5.0), "쌍끌이"))
    out.append(_eq("등급 기관주도", flows._grade(10.0, -5.0), "기관주도"))
    out.append(_eq("등급 외인주도", flows._grade(-10.0, 5.0), "외인주도"))
    out.append(_eq("등급 개인주도", flows._grade(-10.0, -5.0), "개인주도"))
    out.append(_eq("supported 는 개인주도 제외",
                   flows.metrics(raw.assign(inst_net=-1e6, frgn_net=-1e6),
                                 10000.0, 310.0)["supported"], False))

    # 구간
    out.append(_eq("거래량 배수 경계 1.0 → 1~2배", _bucket(1.0, C.VOL_BUCKETS), "1~2배"))
    out.append(_eq("거래량 배수 4.0 → 4배+", _bucket(4.0, C.VOL_BUCKETS), "4배+"))
    out.append(_eq("prior 구간 매핑", prior.bucket_of(0.5), "<1배"))
    out.append(_eq("prior 구간 결측", prior.bucket_of(None), None))

    # 막대 렌더는 최대값으로 나눈다. 그날 어느 등급에도 종목이 안 잡히면 값이
    # 전부 0 이라 분모가 0 이 된다 — run #10 이 그 자리에서 죽어 검증 단계까지
    # 건너뛰어졌다. 값이 0 인 것은 사실이므로 막대만 비우고 숫자는 적는다.
    from . import report as _rp
    try:
        html = _rp._bars([("쌍끌이", 0), ("기관주도", 0), ("외인주도", 0)], "종목")
        out.append(_eq("막대 — 전부 0 이어도 죽지 않는다", "width:0.0%" in html, True))
    except ZeroDivisionError as exc:
        out.append(("막대 — 전부 0 이어도 죽지 않는다", False, f"ZeroDivisionError: {exc}"))
    out.append(_eq("막대 — 결측은 폭 0", "width:0.0%" in _rp._bars([("a", None)]), True))
    out.append(_eq("막대 — 최대값이 폭 100", "width:100.0%" in
                   _rp._bars([("a", 5), ("b", 10)]), True))

    out.append(_eq("엔진 settings.yaml 기준값 일치", C.settings_drift(), []))
    return out


def run(log=print) -> bool:
    rows = cases()
    for name, ok, note in rows:
        log(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {note}" if note else ""))
    ok = all(r[1] for r in rows)
    log(f"  → {sum(r[1] for r in rows)}/{len(rows)} 통과")
    return ok
