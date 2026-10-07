"""stock-dashboard 장마감 시황 — 이번에 고친 두 섹션을 **서버 없이** 만들어 보낸다.

2026-09-23 미국 지수(거래일 표기)와 🧭 코스피200 선물(근월물·원월물 시·고·저·종·
미결제약정)을 고쳤는데 Render 가 정지(suspend-by-user)돼 있어 실제 시황으로
확인할 수 없었다. stock-dashboard 는 공개 레포라 러너에서 체크아웃해 **그 코드
그대로** 두 섹션만 만든다 — 다시 구현하지 않는다(server.py 는 Flask 앱이라
import 하지 않고 함수만 꺼낸다. stock-dashboard scripts/check_futures_us_index.py
와 같은 방식).

ETF 제외·신고가 범위(시총 1,000억 이상)는 서버 DB 가 있어야 해서 여기서 못 본다.
그 사실을 메시지에 적는다.

    SD_DIR=../stock-dashboard python3 -m board.tools.dashboard_brief_preview [--send]
"""
import ast
import os
import sys
from datetime import datetime, timedelta, timezone

from board.report import telegram as TG

KST = timezone(timedelta(hours=9))
WANT = {'_fetch_us_indices_live', '_us_index_lines', '_kospi200_futures_section'}


def build(sd_dir):
    src = open(os.path.join(sd_dir, 'server.py'), encoding='utf-8').read()
    sys.path.insert(0, sd_dir)                   # _kospi200_futures_section 이 kis_api 를 부른다
    ns = {'datetime': datetime, 'timedelta': timedelta, 'timezone': timezone,
          'now_kst': lambda: datetime.now(KST)}
    got = set()
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name in WANT:
            got.add(node.name)
        elif isinstance(node, ast.Assign) and any(
                getattr(t, 'id', '') == 'US_INDEX_TICKERS' for t in node.targets):
            got.add('US_INDEX_TICKERS')
        else:
            continue
        exec(ast.get_source_segment(src, node), ns)
    missing = (WANT | {'US_INDEX_TICKERS'}) - got
    if missing:
        raise SystemExit(f'server.py 에서 못 찾음: {sorted(missing)}')

    now = datetime.now(KST)
    lines = [f'🔎 <b>장마감 시황 — 고친 섹션 미리보기</b> ({now:%m/%d %H:%M} KST)',
             '<i>Render 가 정지돼 있어 서버 대신 같은 코드를 러너에서 돌렸다. '
             'ETF 제외·신고가 범위는 서버 DB 가 필요해 Render 재개 뒤 확인.</i>', '',
             '<b>📈 지수 (미국)</b>']
    lines += ns['_us_index_lines'](('S&P 500', 'NASDAQ'))
    fut = ns['_kospi200_futures_section']()
    lines += ['', f"<b>{fut['title']}</b>"]
    lines += fut.get('items') or [f"  ⚠️ {fut.get('error') or '데이터 없음'}"]
    lines += ['', '<b>🇺🇸 미국 시황 지수</b>']
    lines += ns['_us_index_lines'](tuple(ns['US_INDEX_TICKERS']))
    return '\n'.join(lines)


def raw_bars(syms=('^GSPC', '^IXIC')):
    """거래일 표기 대조용 — yfinance 원자료 마지막 세 봉(인덱스·종가)을 로그에만 찍는다."""
    try:
        import yfinance as yf
    except Exception as e:                        # noqa: BLE001
        print('yfinance 없음', e)
        return
    for sym in syms:
        h = yf.Ticker(sym).history(period='10d', interval='1d', auto_adjust=False)
        print(f'[원자료] {sym} tz={getattr(h.index, "tz", None)}')
        for ts, row in h.tail(3).iterrows():
            print(f'   {ts!s}  close={row["Close"]:.2f}')


def main():
    raw_bars()
    text = build(os.environ.get('SD_DIR', '../stock-dashboard'))
    print(text)
    if '--send' in sys.argv:
        ok, why = TG.send(text, parse_mode='HTML')
        print('발송', 'OK' if ok else f'실패 — {why}')
        return 0 if ok else 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
