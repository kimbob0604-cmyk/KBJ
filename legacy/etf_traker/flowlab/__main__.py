"""flowlab CLI.

    python3 -m flowlab flows                  # 수급 레이어 (최신 state 날짜)
    python3 -m flowlab flows --date 20260904  # 특정 날짜
    python3 -m flowlab study                  # 이벤트 스터디 (기본 3년)
    python3 -m flowlab study --years 5
    python3 -m flowlab backfill --days 60     # (demo) 과거 날짜 state 생성 + flows 누적
    python3 -m flowlab history                # 누적 flows 한 표 + 이벤트 스터디 조인
    python3 -m flowlab verify                 # 검증 3종 + as_of 절단
    python3 -m flowlab selftest               # 파서·계산 단위 검증 (네트워크 불필요)

네이버로 나가는 접속이 막힌 환경에서는 --source demo 로 배관만 돌린다.
합성 산출물에는 source="demo" 가 박히고 리포트가 그대로 노출한다.
"""
from __future__ import annotations

import argparse
import sys
import time

from . import config as C


def _log(msg=""):
    print(msg, flush=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="flowlab", description="board 산출물 위 수급·이벤트 레이어")
    ap.add_argument("--source", choices=[C.SOURCE_NAVER, C.SOURCE_DEMO],
                    default=C.SOURCE_NAVER, help="데이터 소스 (기본 naver)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("flows", help="모듈 A — 수급 레이어")
    f.add_argument("--date", help="YYYYMMDD (기본: 최신 state)")
    f.add_argument("--limit", type=int, help="대상 종목 수 제한")
    f.add_argument("--no-report", action="store_true")
    # KBJ P3 묶음 S: `--shorts`(KRX 웹 스크랩 공매도 — krx.py)는 지웠다(docs/p3_design.md §1.10,
    # 옛 KRX_ID·KRX_PW 관문). KRX 자료는 KRX OPEN API(kbj krx.daily)로만 받는다

    s = sub.add_parser("study", help="모듈 B — 신고가 이벤트 스터디")
    s.add_argument("--years", type=int, default=C.STUDY_YEARS)
    s.add_argument("--date", help="YYYYMMDD (기본: 최신 state)")
    s.add_argument("--limit", type=int, help="유니버스 종목 수 제한")
    s.add_argument("--no-report", action="store_true")

    b = sub.add_parser("backfill", help="합성 state 를 과거 여러 날짜로 만들고 flows 를 누적 (demo 전용)")
    b.add_argument("--days", type=int, default=60, help="마지막 날 이전으로 몇 거래일 (기본 60)")
    b.add_argument("--no-flows", action="store_true", help="엔진 state 만 만들고 flows 는 건너뜀")

    h = sub.add_parser("history", help="누적 flows.json 을 한 표로 모으고 이벤트 스터디와 조인")
    h.add_argument("--no-report", action="store_true")

    sub.add_parser("selftest", help="파서·계산 단위 검증 (네트워크 불필요)")

    sp = sub.add_parser("sample", help="네이버 원문 표본을 찍는다 (응답 형태 점검)")
    sp.add_argument("--code", default="005930")

    v = sub.add_parser("verify", help="검증")
    v.add_argument("--date")
    v.add_argument("--ref", nargs="*", default=[],
                   help="수기 기준값. 예) 999990:inst5=12.3,frgn5=45.6")
    v.add_argument("--ref-event", nargs="*", default=[],
                   help="후행 수기 기준값. 예) 999990:2026-06-15:exc5=-1.23")
    v.add_argument("--show", nargs="*", default=[],
                   help="종목의 최근 기준일별 5일 수급 합산을 찍는다 (기준일 찾기용)")

    a = ap.parse_args(argv)
    C.load_env()
    t0 = time.time()

    if a.cmd == "flows":
        from . import flows
        p = flows.run(date=a.date, source=a.source, limit=a.limit,
                      write_report=not a.no_report, log=_log)
        agg = p["aggregate"]
        _log(f"완료 {time.time() - t0:.1f}초 · 결합 {agg['n_with_flows']}/{agg['n_rows']}종목 "
             f"· supported {agg['supported_n']}종목")
        return 0

    if a.cmd == "study":
        from . import eventstudy
        p = eventstudy.run(years=a.years, source=a.source, date=a.date,
                           limit=a.limit, write_report=not a.no_report, log=_log)
        o = p["overall"]
        _log(f"완료 {time.time() - t0:.1f}초 · 신고가 {o['n']:,}건 "
             f"· 20일 중앙 {o['med_20d_pct']}% · 승률 {o['win_20d_pct']}%")
        return 0

    if a.cmd == "backfill":
        from . import backfill, history
        made = backfill.run(days=a.days, source=a.source, run_flows=not a.no_flows, log=_log)
        if not a.no_flows:
            history.run(log=_log)
        _log(f"완료 {time.time() - t0:.1f}초 · state {len(made)}일 ({made[0]}~{made[-1]})")
        return 0

    if a.cmd == "history":
        from . import history
        s_ = history.run(write_report=not a.no_report, log=_log)
        j = s_.get("eventstudy_join") or {}
        _log(f"완료 {time.time() - t0:.1f}초 · {s_['n_rows']:,}행 · {s_['n_dates']}일 "
             f"· 조인 {j.get('n_joined', 0):,}건")
        return 0

    if a.cmd == "sample":
        from . import naver
        naver.sample(a.code, log=_log)
        return 0

    if a.cmd == "selftest":
        from . import tests
        return 0 if tests.run(log=_log) else 1

    if a.cmd == "verify":
        from . import verify
        for code in a.show:
            try:
                verify.show_flows(code, date=a.date, source=a.source, log=_log)
            except Exception as e:
                _log(f"  {code} 진단 실패: {type(e).__name__}: {e}")
        ok = verify.run(date=a.date, source=a.source, ref=verify.parse_ref(a.ref),
                        ref_event=verify.parse_ref_event(a.ref_event), log=_log)
        return 0 if ok else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
