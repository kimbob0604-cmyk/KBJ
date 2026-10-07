#!/usr/bin/env python3
"""
국장 신고가 보드 — 진입점.

  python3 -m board.run --check       소스 엔드포인트 실측 점검 (네트워크 필요)
  python3 -m board.run --init        최초 1회. 전 종목 과거 일봉 적재 (10~20분)
  python3 -m board.run --daily       매 영업일. 수집 → 집계 → 렌더
  python3 -m board.run --engine      DB 는 그대로 두고 집계만 다시
  python3 -m board.run --render      state JSON 만 읽어 HTML 만 다시
  python3 -m board.run --test        신고가 정의 단위 검증 (네트워크 불필요)
  python3 -m board.run --demo        합성 데이터로 화면까지 한 번에 (네트워크 불필요)
  python3 -m board.run --write       서술 생성 (Claude 호출 → draft.md + claims.json)
  python3 -m board.run --write --dry-run
                                     API 를 부르지 않고 프롬프트만 뽑아 본다
  python3 -m board.run --classify    종목→섹터 배치 분류 (최초 1회 + 신규 상장)
  python3 -m board.run --excel       랭킹 표를 엑셀로
  python3 -m board.run --send        텔레그램 발송 (rankings | draft | xdigest)
  python3 -m board.run --xdigest-render
                                     X 다이제스트 렌더 (facts.json → digest.txt. docs/XDIGEST.md)
  python3 -m board.run --triggers    52주 이상 신고가 종목의 당일 재료(기사·공시·X 포워딩)만 다시
  python3 -m board.run --trigger-probe
  python3 -m board.run --x-probe        X 자동 수집 경로 실측 (XDIGEST.md 2장)
                                     트리거 소스 프로브 (네이버 질의·Google News·DART·비공식 URL·웹훅)
  python3 -m board.run --xdigest-collect  X 24시간 다이제스트 — 블루스카이 수집 (XDIGEST.md 3-1)
  python3 -m board.run --inbox       텔레그램 인박스 — 봇에 공유된 X 게시물을 state/inbox.json 으로
  python3 -m board.run --xdigest-analyze [--dry-run]
                                     X 다이제스트 분석 (posts.json → themes·facts·verify)
  python3 -m board.run --verify-adjust 005930 000660
                                     수정주가 교차 검증 (CLAUDE.md 9장 미확정 1번)

미국장 보드 (docs/US.md)
  python3 -m board.run --us-check    미국장 소스 점검 (네트워크 필요)
  python3 -m board.run --us-init     최초 1회. 유니버스 전 종목 과거 일봉
  python3 -m board.run --us-daily    매일. 수집 → 집계 → 브리프 → HTML
  python3 -m board.run --us-build    수집 없이 집계만 다시
  python3 -m board.run --us-brief    state 만 읽어 브리프 텍스트를 출력
  python3 -m board.run --us-demo     합성 데이터로 끝까지 (네트워크 불필요)
"""
import argparse
import os
import sys

from .engine import build as B
from .engine import db as DB
from .engine.config import ROOT, load

DB_PATH = os.environ.get('BOARD_DB', os.path.join(ROOT, 'board.db'))
# 사이트 루트. 링크는 이 폴더를 그대로 정적 호스트에 올린 주소 하나다.
SITE = os.environ.get('BOARD_SITE', os.path.join(os.path.dirname(ROOT), 'docs'))


def log(*a):
    print(*a, flush=True)


def _telegram_probe():
    """봇과 대화방이 살아 있는지만 본다. 발송은 --send 로 따로 한다."""
    from .report import telegram as TG
    ok, why = TG.check()
    return [('봇·대화방', ok, why)]


def cmd_check():
    from .ingest import (creds, dart, datago, flows, kis, krx, naver, news,
                         stockflows, tg_inbox, triggers)
    from .ingest import bsky                     # X 다이제스트 (b') 경로
    log('자격증명')
    for k, d, ok, m in creds.status():
        log(f'  {"OK  " if ok else "--  "} {k:<22} {d:<26} {m}')
    log('')
    groups = [
        ('KRX 오픈API — 전 종목 시세·업종 (권장 주 소스)', krx.probe),
        ('한국투자증권 KIS — 투자자별 수급', kis.probe),
        ('DART OpenAPI — 공시·업종코드', dart.probe),
        ('네이버 검색 API — 뉴스', news.probe),
        ('네이버 금융 — 폴백 (인증키 불필요)', naver.probe),
        # KIS 시장 수급이 막혀 있는 동안 이쪽이 유일한 투자자별 수급 경로다.
        # 점검에 없으면 파서가 깨져도 다음 daily 의 '빠진 데이터' 에서야 안다.
        ('네이버 금융 — 투자자별 매매동향 (KIS 폴백)', flows.probe),
        # KIS 가 23시 전후에 접속 자체가 안 되는 시간대가 있어(D-083) 종목별
        # 수급은 이 trend 경로로 넘어간다. 파싱 행수와 최신 bizdate 까지 본다.
        ('네이버 trend — 종목별 수급 폴백 (KIS 접속 불가 시)', stockflows.probe),
        # 종목별 재료(D-085). 일반명사 종목 '케이씨 029460' 한 건으로 질의·경계 규칙이
        # 실제로 도는지, Google News RSS 가 XML 을 주는지, DART 가 corp_code 로 답하는지,
        # X 포워딩 인박스 파일이 있는지를 본다. 라이브 호출은 러너에서만 돈다.
        ('트리거 소스 — 네이버 종목 질의·Google News RSS·DART 종목 공시·X 인박스', triggers.probe),
        ('공공데이터포털 — 교차검증 (선택)', datago.probe),
        # 발송 경로도 점검한다. getMe·getChat 만 부르므로 **메시지는 안 나간다**.
        # 여기까지 통과해야 18시에 조용히 실패하는 일이 없다.
        ('텔레그램 — 발송 (메시지는 보내지 않음)', _telegram_probe),
        # 들어오는 쪽도 점검한다. 웹훅이 걸려 있으면 getUpdates 가 409 로 거부되는데,
        # 그걸 daily 의 로그 한 줄에서야 알면 주말에 공유한 게시물은 이미 사라진 뒤다.
        # getUpdates 는 offset 없이 부르므로 확인 처리되지 않는다.
        ('텔레그램 인박스 — X 포워딩 (메시지는 보내지 않음)', tg_inbox.probe),
        # X 자동 수집 아홉 경로가 전부 막힌 뒤의 유일한 수집원이다(XDIGEST.md 2장).
        # 여기서 막히면 그날 다이제스트가 빈다 — 발송 시각에 알면 늦다.
        ('블루스카이 — X 다이제스트 수집', bsky.probe),
    ]
    fails = []
    for title, fn in groups:
        log(f'소스 점검 — {title}')
        try:
            rows = fn()
        except Exception as e:                    # noqa: BLE001
            log(f'  FAIL  점검 자체가 실패: {e}')
            fails.append(title)
            log('')
            continue
        for name, ok, note in rows:
            # 실패 사유는 길게 남긴다. 100자에서 끊으면 답이 그 뒤에 있을 때 러너를
            # 한 번 더 돌려야 한다 — 2026-09-22 업종 목록 진단이 '후보 주소 /market/
            # stock/kr/gr' 에서 잘려 실제로 그랬다. 통과 줄은 얼마나 받았는지만
            # 보면 되므로 짧아도 된다 (kis-probe 가 같은 이유로 [원문] 을 안 자른다).
            log(f'  {"PASS" if ok else "FAIL"}  {name:<18} '
                + (note[:100] if ok else note[:400]))
            if not ok:
                fails.append(f'{title} / {name}')
        log('')
    _check_diagnostics()
    log('필요한 API 전체 목록은 docs/APIS.md 참고.')
    if fails:
        log(f'실패 {len(fails)}건. 실데이터 수집 전에 원인을 확인하라.')
    return 1 if fails else 0


def _check_diagnostics():
    """마지막 산출의 엔진 자기검사를 찍는다 (D-NEXT-Z).

    정합성 위반·단위 의심은 배너에서 뺐다 — 읽는 사람에게 할 말이 아니다. 대신
    **고치는 사람이 볼 곳이 있어야** 뺀 것이 숨긴 것이 되지 않는다(2장 6번).
    여기가 그 자리다. 옮겨 놓고 이 단계를 안 만들어서, 한동안 `universe.json` 을
    직접 열지 않으면 아무도 못 보는 상태였다.

    점검 실패로 세지 않는다 — 소스가 살아 있는지와 엔진 산출이 스스로 모순인지는
    다른 질문이고, 종료코드는 앞의 질문 것이다.
    """
    asof = _asof()
    if not asof:
        # 아직 한 번도 안 돌린 상태다. 실패가 아니라 볼 것이 없는 것이다.
        log('산출 진단 — 아직 산출이 없다 (--daily 를 먼저 돌린다)')
        log('')
        return
    try:
        uni = B.read(asof, 'universe.json') or {}
    except Exception as e:                        # noqa: BLE001
        log(f'산출 진단 — {asof} 산출을 읽지 못했다: {type(e).__name__}')
        log('')
        return
    diags = uni.get('diagnostics') or []
    log(f'산출 진단 — 마지막 산출({uni.get("as_of") or "기준일 미상"})의 자기검사')
    if not diags:
        log('  OK    모순 없음')
    for d in diags:
        log(f'  경고  {str(d)[:400]}')
    log('')


def cmd_init(years):
    from .ingest import pipeline as P
    cfg = load()
    conn = DB.connect(DB_PATH)
    log('초기 적재 시작 — ' + (f'최근 {years}년' if years else '상장 이후 전 구간'))
    ok, bad = P.init(conn, cfg, years=years, log=log)
    conn.close()
    log(f'완료 — 성공 {ok:,} / 실패 {bad}')
    return 0


def _run_init_subprocess():
    """재적재를 **별도 프로세스**로 돌린다.

    한 프로세스에서 init 뒤에 daily 를 이어 돌렸더니 11분 뒤 트레이스백 없이
    죽었다(2026-08-29). 전 종목 전 구간 적재는 그 자체로 메모리를 크게 쓰고,
    같은 프로세스에서 수집을 한 번 더 하면 러너 한도를 넘는다. 프로세스를 나누면
    적재가 끝나면서 메모리가 통째로 반환된다.

    나누면 커넥션 상태가 섞이는 문제도 같이 사라진다. 원인이 둘 중 무엇이든 막는다.
    """
    import subprocess
    r = subprocess.run([sys.executable, '-m', 'board.run', '--init'],
                       cwd=os.path.dirname(ROOT))
    return r.returncode


def restore_sectors(conn):
    """board48 배정이 **빠진 종목만** yaml 에서 되살린다. 반환은 채운 종목 수.

    첫 판은 "board48 행이 하나라도 있으면 건너뛴다" 였다. 그런데 main 의 DB 에는
    옛 분류 행이 일부 남아 있어서 통째로 건너뛰었고, 나머지 종목은 네이버 업종
    이름을 단 채 남았다 — 보드는 48섹터 이름을 쓰면서 '실제 분류는 naver_upjong'
    이라고 적는 상태가 됐다. 전부 아니면 전무로 볼 일이 아니었다.

    이미 board48 인 행은 건드리지 않는다. 사람이 손본 배정이나 새 분류 결과를
    옛 yaml 로 덮으면 안 된다 — 되살리는 것과 덮어쓰는 것은 다르다.
    네이버 업종만 붙어 있던 행은 yaml 값으로 올려 준다.
    """
    from .classify import sectors as CS
    cur = CS.load_map()
    if not cur:
        return 0
    now = DB.now_kst()
    rows = [(c, v['sector'], 'board48', now)
            for c, v in cur.items() if v.get('sector')]
    before = conn.execute(
        "SELECT COUNT(*) FROM sector_map WHERE taxonomy='board48'").fetchone()[0]
    conn.executemany(
        'INSERT INTO sector_map(code,sector,taxonomy,updated_at) VALUES(?,?,?,?) '
        'ON CONFLICT(code) DO UPDATE SET '
        '  sector=excluded.sector, taxonomy=excluded.taxonomy, '
        '  updated_at=excluded.updated_at '
        "WHERE sector_map.taxonomy IS NOT 'board48'", rows)
    conn.commit()
    after = conn.execute(
        "SELECT COUNT(*) FROM sector_map WHERE taxonomy='board48'").fetchone()[0]
    return after - before


def cmd_inbox():
    """텔레그램 인박스 — 봇 대화에 공유된 X 게시물을 state/inbox.json 으로 (ingest/tg_inbox.py).

    메시지는 보내지 않는다. 텔레그램이 갱신을 24시간만 보관하므로 보드를 만드는 실행과
    파일 발송 재시도 실행이 맨 앞에서 이걸 한 번씩 부르고, 주말에는 인박스 크론이 돈다.
    신고가 종목 줄에 붙이는 것은 트리거 단계(ingest/triggers.py, D-085)다 — 여기서는 파일만 남긴다.

    설정이 없어 **건너뛰는 것은 0** 이다. 주말 크론이 `--inbox` 로 이 함수만 부르는데,
    토큰이 없는 레포에서 1 을 돌려주면 set -e 아래 잡이 매주 두 번 빨갛게 되어 진짜
    실패가 묻힌다(GITHUB.md 2장 '하나도 없어도 워크플로는 돌아간다'). 실패는 수집
    자체(Fetch)뿐이다.
    """
    # KBJ P2(설계 §5.8): 갱신은 KBJ notifier 웹훅이 받아 DB 에 둔다 — 여기서는 그것을 읽어
    # state/inbox.json 을 만든다(getUpdates·봇 토큰 없음). DB 주소가 없으면 건너뛴다(0).
    from kbj.config.settings import Settings
    from .ingest import tg_inbox as TI
    from .ingest.http import Fetch
    if Settings().database_url is None:
        log('  인박스 건너뜀 — KBJ_DATABASE_URL 없음 (인박스는 KBJ notifier DB)')
        return 0
    chat_ids = TI.inbox_chat_ids()
    if not chat_ids:
        log('  인박스 건너뜀 — KBJ_TELEGRAM_INBOX_CHAT_IDS 도 KBJ_TELEGRAM_CHAT_ID 도 없다')
        return 0
    keep = (load().get('inbox') or {}).get('keep_days', TI.KEEP_DAYS)
    try:
        r = TI.drain(chat_ids, TI.INBOX_PATH, log=log, keep_days=keep)
    except Fetch as ex:
        log(f'  인박스 실패: {ex}')
        return 1
    log(f'  인박스 새 {r["n_new"]}건 (X {r["n_x"]}건) · 다른 대화 {r["n_dropped_other_chat"]}건 버림 · '
        f'페이지 {r["n_pages"]} · 보관 {r["n_kept"]}건 (만료 {r["n_expired"]}건) → {TI.INBOX_PATH}')
    return 0


def cmd_daily(skip_ingest=False, skip_render=False, write=None, skip_news=False):
    cfg = load()
    # 인박스가 맨 앞이다. 텔레그램은 갱신을 24시간만 두므로 수집이 오래 걸린 날에도
    # 먼저 받아 둬야 한다. 실패해도 보드는 그대로 나온다 — 사유만 남긴다.
    log('인박스')
    try:
        cmd_inbox()
    except Exception as e:                    # noqa: BLE001
        log(f'  인박스 실패: {e}')
    if not skip_ingest:
        from .ingest import pipeline as P
        conn = DB.connect(DB_PATH)
        # 옛 규약으로 쌓은 DB 위에서 새 코드를 돌리면 안 된다. 코드는 고쳐졌는데
        # 값은 그대로라 화면만 보고는 알 수 없다 — 2026-08-29 에 실제로 그랬다.
        # 파일이 있느냐가 아니라 내용이 지금 규약인지를 본다 (D-039).
        why = P.stale_reason(conn)
        if why:
            log(f'  DB 를 다시 쌓는다 — {why}')
            conn.close()
            rc = _run_init_subprocess()
            if rc != 0:
                log(f'  재적재 실패 (exit {rc}) — 여기서 멈춘다. '
                    '깨진 DB 위에서 보드를 만들면 안 된다')
                return 1
            conn = DB.connect(DB_PATH)
        # 48섹터 배정이 DB 에서 사라졌으면 yaml 로 다시 붙인다. 네트워크도 API 도
        # 안 쓴다 — knowledge/sector_map.yaml 을 읽어 테이블에 쓰기만 한다.
        #
        # D-048 은 daily 가 덮어쓰는 것을 막았을 뿐, 이미 지워진 것을 되살리지는
        # 않는다. Actions 캐시는 브랜치 단위라 브랜치에서 분류를 다시 붙여도
        # main 은 지워진 DB 를 그대로 물려받는다. 머지만으로는 안 고쳐진다 —
        # 2026-09-01 에 실제로 그랬다.
        #
        # 그때 --classify 를 따로 태우려 했더니 그것도 조용히 실패했다.
        # concurrency 그룹에 pending 은 하나만 남아서, 뒤에 daily 를 하나 더
        # 넣는 순간 가운데 classify 가 취소된다. 사람이 순서를 지켜야 하는
        # 절차는 언젠가 어긋난다. 코드가 자기를 고치게 둔다.
        n = restore_sectors(conn)
        if n:
            log(f'  48섹터 배정이 비어 있어 sector_map.yaml 에서 {n:,}종목을 다시 붙였다')
        log('수집')
        P.daily(conn, cfg, log=log)
        asof = DB.last_asof(conn)
        mk = P.sync_market(conn, asof, log=log)
        B.write(asof, 'market.json', mk)
        conn.close()
    log('집계')
    asof = B.run(DB_PATH, cfg=cfg, log=log)
    # 뉴스는 서술보다 먼저다. 서술이 재료를 읽을 수 있어야 한다.
    # 실패해도 보드는 그대로 나온다 — news.json 이 없으면 뉴스 탭만 사유를 적는다.
    if not skip_news:
        log('뉴스')
        try:
            cmd_news()
        except Exception as e:                    # noqa: BLE001
            log(f'  뉴스 실패: {e}')
    # 종목별 재료(D-085)도 서술보다 먼저다 — 신고가 종목의 기사·공시·X 포워딩이
    # 사실 팩에 실려야 "트리거는 확인되지 않음" 대신 재료를 적는다. 실패해도
    # triggers.json 은 남고(사유 포함), 없으면 소비자가 '수집되지 않음' 을 적는다.
    log('트리거')
    try:
        cmd_triggers(asof)
    except Exception as e:                    # noqa: BLE001
        log(f'  트리거 실패: {e}')
    # 서술은 키가 있을 때만. 없으면 대시보드는 그대로 나오고 코멘트 탭만 빈다.
    if write is None:
        from .ingest import creds
        write = bool(creds.get('ANTHROPIC_API_KEY'))
    if write:
        log('서술')
        try:
            cmd_write()
        except Exception as e:                    # noqa: BLE001
            log(f'  서술 실패: {e}')
    else:
        log('서술 건너뜀 — ANTHROPIC_API_KEY 없음')
    # 52주 이상 신고가 종목의 **종목별** 수급. 시장 전체 수급으로는 '누가
    # 샀는지' 를 알 수 없다 — 레퍼런스 코멘트가 종목마다 한 줄씩 적는 값이다.
    # 실패해도 보드는 그대로 나오고, 못 받은 종목은 배너에 사유가 남는다.
    log('종목 수급')
    try:
        cmd_stockflows(asof)
    except Exception as e:                    # noqa: BLE001
        log(f'  종목 수급 실패: {e}')
    # 히트맵 툴팁 재무 (D-075). 키가 없거나 실패해도 보드는 그대로 나온다 —
    # 툴팁만 '재무 데이터 없음' 으로 뜬다.
    log('재무')
    try:
        cmd_financials(asof)
    except Exception as e:                    # noqa: BLE001
        log(f'  재무 실패: {e}')
    if not skip_render:
        # 엑셀을 먼저 만들어야 사이트의 내려받기 링크가 실제 파일을 가리킨다.
        cmd_excel()
        cmd_render()
    return 0


def _heatmap_codes(asof):
    """히트맵에 오르는 종목 — 툴팁이 필요한 곳만 받는다."""
    sec = B.read(asof, 'sectors.json') or {}
    codes = []
    for key in ('heatmap_theme', 'heatmap_sector'):
        for g in sec.get(key) or []:
            codes.extend(c.get('code') for c in g.get('cells') or [] if c.get('code'))
    return list(dict.fromkeys(codes))


def cmd_stockflows(asof=None):
    """52주 이상 신고가 종목의 종목별 수급 → state/stockflows.json.

    누가 샀는지는 시장 전체 수급으로 알 수 없다. 대상은 고가·종가 어느
    기준으로든 52주 이상을 낸 **전 종목**이다 — 거래량 문턱은 걸지 않는다.
    """
    from .ingest import stockflows as SF
    asof = asof or _asof()
    if not asof:
        log('state 가 없다. --daily 를 먼저 돌려라.')
        return 1
    nh = B.read(asof, 'newhigh.json')
    if not nh:
        log('newhigh.json 이 없다. --engine 을 먼저 돌려라.')
        return 1
    out = SF.collect(nh, asof, log=log)
    B.write(asof, 'stockflows.json', dict(out, generated_at=B.now_kst()))
    return 0


def cmd_financials(asof=None, force=False):
    """DART 주요계정을 받아 state/financials.json 을 갱신한다 (D-075)."""
    from .ingest import creds, financials as FIN
    if not creds.get('DART_API_KEY'):
        log('  DART_API_KEY 없음 — 재무 툴팁 건너뜀')
        return 0
    asof = asof or _asof()
    if not asof:
        log('  state 가 없다. --daily 를 먼저 돌려라.')
        return 1
    codes = _heatmap_codes(asof)
    if not codes:
        log('  히트맵 종목이 없다 — sectors.json 확인')
        return 0
    n = FIN.refresh(codes, load(), asof=asof, log=log, force=force)
    log(f'  재무 갱신 {n}종목 → state/financials.json')
    return 0


def latest_state():
    """가장 최근 state/YYYYMMDD. DB 없이 렌더만 다시 돌릴 때 쓴다."""
    d = os.path.join(ROOT, 'state')
    if not os.path.isdir(d):
        return None
    ds = sorted(x for x in os.listdir(d) if x.isdigit() and len(x) == 8)
    if not ds:
        return None
    x = ds[-1]
    return f'{x[:4]}-{x[4:6]}-{x[6:8]}'


def cmd_write(dry_run=False):
    """서술 생성 — 사실 팩 → Claude → 검증 → draft.md + claims.json."""
    from .engine import build as EB
    from .writer import compose
    cfg = load()
    asof = _asof()
    if not asof:
        log('state 가 없다. --daily 또는 --demo 를 먼저 돌려라.')
        return 1
    log(f'서술 생성 — 기준일 {asof}' + (' (드라이런)' if dry_run else ''))
    md, meta = compose.run(asof, cfg, log=log, dry_run=dry_run)
    name = 'draft.prompt.md' if dry_run else 'draft.md'
    p = EB.write_text(asof, name, md)
    log(f'  → {p}')
    if dry_run:
        return 0
    # 내일 대조할 주장을 남긴다 (탐지기 7)
    claims = compose.extract_claims(md, meta['pack'], cfg, log=log)
    EB.write(asof, 'claims.json', claims)
    # 테마별 서술 — 뉴스 탭이 카드 위에 얹는다. 검증(2장 4번)을 통과한
    # 본문에서 잘라낸 것이므로 여기서는 다시 검증하지 않는다.
    if meta.get('narratives'):
        EB.write(asof, 'narratives.json',
                 dict(as_of=asof, themes=meta['narratives']))
        log(f'  테마 서술 {len(meta["narratives"])}건 → narratives.json')
    if meta.get('skipped'):
        log(f'  빠진 섹션 {len(meta["skipped"])}개: {", ".join(meta["skipped"])}')
    return 0


def _index_hist(asof, sc):
    """코스피·코스닥 지수 일봉 → state/index_hist.json. 실패는 사유와 함께 남긴다.

    market.json 은 20일치만 받아 60·120일선을 못 만든다. 그 파일의 계약을 바꾸지
    않고 시그널 단계가 따로 받는다.
    """
    from datetime import date, timedelta
    from .ingest import naver
    start = (date.fromisoformat(asof)
             - timedelta(days=sc['regime']['fetch_calendar_days'])).isoformat()
    out = dict(source=naver.SOURCE, as_of=asof, generated_at=B.now_kst(),
               series={}, missing=[])
    for sym in sc['regime']['index_ma']:
        try:
            rows = naver.fetch_index(sym, start, asof)
            out['series'][sym] = [dict(asof=r['asof'], close=r.get('close'))
                                  for r in rows if r['asof'] <= asof]
        except Exception as e:                          # noqa: BLE001
            out['missing'].append(f'{sym} 지수 일봉: {e}')
            log(f'  {sym} 지수 일봉 실패: {e}')
    B.write(asof, 'index_hist.json', out)
    return out if out['series'] else None


def cmd_signals(asof=None, fetch=True):
    """스윙 시그널 → state/signals.json (docs/SIGNALS.md).

    fetch=False 면 네트워크를 쓰지 않는다 — 이미 있는 index_hist.json ·
    signal_flows.json 만 읽는다. 로컬 재계산용이다.
    """
    from .engine import signals as S
    cfg, sc = load(), S.load_cfg()
    asof = asof or _asof()
    if not asof:
        log('state 가 없다. --daily 를 먼저 돌려라.')
        return 1
    log('시그널')
    ih = _index_hist(asof, sc) if fetch else B.read(asof, 'index_hist.json')
    flows = dict((B.read(asof, 'stockflows.json') or {}).get('by_code') or {})
    extra = B.read(asof, 'signal_flows.json') or {}
    flows.update(extra.get('by_code') or {})
    conn = DB.connect(DB_PATH)
    try:
        pay = S.build(asof, cfg, sc, conn, flows_by_code=flows, index_hist=ih, log=log)
        need = S.need_flows(pay, flows)
        if fetch and need and sc['flows']['fetch_candidates']:
            from .ingest import stockflows as SF
            by_code = {x['code']: x for x in pay['breakout'] + pay['watch']}
            rows = [dict(code=c, name=by_code[c]['name'])
                    for c in need[:sc['flows']['fetch_max']]]
            got = SF.collect(None, asof, log=log, rows=rows)
            B.write(asof, 'signal_flows.json', dict(got, generated_at=B.now_kst()))
            flows.update(got.get('by_code') or {})
            pay = S.build(asof, cfg, sc, conn, flows_by_code=flows, index_hist=ih,
                          log=log)
            pay['missing'] += got.get('missing') or []
            if len(need) > sc['flows']['fetch_max']:
                pay['missing'].append(
                    f'수급은 후보 {len(need)}종목 중 {sc["flows"]["fetch_max"]}종목만 '
                    '받았습니다(fetch_max)')
        # 페이퍼 장부 (engine/ledger.py) — 오늘 신호를 쌓고 지난 신호를 실제 가격으로
        # 채점한다. 실패해도 시그널은 나간다 — 사유만 결손 줄에 남긴다.
        try:
            from .engine import ledger as L
            led = L.load()
            n_new = L.append(led, pay, sc)
            n_scored, n_closed = L.score(led, conn, sc, asof)
            L.save(led)
            pay['paper'] = L.summary(led)
            log(f'  페이퍼 — 새 {n_new} · 채점 {n_scored} · 이번에 닫힘 {n_closed}')
        except Exception as e:                          # noqa: BLE001
            pay['missing'].append(f'페이퍼 장부 실패: {e}')
            log(f'  페이퍼 장부 실패: {e}')
    finally:
        conn.close()
    # 가장 최근 백테스트 한 줄을 붙인다 — 시그널을 검증된 우위로 읽지 않게 (D-NEXT-S).
    bp = os.path.join(B.STATE, 'backtest.json')
    if os.path.exists(bp):
        import json
        with open(bp, encoding='utf-8') as f:
            bj = json.load(f)
        pay['backtest'] = dict(
            as_of=bj.get('as_of'), test_start=bj.get('test_start'),
            **{k: dict(all=(bj['results'][k]['all']), gate_open=bj['results'][k]['gate_open'])
               for k in ('breakout', 'watch', 'base') if k in (bj.get('results') or {})})
    B.write(asof, 'signals.json', pay)
    return 0


BACKTEST_DB = os.environ.get('BOARD_BACKTEST_DB', os.path.join(ROOT, 'backtest.db'))


def _load_backtest_db(sc, years=None):
    """백테스트 전용 DB 에 긴 일봉을 받는다. 보드 DB(board.db)는 건드리지 않는다.

    보드 DB 는 최근 420일만 남긴다(pipeline.KEEP_DAYS) — 워밍업 300봉을 빼면
    검증할 날이 한 달 남짓이다. 받는 길은 --init 과 같다(종목당 한 번 호출, 같은
    폴백·가드). 남기는 구간만 길다. 현재 상장 종목만 받으므로 폐지 종목은 없다.
    """
    from datetime import timedelta
    from .ingest import pipeline as P
    cfg = load()
    bt = sc['backtest']
    t = P.today_kst()
    keep_from = (t - timedelta(days=int(365.25 * (years or bt['years']))
                               + int(bt['warmup_bars'] * 1.5) + 30)).isoformat()
    conn = DB.connect(BACKTEST_DB)
    try:
        conn.execute('DELETE FROM px')
        conn.execute('DELETE FROM alltime')
        conn.commit()
        uni = P.sync_universe(conn, t.isoformat(), log)
        log(f'  백테스트 일봉 {keep_from} ~ {t} · {len(uni):,}종목')
        ok, bad = P.sync_px(conn, uni.keys(), keep_from, t.isoformat(), cfg, log=log,
                            keep_from=keep_from)
        log(f'  적재 — 성공 {len(ok):,} / 실패 {len(bad):,}')
    finally:
        conn.close()


def cmd_backtest(fetch=True, live=False):
    """스윙 시그널 백테스트 → state/backtest.json + 로그 요약 (engine/backtest.py).

    live=True 면 백테스트 전용 DB(backtest.db)에 긴 이력을 먼저 받는다. 아니면
    backtest.db 가 있으면 그것을, 없으면 보드 DB 를 읽는다(구간이 짧다).
    fetch=False 면 지수 일봉을 받지 않는다 — 게이트 위/아래 분할만 빈다.
    """
    import json
    from datetime import date, timedelta
    from .engine import backtest as BT
    from .engine import signals as S
    cfg, sc = load(), S.load_cfg()
    if live:
        _load_backtest_db(sc)
    path = BACKTEST_DB if os.path.exists(BACKTEST_DB) else DB_PATH
    if not os.path.exists(path):
        log('DB 가 없다. --init 이나 --backtest --live 를 먼저 돌려라.')
        return 1
    log(f'  읽는 DB — {os.path.basename(path)}')
    conn = DB.connect(path)
    try:
        end = DB.last_asof(conn)
        ih = None
        if fetch:
            from .ingest import naver
            start = (date.fromisoformat(end)
                     - timedelta(days=sc['backtest']['index_calendar_days'])).isoformat()
            ih = dict(source=naver.SOURCE, series={})
            for sym in sc['regime']['index_ma']:
                try:
                    ih['series'][sym] = [dict(asof=r['asof'], close=r.get('close'))
                                         for r in naver.fetch_index(sym, start, end)]
                except Exception as e:                  # noqa: BLE001
                    log(f'  {sym} 지수 일봉 실패 — 게이트 분할이 빈다: {e}')
        pay = BT.run(conn, sc, cfg, index_hist=ih, end=end, log=log)
    finally:
        conn.close()
    os.makedirs(B.STATE, exist_ok=True)
    with open(os.path.join(B.STATE, 'backtest.json'), 'w', encoding='utf-8') as f:
        json.dump(dict(pay, generated_at=B.now_kst()), f, ensure_ascii=False, indent=1)
    for line in BT.summary_lines(pay):
        log('  ' + line)
    return 0


def _index_for(sc, end, days):
    """게이트 판정용 지수 일봉. 실패하면 시장별로 빈다 — 사유는 로그에."""
    from datetime import date, timedelta
    from .ingest import naver
    start = (date.fromisoformat(end) - timedelta(days=days)).isoformat()
    ih = dict(source=naver.SOURCE, series={})
    for sym in sc['regime']['index_ma']:
        try:
            ih['series'][sym] = [dict(asof=r['asof'], close=r.get('close'))
                                 for r in naver.fetch_index(sym, start, end)]
        except Exception as e:                          # noqa: BLE001
            log(f'  {sym} 지수 일봉 실패 — 게이트 조합이 거래 0 이 된다: {e}')
    return ih


def cmd_search(fetch=True, live=False):
    """조합 탐색 → state/search.json + 로그 요약 (engine/search.py).

    최근 search.holdout_days 영업일을 봉인하고 그 앞에서만 조합을 고른다. 봉인 구간은
    후보를 한 번 재는 데만 쓴다.
    """
    import json
    from .engine import search as SE
    from .engine import signals as S
    sc = S.load_cfg()
    if live:
        _load_backtest_db(sc, years=sc['search']['years'])
    path = BACKTEST_DB if os.path.exists(BACKTEST_DB) else DB_PATH
    if not os.path.exists(path):
        log('DB 가 없다. --search --live 로 긴 이력을 받아라.')
        return 1
    conn = DB.connect(path)
    try:
        end = DB.last_asof(conn)
        ih = _index_for(sc, end, int(365.25 * sc['search']['years']) + 600) if fetch else None
        pay = SE.run(conn, sc, index_hist=ih, end=end, log=log)
    finally:
        conn.close()
    os.makedirs(B.STATE, exist_ok=True)
    with open(os.path.join(B.STATE, 'search.json'), 'w', encoding='utf-8') as f:
        json.dump(dict(pay, generated_at=B.now_kst()), f, ensure_ascii=False, indent=1)
    for line in SE.summary_lines(pay, sc):
        log('  ' + line)
    return 0


def cmd_screen(fetch=True, live=False):
    """매매 시스템 점검 → state/screen.json + 로그 요약 (engine/systems.py).

    원문에서 규칙이 숫자로 끝까지 적힌 시스템을 같은 조건으로 돌려, signals.yaml
    `screen` 기준(승률·기대값·PF, 앞·뒤 절반 모두)을 넘는 것만 통과로 적는다.
    """
    import json
    from datetime import date, timedelta
    from .engine import signals as S
    from .engine import systems as SY
    sc = S.load_cfg()
    if live:
        _load_backtest_db(sc)
    path = BACKTEST_DB if os.path.exists(BACKTEST_DB) else DB_PATH
    if not os.path.exists(path):
        log('DB 가 없다. --screen --live 로 긴 이력을 받아라.')
        return 1
    conn = DB.connect(path)
    try:
        end = DB.last_asof(conn)
        ih = None
        if fetch:
            from .ingest import naver
            start = (date.fromisoformat(end)
                     - timedelta(days=sc['backtest']['index_calendar_days'])).isoformat()
            ih = dict(source=naver.SOURCE, series={})
            for sym in sc['regime']['index_ma']:
                try:
                    ih['series'][sym] = [dict(asof=r['asof'], close=r.get('close'))
                                         for r in naver.fetch_index(sym, start, end)]
                except Exception as e:                  # noqa: BLE001
                    log(f'  {sym} 지수 일봉 실패 — 게이트 분할이 빈다: {e}')
        pay = SY.run(conn, sc, index_hist=ih, end=end, log=log)
    finally:
        conn.close()
    crit = sc['screen']
    pay['passed'] = []
    for key, r in pay['results'].items():
        for scope in ('', 'gate_open'):
            ok, why = SY.verdict(r, crit, scope)
            r['verdict' + ('_' + scope if scope else '')] = dict(ok=ok, why=why)
            if ok:
                pay['passed'].append(key + ('+gate' if scope else ''))
    os.makedirs(B.STATE, exist_ok=True)
    with open(os.path.join(B.STATE, 'screen.json'), 'w', encoding='utf-8') as f:
        json.dump(dict(pay, criteria=crit, generated_at=B.now_kst()), f,
                  ensure_ascii=False, indent=1)
    for line in SY.summary_lines(pay, crit):
        log('  ' + line)
    log(f'  통과 — {", ".join(pay["passed"]) or "없음"}')
    return 0


def _asof():
    if os.path.exists(DB_PATH):
        conn = DB.connect(DB_PATH)
        a = DB.last_asof(conn)
        conn.close()
        if a:
            return a
    return latest_state()


def cmd_classify(dry_run=False, live=False, limit=None):
    """종목 → 섹터 배치 분류. 최초 1회 + 신규 상장 때만."""
    from .classify import sectors as CS
    cfg = load()
    conn = DB.connect(DB_PATH)
    asof = DB.last_asof(conn)
    rows = []
    if asof:
        for r in conn.execute(
                'SELECT s.code, s.name, m.sector FROM snap s '
                'LEFT JOIN sector_map m ON m.code=s.code AND m.taxonomy!=? '
                'WHERE s.asof=?', ('board48', asof)):
            rows.append(dict(code=r['code'], name=r['name'], krx_sector=r['sector']))
    if not rows:
        # 전 종목이 이미 board48 이면 물어볼 게 없다. 그래도 yaml 을 DB 에 다시
        # 쓴다 — DB 가 캐시에서 날아가거나 다른 러너에서 새로 쌓였을 때, 이
        # 명령이 "다시 붙이는" 유일한 경로다. API 는 부르지 않는다.
        cur = CS.load_map()
        if cur:
            log(f'분류할 새 종목이 없다. 기존 배정 {len(cur):,}종목을 DB 에 다시 쓴다')
            CS.apply_to_db(conn, cur, log=log)
            conn.close()
            return 0
        log('분류할 종목이 없다. --daily 를 먼저 돌려라.')
        conn.close()
        return 1
    if limit:
        rows = rows[:limit]
    log(f'섹터 분류 — 후보 {len(rows):,}종목'
        + (' (드라이런)' if dry_run else '')
        + (' · 실시간' if live else ' · Batch API'))
    m = CS.run(rows, cfg, log=log, use_batch=not live, dry_run=dry_run)
    if not dry_run:
        CS.apply_to_db(conn, m, log=log)
    conn.close()
    return 0


def cmd_reclassify(sector_names, dry_run=False, live=True):
    """이미 배정된 섹터를 골라 다시 분류한다 (D-061).

    분류 체계를 쪼갤 때 쓴다 — 반도체 하나를 다섯으로 나누면, 기존에 '반도체'
    로 배정된 종목들이 새 이름 중 하나를 다시 골라야 한다. cmd_classify 는
    배정 없는 종목만 물으므로 이 경로가 따로 필요하다.

    힌트는 네이버 업종 분류를 **새로 받아** 쓴다(사용자 요청). 재분류 대상은
    board48 배정이 이미 있어서 DB 의 naver_upjong 행이 남아 있지 않다.

    locked: true (사람이 고친 것)는 건드리지 않는다.
    """
    from .classify import sectors as CS
    from .ingest import naver
    from .ingest.http import gather
    cfg = load()
    cur = CS.load_map()
    known = {x['name'] for x in (CS.taxonomy().get('sectors') or [])}
    gone = [n for n in sector_names if n in known]
    if gone:
        # 지금 체계에 있는 이름을 다시 분류하는 것도 되지만, 주된 용도는
        # 체계에서 사라진 이름이다. 어느 쪽인지 로그로 알린다.
        log(f'  참고 — {", ".join(gone)} 은 현행 체계에도 있는 이름이다')
    targets = sorted(c for c, v in cur.items()
                     if v.get('sector') in sector_names and not v.get('locked'))
    n_locked = sum(1 for v in cur.values()
                   if v.get('sector') in sector_names and v.get('locked'))
    if not targets:
        log(f'재분류할 종목이 없다 — {", ".join(sector_names)} 배정이 '
            f'{"전부 잠겨 있다" if n_locked else "없다"}')
        return 1
    log(f'재분류 — {", ".join(sector_names)} {len(targets):,}종목'
        + (f' (잠긴 {n_locked}종목 제외)' if n_locked else ''))

    # 네이버 업종을 힌트로 새로 받는다. 79개 업종 페이지를 훑는다.
    hint = {}
    try:
        idx = naver.fetch_sector_index()
        ok, bad = gather(lambda g: (g['name'], naver.fetch_sector_members(g['no'])),
                         idx['sectors'], workers=6)
        for _, (nm, codes) in ok:
            for c in codes:
                hint[c] = nm
        log(f'  네이버 업종 힌트 {len(hint):,}종목'
            + (f' (업종 {len(bad)}개 실패)' if bad else ''))
    except Exception as e:                            # noqa: BLE001
        # 힌트는 좁히는 장치일 뿐이다. 없다고 재분류를 멈추지는 않되 알린다.
        log(f'  네이버 업종 힌트 실패 — 힌트 없이 진행: {str(e)[:120]}')

    conn = DB.connect(DB_PATH)
    asof = DB.last_asof(conn)
    names = ({r['code']: r['name'] for r in conn.execute(
        'SELECT code, name FROM snap WHERE asof=?', (asof,))} if asof else {})

    # DART 표준산업분류(KSIC)를 근거로 붙인다. 1차 재분류에서 184종목 중
    # 115종목이 종목명만으로는 판단이 안 서 미분류로 떨어졌고, 0.7 을 넘긴
    # 것에도 틀린 배정이 있었다(한미반도체 → 소재부품). 이름은 증거가 아니다.
    # KSIC 는 26110(전자집적회로 제조)·29271(반도체 장비) 식으로 세분류를
    # 직접 가른다. 종목당 기업개황 한 번이라 대상 수백 건이면 몇 분이다.
    ksic = _dart_ksic(targets, log)
    rows = [dict(code=c, name=names.get(c, ''), krx_sector=hint.get(c),
                 business=ksic.get(c))
            for c in targets]
    # 배정을 비워 run() 이 다시 묻게 한다. run() 이 결과를 얹어 저장한다.
    for c in targets:
        cur[c] = {k: v for k, v in cur[c].items() if k != 'sector'}
    CS.save_map(cur)
    m = CS.run(rows, cfg, log=log, use_batch=not live, dry_run=dry_run)
    if dry_run:
        conn.close()
        return 0
    CS.apply_to_db(conn, m, log=log)
    # 결과 요약 — 어느 세분류로 몇 종목이 갔는지 바로 보이게 한다.
    dist = {}
    for c in targets:
        sec = (m.get(c) or {}).get('sector') or '(응답 없음)'
        dist[sec] = dist.get(sec, 0) + 1
    for sec, n in sorted(dist.items(), key=lambda x: -x[1]):
        log(f'    {sec:<12} {n:>4}종목')
    conn.close()
    return 0


def _dart_ksic(codes, log=print):
    """종목 → 'KSIC(표준산업분류) NNNNN' 문자열. 실패한 종목은 그냥 비운다.

    코드 값은 DART 기업개황이 준 그대로다. 우리가 이름을 붙이지 않는다 —
    KSIC 해석은 분류 모델이 한다. 값을 지어내지 않는 선에서 줄 수 있는
    가장 싼 실증 근거다.
    """
    from .ingest import dart
    from .ingest.http import gather
    try:
        dart.corp_codes()                   # 캐시를 먼저 만든다 — 병렬 중복 방지
    except Exception as e:                  # noqa: BLE001
        log(f'  DART 종목코드 매핑 실패 — KSIC 근거 없이 진행: {str(e)[:100]}')
        return {}
    # DART 는 분당 호출 한도가 있다. 2워커 + 호출당 소폭 지연으로
    # 분당 수백 건 아래로 누른다 — 1,300종목이어도 몇 분이다.
    import time as _t

    def _one(c):
        _t.sleep(0.1)
        return c, dart.company(c)

    ok, bad = gather(_one, list(codes), workers=2)
    out = {}
    for _, (c, info) in ok:
        code = (info or {}).get('induty_code')
        if code:
            out[c] = f'KSIC(표준산업분류) {code}'
    log(f'  DART KSIC 근거 {len(out):,}종목'
        + (f' · 실패 {len(bad)}종목' if bad else ''))
    return out


def cmd_excel():
    """랭킹 표를 엑셀로. 사이트의 x/ 아래에 둔다 — 화면에서 바로 내려받는다."""
    from .export import excel
    asof = _asof()
    if not asof:
        log('state 가 없다. --daily 또는 --demo 를 먼저 돌려라.')
        return 1
    out = os.path.join(SITE, 'x', f'rankings-{asof}.xlsx')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    log(f'엑셀 → {excel.from_state(asof, out)}')
    return 0


def cmd_send(what='rankings', only_fresh=False, once=False, asof=None, inbox=True):
    """텔레그램 발송."""
    from .engine.build import read, read_text
    from .report import telegram as TG
    # 파일 발송 전에 인박스를 비운다. 재시도 크론(`--send files`)이 하루 두 번 더
    # 깨어나므로 이 자리가 텔레그램의 24시간 보존 창을 메운다. 실패해도 발송은 계속한다.
    # `files` 에만 거는 이유: daily 는 맨 앞에서 이미 읽었고 draft·rankings 까지 매번
    # 읽으면 한 실행에 네 번 돌아 '새 0건' 로그가 발송 로그를 흐린다. daily 사슬은
    # --no-inbox 로 이 자리도 건너뛴다.
    if inbox and what == 'files':
        try:
            cmd_inbox()
        except Exception as e:                    # noqa: BLE001
            log(f'  인박스 실패: {e}')
    if what == 'files':
        # 보내는 대상은 docs/ 다. 기준일도 사이트의 보관 목록에서 읽는다 —
        # DB 캐시가 빈 러너에서도 발송은 성립해야 하고, 캐시가 낡았을 때
        # DB 날짜로 판단하면 멀쩡한 사이트를 낡았다고 오판한다.
        site_asof = _site_asof() or _asof()
        if not site_asof:
            log('발송할 사이트가 없다 — docs/d/index.json 도 state 도 없다.')
            return 1
        return _send_files(site_asof, TG, only_fresh, once=once)
    if what == 'note':
        # --asof 가 있으면 그 날, 없으면 보드 기준일. 노트 파일 이름이 곧 날짜다.
        return _send_note(asof or _asof(), TG)
    if what == 'guru':
        # 글로벌 투자 구루 브리핑(docs/GURU.md). 기준일은 KST 달력일이다.
        from .guru import pipeline as GP
        return GP.send(asof, once=once, log=log)
    if what == 'xdigest':
        # X 다이제스트는 보드 state 를 읽지 않는다 — 기준일이 KST 달력일이고
        # 휴장일에도 나간다(XDIGEST.md 4장). 통마다 평문으로 직접 보낸다.
        from .xdigest import render as X
        from .xdigest import send as XS
        return XS.run(asof or X.today(), once=once, log=log)
    asof = _asof()
    if not asof:
        log('state 가 없다.')
        return 1
    if what == 'backtest':
        import json
        from .engine import backtest as BT
        p = os.path.join(B.STATE, 'backtest.json')
        if not os.path.exists(p):
            log('backtest.json 이 없다. --backtest 를 먼저 돌려라.')
            return 1
        with open(p, encoding='utf-8') as f:
            pay = json.load(f)
        ok, why = TG.send('\n'.join(['*스윙 시그널 백테스트*'] +
                                     [TG._safe(x) for x in BT.summary_lines(pay)]),
                          kind='board.manual')
        log(f'  발송 {"성공" if ok else "실패"} — {why}')
        return 0 if ok else 1
    if what == 'search':
        import json
        from .engine import search as SE
        from .engine import signals as S
        p = os.path.join(B.STATE, 'search.json')
        if not os.path.exists(p):
            log('search.json 이 없다. --search 를 먼저 돌려라.')
            return 1
        with open(p, encoding='utf-8') as f:
            pay = json.load(f)
        lines = SE.summary_lines(pay, S.load_cfg())
        # 텔레그램 한 통에 36줄은 길다 — 머리 4줄 + 1단계 통과만 + 꼬리
        keep = lines[:4] + [x for x in lines[4:-1] if not x.startswith('✗')] + lines[-1:]
        ok, why = TG.send('\n'.join(['*시스템 조합 탐색*'] + [TG._safe(x) for x in keep]),
                          kind='board.manual')
        log(f'  발송 {"성공" if ok else "실패"} — {why}')
        return 0 if ok else 1
    if what == 'screen':
        import json
        from .engine import systems as SY
        p = os.path.join(B.STATE, 'screen.json')
        if not os.path.exists(p):
            log('screen.json 이 없다. --screen 을 먼저 돌려라.')
            return 1
        with open(p, encoding='utf-8') as f:
            pay = json.load(f)
        body = ['*매매 시스템 점검*'] + [TG._safe(x) for x in SY.summary_lines(pay, pay['criteria'])]
        body.append(TG._safe('통과: ' + (', '.join(pay.get('passed') or []) or '없음')))
        ok, why = TG.send('\n'.join(body), kind='board.manual')
        log(f'  발송 {"성공" if ok else "실패"} — {why}')
        return 0 if ok else 1
    if what == 'signals':
        from .engine import signals as S
        from .report import signals_tg as ST
        sig = read(asof, 'signals.json')
        if not sig:
            log('signals.json 이 없다. --signals 를 먼저 돌려라.')
            return 1
        ok, why = TG.send(ST.message(sig, S.load_cfg()), kind='board.signals')
        log(f'  발송 {"성공" if ok else "실패"} — {why}')
        return 0 if ok else 1
    if what == 'draft':
        md = read_text(asof, 'draft.md')
        if not md:
            log('draft.md 가 없다. --write 를 먼저 돌려라.')
            return 1
        text = TG.draft_message(md)
    else:
        rk = read(asof, 'rankings.json')
        if not rk:
            log('rankings.json 이 없다.')
            return 1
        # 52주 이상 신고가와 종목별 수급·재료를 같은 메시지 맨 앞에 싣는다.
        # 어느 것이 없어도 메시지는 나간다 — 랭킹만이라도 가는 게 맞다.
        # triggers.json 이 없으면 load() 가 '수집되지 않음' 결손 한 줄짜리 대역을 준다.
        from .ingest import triggers as TR
        text = TG.rankings_message(rk, newhigh=read(asof, 'newhigh.json'),
                                   stockflows=read(asof, 'stockflows.json'),
                                   triggers=TR.load(asof))
    # KBJ P2(설계 §5.10 #23·#24): notifier 종류를 넘긴다 — 발송은 notifier 대기열
    ok, why = TG.send(text, kind='board.draft' if what == 'draft' else 'board.rankings')
    log(f'  발송 {"성공" if ok else "실패"} — {why}')
    return 0 if ok else 1


NOTES = os.path.join(ROOT, 'notes')


def _send_note(date, TG):
    """일일 노트를 텔레그램으로. 계약은 docs/NOTE.md.

    **부분 발송을 하지 않는다.** 이름 하나를 못 찾으면 세 통 모두 보내지 않고
    사유만 적는다 — 종목 하나가 조용히 빠진 노트는 읽는 사람이 그 사실을 알
    길이 없다(D3). 랭킹 발송이 결손을 머리에 적고 그대로 나가는 것과 다른데,
    랭킹은 보드가 만든 표라 무엇이 빠졌는지 화면에서 다시 볼 수 있어서다.
    """
    import yaml
    from .engine.build import read
    from .report import note as NOTE
    if not date:
        log('기준일을 모른다 — state 도 --asof 도 없다.')
        return 1
    path = os.path.join(NOTES, f'{date}.yaml')
    if not os.path.exists(path):
        have = sorted(f[:-5] for f in os.listdir(NOTES)) if os.path.isdir(NOTES) else []
        log(f'노트가 없다: {path}')
        log(f'  있는 노트: {", ".join(have[-5:]) or "없음"}')
        return 1
    with open(path, encoding='utf-8') as f:
        doc = yaml.safe_load(f) or {}
    # 수치는 노트가 적은 date 의 state 에서 가져온다 (계약 1장).
    # yaml 은 `date: 2026-09-21` 을 datetime.date 로 읽는다. state_dir 은 문자열을
    # 받으므로 여기서 한 번 문자열로 만든다 — 안 하면 date.replace 가 불려 깨진다.
    asof = str(doc.get('date') or date)
    msgs, errs = NOTE.note_messages(doc, read(asof, 'universe.json'),
                                    read(asof, 'newhigh.json'))
    if errs:
        log(f'노트를 보내지 않았다 ({asof}):')
        for e in errs:
            log(f'  · {e}')
        return 1
    for i, m in enumerate(msgs, 1):
        ok, why = TG.send(m, kind='board.note')
        log(f'  {i}/{len(msgs)} {"성공" if ok else "실패"} — {why}')
        if not ok:
            return 1
    return 0


def _site_asof():
    """발행된 사이트의 최신 기준일. 없으면 None."""
    import json
    p = os.path.join(SITE, 'd', 'index.json')
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding='utf-8') as f:
            return (json.load(f) or {}).get('latest')
    except ValueError:
        return None


SENT_MARK = ('api', 'sent.json')


def _sent_mark(asof=None):
    """발송 표식을 읽거나(asof=None) 쓴다. 반환: 이미 보낸 기준일 또는 None.

    예약 실행이 여러 번 깨어나도 같은 보드를 두 번 보내지 않게 하는 자리다.
    GitHub 크론은 제때 못 깨우는 일이 잦아(피크 시간대에는 몇 시간씩 밀린다)
    발송 시도를 여러 번 걸어 두는데, 표식이 없으면 그만큼 중복으로 온다.

    사이트(docs/) 안에 두어 커밋과 함께 남는다. 러너 캐시에 두면 캐시가
    비워진 날 표식이 사라져 또 보낸다.
    """
    import json
    p = os.path.join(SITE, *SENT_MARK)
    if asof is None:
        if not os.path.exists(p):
            return None
        try:
            with open(p, encoding='utf-8') as f:
                return (json.load(f) or {}).get('as_of')
        except ValueError:
            return None
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(dict(as_of=asof, sent_at=B.now_kst()), f, ensure_ascii=False)
    return asof


def _send_files(asof, TG, only_fresh, once=False):
    """보드 HTML 과 랭킹 엑셀을 첨부로 보낸다 (D-063 — 장 마감 후 자동 발송).

    only_fresh 는 예약 실행용 가드다. 휴장일에도 크론은 돌고 daily 는 직전
    거래일 보드를 다시 만든다 — 그대로 보내면 같은 파일이 매 휴일 다시 온다.
    보드 기준일이 오늘(KST)이 아니면 발송을 생략하되, 생략했다는 사실은 남긴다.
    손으로 부르는 --send files 는 가드 없이 항상 보낸다.

    once 는 재시도용 가드다. 그 기준일을 이미 보냈으면 아무 일도 하지 않는다.
    """
    if only_fresh:
        # KBJ P2(설계 §7.2): '기준일 ≠ 오늘이면 휴장 추정' 을 캘린더로 명시한다 — 오늘이 KRX
        # 휴장일이면 그렇다고 적고, 거래일인데 기준일이 오늘이 아니면 오늘 보드가 아직 없다.
        from kbj.core.calendar_compat import is_kr_holiday
        from kbj.core.time import now_kst
        now = now_kst()
        today = now.date().isoformat()
        if is_kr_holiday(now):
            log(f'  오늘({today})은 KRX 휴장일이다 — 발송을 생략한다')
            return 0
        if asof != today:
            log(f'  보드 기준일 {asof} 이 오늘({today})이 아니다 — '
                '오늘 보드가 아직 없어 발송을 생략한다')
            return 0
    if once and _sent_mark() == asof:
        log(f'  {asof} 보드는 이미 보냈다 — 다시 보내지 않는다')
        return 0
    # 보내는 것은 **보관본**이다. 사이트 루트(index.html)는 데이터가 없는 고정
    # 셸이라(web/app.py) 첨부로 열면 표가 한 줄도 없다 — 셸은 같은 사이트의
    # api/latest.json 을 받아야 그려지는데, 내려받은 파일에는 그 사이트가 없다.
    index = os.path.join(SITE, 'd', f'{asof}.html')
    if not os.path.exists(index):
        index = os.path.join(SITE, 'index.html')
    xlsx = _xlsx_path(asof)
    if not os.path.exists(index):
        log('  보낼 보드 파일이 없다 — --render 를 먼저 돌려라')
        return 1
    # 채팅방에서 "index.html" 은 무슨 파일인지 안 보인다. 기준일을 이름에 박아
    # 보낸다. 원본은 건드리지 않고 사본을 만들어 보낸 뒤 지운다.
    import shutil
    import tempfile
    tmp = tempfile.mkdtemp(prefix='board-send-')
    try:
        html_copy = os.path.join(tmp, f'신고가보드-{asof}.html')
        shutil.copyfile(index, html_copy)
        n_fail = 0
        ok, why = TG.send_document(html_copy, caption=f'신고가 보드 {asof}')
        log(f'  HTML {"발송" if ok else "실패"} — {why}')
        n_fail += 0 if ok else 1
        if xlsx:
            ok, why = TG.send_document(xlsx, caption=f'랭킹 엑셀 {asof}')
            log(f'  엑셀 {"발송" if ok else "실패"} — {why}')
            n_fail += 0 if ok else 1
        else:
            # 엑셀이 없으면 없는 채로 보내되 그 사실을 알린다. 조용히 한 개만
            # 보내면 받은 쪽은 원래 한 개였다고 믿는다.
            TG.send(f'랭킹 엑셀({asof})이 없어 HTML 만 보냈습니다. '
                    '--excel 실행 여부를 확인하세요', kind='board.files')
            log('  엑셀 없음 — 그 사실을 함께 발송')
        # 표식은 **보낸 뒤에만** 남긴다. 먼저 남기면 발송이 실패한 날도
        # 재시도가 "이미 보냈다" 며 건너뛴다.
        if not n_fail:
            _sent_mark(asof)
        return 1 if n_fail else 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def cmd_news():
    """섹터 뉴스 수집 — sectors.json·universe.json 을 읽어 news.json 을 쓴다.

    가격은 무엇이 일어났는지만 말한다. 왜인지는 재료에 있다.
    """
    from .ingest import news as N
    cfg = load()
    asof = _asof()
    if not asof:
        log('state 가 없다. --daily 를 먼저 돌려라.')
        return 1
    sec = B.read(asof, 'sectors.json')
    if not sec:
        log('sectors.json 이 없다. --engine 을 먼저 돌려라.')
        return 1
    out = N.collect(asof, sec, B.read(asof, 'universe.json') or {}, cfg, log=log)
    out = dict(out, generated_at=B.now_kst())
    B.write(asof, 'news.json', out)
    got = sum(len(t.get('articles') or []) for t in out['themes'])
    log(f'  뉴스 → 테마 {len(out["themes"])}개 · 기사 {got}건 · 질의 {out["n_queries"]}회')
    for m in out['missing']:
        log(f'  빠짐: {m}')
    return 0


def cmd_triggers(asof=None):
    """52주 이상 신고가 종목의 당일 재료 → state/triggers.json (D-085).

    대상은 stockflows 와 같은 집합(고가·종가 어느 기준으로든 52주 이상)이다.
    **예외가 나도 파일은 남긴다** — sources 전부 failed + missing 한 줄. 파일이
    없으면 소비자는 '수집되지 않음(단계 실패)' 밖에 못 적지만, 있으면 왜인지까지
    남는다. 인박스(X 포워딩)는 다른 단계가 만든 파일을 읽기만 한다.
    """
    from .ingest import triggers as TR
    cfg = load()
    asof = asof or _asof()
    if not asof:
        log('state 가 없다. --daily 를 먼저 돌려라.')
        return 1
    try:
        nh = B.read(asof, 'newhigh.json')
        if not nh:
            raise RuntimeError('newhigh.json 이 없다 — --engine 을 먼저 돌려라')
        out = TR.collect(nh, B.read(asof, 'universe.json') or {}, asof, cfg, log=log)
    except Exception as e:                        # noqa: BLE001
        B.write(asof, 'triggers.json',
                dict(TR.failed(asof, cfg, e), generated_at=B.now_kst()))
        log(f'  트리거 실패 — {type(e).__name__}: {str(e)[:160]} (사유를 triggers.json 에 남겼다)')
        return 1
    B.write(asof, 'triggers.json', dict(out, generated_at=B.now_kst()))
    for m in out['missing']:
        log(f'  빠짐: {m}')
    return 0


def cmd_x_probe():
    """X 자동 수집 경로 실측 (XDIGEST.md 2장 (b)).

    사용자가 규약 위반 경로를 쓰기로 정했다(2026-09-22). 다만 그 경로들이 지금
    살아 있는지는 모른다 — 조사 시점 보고로는 대부분 막혔다. 수집기를 쓰기 전에
    러너에서 눌러 본다. **로그인 경로는 여기 없다** (계정 정지 위험).
    """
    from .ingest import xsource as X
    rows = X.probe_table(log=log)
    # 전부 막힌 것도 답이다. 잡을 빨갛게 만들지 않는다 — 판정이 아니라 측정이다.
    return 0 if rows else 1


def cmd_bsky_probe():
    """블루스카이 설계 실측 (XDIGEST.md 2장 (b')).

    사용자가 A 안(블루스카이)을 골랐다(2026-09-22). 수집기를 쓰기 전에 둘을
    잰다 — 예시 12계정이 거기 있나, 주제 검색이 하루치 물량을 주나. 판정이
    아니라 측정이라 실패가 있어도 잡을 빨갛게 만들지 않는다.
    """
    from .ingest import xsource as X
    rows = X.probe_bsky(log=log)
    return 0 if rows else 1
def cmd_xdigest_check():
    """다이제스트 소스만 점검 — 블루스카이가 열려 있나 (Actions 의 mode=check).

    보드 `--check` 에도 같은 프로브가 들어 있지만(`bsky.probe`), 그쪽은 KIS·DART·
    네이버까지 전부 누르고 종료코드도 그 전부를 반영한다. 다이제스트 워크플로가
    발송 전에 알고 싶은 것은 **수집원 하나**다 — 다른 소스가 죽은 날 다이제스트
    점검이 빨개지면 신호가 아니라 잡음이 된다.

    막힌 것을 '0건' 으로 적지 않는다. 판정은 `probe` 가 하고(200 JSON 을 받았나)
    24시간 물량은 note 로만 남는다 — 조용한 시간대의 0건은 막힘이 아니다.
    """
    from .ingest import bsky as BS
    log('소스 점검 — 블루스카이 (X 다이제스트 수집)')
    try:
        rows = BS.probe()
    except Exception as e:                        # noqa: BLE001 - 사유를 남기고 실패로
        log(f'  FAIL  점검 자체가 실패: {e}')
        return 1
    bad = 0
    for name, ok, note in rows:
        log(f'  {"OK  " if ok else "FAIL"} {name} — {note}')
        bad += 0 if ok else 1
    return 1 if bad else 0


def cmd_xdigest_collect(asof=None, ignore_mark=False):
    """X 24시간 다이제스트 — 블루스카이 수집만 (XDIGEST.md 3-1).

    산출은 `board/state/xdigest/YYYYMMDD/posts.json` 하나다. 분석·렌더·발송은
    그 파일만 읽는다(CLAUDE.md 3장).

    **이미 표식이 있는 기준일은 다시 쓰지 않는다**(XDIGEST.md 4장). 늦은 크론이
    깨어나 그날 posts.json 을 덮으면 이미 발송한 다이제스트의 근거 파일이 갈려
    검증(3-3)을 나중에 재현할 수 없다. 표식이 없으면 그냥 쓴다.
    """
    import json
    from datetime import datetime, timedelta, timezone
    from .ingest import bsky as BS
    asof = asof or datetime.now(timezone(timedelta(hours=9))).date().isoformat()
    mark_path = os.path.join(SITE, 'api', 'xdigest-sent.json')
    mark = None
    if os.path.exists(mark_path):
        try:
            with open(mark_path, encoding='utf-8') as f:
                mark = (json.load(f) or {}).get('as_of')
        except (OSError, ValueError) as e:
            # 표식을 못 읽은 것과 표식이 없는 것은 다르다. 삼키지 않는다.
            # 파싱 실패(ValueError)만 잡으면 퍼미션·읽기 오류(OSError)에 수집이
            # 시작도 못 하고 죽어 그날 posts.json 이 아예 안 생긴다 —
            # '표식이 없으면 그냥 쓴다'(XDIGEST.md 3-1)가 깨진다.
            # UnicodeDecodeError 는 ValueError 의 하위라 여기 이미 든다.
            log(f'  표식 {mark_path} 을 읽지 못했다 — {e}. 없는 것으로 보고 쓴다')
    if mark == asof and not ignore_mark:
        log(f'  {asof} 다이제스트는 이미 보냈다 — posts.json 을 다시 쓰지 않는다')
        return 0
    cfg = BS.load_cfg()
    win = BS.window_of(asof, cfg)
    log(f'블루스카이 수집 — 기준일 {asof} · 창 {win["start"]} ~ {win["end"]}')
    out = BS.collect(asof, win, cfg, log=log)
    path = BS.save(asof, out)
    b = out['sources']['bluesky']
    log(f'  {path}')
    log(f'  질의 {b["queries"]} · 호출 {b["calls"]} · 원본 {b["got"]}건 · '
        f'창 안 {b["in_window"]}건 · 남김 {b["kept"]}건 · '
        f'못 받은 질의 {b["failed"]}개(그중 막힘 {b["blocked"]}개)')
    # 구획별 창 안 건수 — 새 구획의 질의가 물량을 주는지 여기서 본다(D-NEXT-Q).
    log('  구획별 창 안 ' + ' · '.join(f'{k} {v}건' for k, v in
                                    (b.get('by_topic') or {}).items()))
    cov = out['coverage']
    log(f'  창 안 게시 시각 {cov["first"] or "없음"} ~ {cov["last"] or "없음"}')
    for e in b['errors']:
        log(f'  빠짐: {e}')
    # 전부 실패해도 파일은 남긴다. 종료코드로만 알린다 — 파일에 '없다' 고 적지
    # 않는다(XDIGEST.md 3-1). 막힘(403/429/503)과 전송 오류를 함께 센다: 어느
    # 쪽이든 그날 받은 것이 0 이라는 사실은 워크플로가 봐야 한다.
    # `errors` 줄 수로 세지 않는다. 그 목록에는 파싱 드롭·폴백 사유 줄도 있어
    # 수집이 된 날도 '전부 실패' 가 된다 — 판정은 `failed`(못 받은 질의 수)다.
    if b['queries'] and b['failed'] >= b['queries']:
        log('  질의가 전부 실패했다 — posts.json 은 남겼고 사유는 coverage.gaps 에 있다')
        return 1
    return 0


def cmd_xdigest_preview(send=False):
    """X 다이제스트 미리보기 — 수집·분석·렌더를 **임시 디렉터리**에서 한 번 더 돈다.

    그날 다이제스트를 이미 보냈으면 수집이 표식을 보고 멈춘다(4장). 그래서
    구획을 바꾼 날(D-NEXT-Q)에는 다음 아침까지 새 구획이 실제로 어떻게
    나오는지 볼 길이 없었다. 미리보기는 표식을 보지 않되 **아무것도 남기지
    않는다** — state/xdigest 도, 표식도, sent.json 도 건드리지 않는다. 커밋할
    것이 없으니 그날 발송본의 근거 파일도 그대로다.

    send=True 면 첫 통 머리에 '미리보기' 를 붙여 다이제스트 방으로 보낸다.
    """
    import tempfile
    from .ingest import bsky as BS
    from .report import telegram as TG
    from .xdigest import render as XR, send as XS
    tmp = tempfile.mkdtemp(prefix='xdigest-preview-')
    BS.XSTATE = XR.STATE = tmp
    asof = XR.today()
    log(f'미리보기 — 기준일 {asof} · 산출은 {tmp} 에만 (표식·state 무관)')
    rc = cmd_xdigest_collect(asof, ignore_mark=True)
    if rc:
        return rc
    cmd_xdigest_analyze(asof=asof)          # 실패해도 렌더는 결손 경로로 돈다(3-6)
    rc = cmd_xdigest_render(asof)
    if rc:
        return rc
    text = XR.read(asof, 'digest.txt') or ''
    log('')
    log(text)
    if not send:
        return 0
    cid, where = XS.chat()
    if not cid:
        log('  보낼 대화방이 없다 — XDIGEST_CHAT_ID 또는 TELEGRAM_CHAT_ID')
        return 1
    parts = XR.split(text)
    parts[0] = '🔎 미리보기 — 오늘 발송분과 별개로 한 번 더 돌린 것 (기록 없음)\n' + parts[0]
    for n, part in enumerate(parts, 1):
        ok, why = TG.send(part, chat_id=cid, parse_mode=None)
        log(f'  {n}/{len(parts)}통 → {where} {"OK" if ok else "실패"} {why if not ok else ""}')
        if not ok:
            return 1
    return 0


def cmd_trigger_probe(asof=None):
    """트리거 소스 프로브 — 조사에서 정한 항목을 순서대로 찍는다 (D-085).

    판정은 PASS / FAIL / 기록 셋이다. '기록' 은 판정 없이 값만 남긴다(질의별 제목
    포함 비율, 비공식 URL 의 상태·키, 웹훅 상태). 이 환경은 외부 접속이 막혀
    있으므로 **Actions 에서 mode=trigger-probe 로** 돌린다. 결과는 /tmp/check.txt 로
    실행 요약에 실린다.
    """
    from .ingest import triggers as TR
    from .ingest.pipeline import today_kst
    asof = asof or _asof() or today_kst().isoformat()
    log(f'트리거 소스 프로브 — 기준일 {asof}')
    rows = TR.probe_table(asof, load(), log=log)
    n_fail = sum(1 for st, _l, _n in rows if st == 'FAIL')
    log('')
    log(f'PASS {sum(1 for st, _l, _n in rows if st == "PASS")} · FAIL {n_fail} · '
        f'기록 {sum(1 for st, _l, _n in rows if st == "기록")}')
    log('기록 줄은 판정이 아니다 — 네이버 금융 비공식 URL 은 BACKLOG 의 반영 항목이고, '
        '질의별 비율은 settings.yaml triggers.naver_queries 를 고를 근거다.')
    return 1 if n_fail else 0


def cmd_render():
    """보드를 그려 사이트에 올린다. 최신은 항상 사이트 루트다.

    루트에 놓는 것은 **데이터가 없는 고정 셸**이다. 열릴 때 api/latest.json 을
    받아 그리고, 열어 둔 채로 주기적으로 다시 받아 갱신한다 — 링크를 다시 열지
    않아도 최신이 된다. 구운 문서는 보관본(d/)과 아티팩트 조각에만 쓴다.
    """
    from .web import app, payload, render, site
    asof = _asof()
    if not asof:
        log('렌더할 state 가 없다. --daily 또는 --demo 를 먼저 돌려라.')
        return 1
    xlsx = _xlsx_path(asof)
    html = render.from_state(asof)
    site.write_data(SITE, asof, payload.from_state(asof, xlsx=xlsx), log=log)
    site.publish(SITE, asof, html, xlsx=xlsx, log=log, index_html=app.build())
    site.prune_files(SITE, log=log)
    log('  보려면 → python3 -m board.run --serve')
    return 0


def _xlsx_path(asof):
    p = os.path.join(SITE, 'x', f'rankings-{asof}.xlsx')
    return p if os.path.exists(p) else None


def cmd_xdigest_render(asof=None):
    """X 다이제스트 렌더 — `facts.json`·`posts.json` → `digest.txt` (XDIGEST.md 3-4).

    `facts.json` 이 없으면 빈 다이제스트를 보내지 않고 **분석 실패 경로**로
    렌더한다(3-6) — 머리 세 줄 + 결손 줄 + 계정별 건수 + 창 안 URL 이다.
    아무것도 안 오는 것보다 낫고 지어낸 것이 없다.
    """
    from .xdigest import render as X
    asof = asof or X.today()
    posts = X.read(asof, 'posts.json')
    if not posts:
        log(f'posts.json 이 없다 ({asof}) — 수집을 먼저 돌려라.')
        return 1
    facts = X.read(asof, 'facts.json')
    if not facts:
        log('facts.json 이 없다 — 분석 실패 경로로 렌더한다 (XDIGEST.md 3-6)')
        facts = {'asof': asof, 'error': 'facts.json 이 없다 — 분석 단계가 돌지 않았다'}
    text = X.compose(facts, posts, X.cfg_load())
    p = X.write_digest(asof, text)
    parts = X.split(text)
    log(f'  digest → {p} ({len(text)}자 · {len(parts)}통 {[len(x) for x in parts]})')
    for i in X.over(parts):
        log(f'  {i}통이 4,096자를 넘는다 — 발송 때 telegram.send 가 줄 경계에서 한 번 더 나눈다')
    return 0


def cmd_test():
    """tests/test_*.py 를 전부 찾아 돌린다. 새 시험 파일을 여기 등록할 필요 없다."""
    import unittest
    d = os.path.join(ROOT, 'tests')
    suite = unittest.defaultTestLoader.discover(
        d, pattern='test_*.py', top_level_dir=os.path.dirname(ROOT))
    r = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if r.wasSuccessful() else 1


def cmd_kis_probe(markets):
    """KIS 시장 수급의 FID_INPUT_ISCD_2 · FID_INPUT_DATE_2 값을 실측으로 좁힌다.

    CLAUDE.md 9장 2번. 필드 **이름**은 응답이 알려줬지만(D-032) 올바른 **값**은
    모른다. 빈 값으로 부르면 호출은 200 으로 성공하면서 0 만 돌아오고(D-037),
    그 0 을 내보내면 화면에 '기관계 +0억원' 이 사실처럼 찍힌다.

    문서를 뒤져 맞히는 대신 후보 격자를 실제로 눌러 보고 결과를 표로 남긴다.
    이 환경은 외부 접속이 막혀 있으므로 **Actions 에서 mode=kis-probe 로** 돌린다.
    """
    from .ingest import creds, kis
    if not creds.has('KIS_APP_KEY', 'KIS_APP_SECRET'):
        log('KIS_APP_KEY / KIS_APP_SECRET 이 없다. 레포 Secrets 를 확인하라.')
        return 1
    rc = 0
    for market in markets:
        name = {'0001': '코스피', '1001': '코스닥'}.get(market, market)
        log(f'KIS 시장 수급 파라미터 후보 — {name}({market})')
        # 첫 합격에서 멈추지 않는다. 한 번의 실행으로 격자 전체를 봐야
        # 그 합격이 우연인지 아닌지 가릴 수 있다.
        try:
            rows = kis.probe_market_params(market, stop_on_hit=False)
        except Exception as e:                       # noqa: BLE001
            log(f'  FAIL  진단 자체가 실패: {e}')
            rc = 1
            continue
        hit = False
        for label, ok, note in rows:
            # 원문 줄은 자르지 않는다. 답이 그 안에 있는데 150자에서 끊으면
            # 진단을 한 번 더 돌려야 한다 — 실제로 그랬다.
            n = note if label.startswith('[원문]') else note[:150]
            log(f'  {"PASS" if ok else "--  "}  {label:<28} {n}')
            hit = hit or label == '확정 후보'
        if not hit:
            log('  합격 조합 없음 — 격자를 더 넓히거나 KIS 문서의 TR 을 확인하라')
            rc = 1
        log('')
    log('합격 조합이 나오면 ingest/kis.py 의 ENDPOINTS["market_investor"] 에 '
        '그 params 와 out 키를 박고, DECISIONS.md 에 근거를 남긴다.')
    return rc


def cmd_diagnose_banner(codes):
    """배너에 남은 빨간 줄의 정체를 원자료로 확인한다.

    두 줄이 며칠째 그대로다.
      · 가드가 잡았는데 DART 가 corp_code 를 못 준 코드 (520101)
      · themes.yaml 시드가 상장 종목 마스터에 없는 것 (29건)

    둘 다 원인이 '무엇인지 모른다' 라서 코드만 고쳐서는 안 없어진다. 시세만
    보면 ETN 인지 주식인지 갈리지 않고, 이름만 보면 사명 변경인지 남남인지
    갈리지 않는다. D-056 이 520101 을 보류로 남긴 것도 이 확인을 안 해서다.
    사실을 먼저 확정하고 그다음에 고친다.

    state 를 읽지 않는다 — 러너의 state 는 실행마다 사라지므로 이 진단만 따로
    돌릴 수 있어야 한다. 필요한 것을 그 자리에서 받는다.
    """
    from .engine import themes as TH
    from .engine.config import themes as load_themes
    from .ingest import funds, naver
    cfg = load()

    log('■ 상장 종목 마스터')
    uni = naver.fetch_universe(log=log)
    log(f'  전 종목 {len(uni):,}')
    try:
        etf = funds.fetch_etf_codes()
        log(f'  네이버 ETF 목록 {len(etf):,}종목')
    except Exception as ex:                            # noqa: BLE001
        etf = None
        log(f'  ETF 목록 실패 — 목록 대조를 건너뛴다: {ex}')

    log('')
    log('■ 가드 대상 코드의 정체')
    for c in codes:
        x = uni.get(c)
        if not x:
            log(f'  {c}  마스터에 없다 — 상장폐지·비상장이거나 우리가 훑는 '
                '시장(코스피·코스닥) 밖이다')
            continue
        nm = x.get('name') or ''
        tags = []
        if etf is not None and c in etf:
            tags.append('네이버 ETF 목록에 있음')
        if funds.is_etn(nm):
            tags.append('이름에 ETN')
        if funds.looks_like_fund(nm):
            tags.append('펀드 이름 패턴')
        log(f'  {c}  "{nm}" · {x.get("market")} · 종가 {x.get("close")} · '
            + (' / '.join(tags) if tags else
               '펀드 표시 없음 — 지금은 주식으로 취급하고 있다'))

    log('')
    log('■ themes.yaml 시드 미해결 (전체)')
    _, _, unres = TH.build(load_themes(), uni, cfg)
    unknown = [x for x in unres if not x.get('known')]
    seen, uniq = set(), []
    for x in unknown:
        if x['seed'] not in seen:
            seen.add(x['seed'])
            uniq.append(x)

    # near 후보는 '사명 변경으로 볼 만한 것' 만 통과시키는 좁은 규칙이라
    # (themes._near 의 60% 공통부분 조건), 진짜로 마스터에 있는지 없는지는
    # 그것만 봐서는 모른다. 부분문자열로 한 번 더 훑어 '있는데 못 붙은 것' 과
    # '정말 없는 것' 을 가른다. 엔씨소프트 같은 대형주가 미해결로 나오면
    # 이름 문제가 아니라 수집이 덜 된 것이므로 처방이 완전히 다르다.
    disp = {}
    for code, x in uni.items():
        if x.get('name'):
            disp.setdefault(TH.norm(x['name']), (code, x['name']))

    def _sub(seed):
        k = TH.norm(seed)
        if not k:
            return []
        return [f'{nm}({c})' for key, (c, nm) in disp.items()
                if (k in key or key in k) and key != k][:3]

    log(f'  미해결 {len(uniq)}건 (시드 기준 {len(unknown)}건)')
    for x in uniq:
        near = ', '.join(x.get('near') or []) or '후보 없음'
        sub = ', '.join(_sub(x['seed'])) or '없음'
        log(f'    {x["seed"]:<16} [{x["theme"]}]')
        log(f'      비슷한 이름  {near}')
        log(f'      부분일치     {sub}')

    # 마스터가 온전한지 대조군으로 확인한다. 이게 없으면 위 목록을 '이름 문제'
    # 로 읽어 버릴 수 있다 — 실제로 D-051 에서 LIG넥스원이 그렇게 빠졌다.
    log('')
    log('■ 마스터 대조군 (여기 하나라도 없으면 수집이 덜 된 것이다)')
    # **이름과 코드를 따로 본다.** 이름만 보면 사명 변경과 수집 결손이 같은
    # '없음' 으로 나온다. 코드로도 없으면 그날 목록에 아예 안 온 것이다.
    for nm, code in (('삼성전자', '005930'), ('SK하이닉스', '000660'),
                     ('엔씨소프트', '036570'), ('LIG넥스원', '079550'),
                     ('카카오', '035720'), ('HD현대미포', '010620')):
        by_nm = disp.get(TH.norm(nm))
        by_cd = uni.get(code)
        log(f'  {nm:<10} 이름 {"있음" if by_nm else "없음"} · '
            f'코드 {code} ' + (f'있음 "{by_cd.get("name")}"' if by_cd
                               else '없음 ← 목록에 안 옴'))
    log('')
    log('  → 같은 회사가 확실하면 themes.yaml 의 시드 이름을 바꾸고,')
    log('    상장폐지·비상장이면 unlisted 에 사유를 적는다. 확신이 없으면 둔다.')
    return 0


def cmd_verify_adjust(codes, start='2015-01-01'):
    """수정주가 교차 검증 — CLAUDE.md 9장 미확정 1번, T1 의 첫 작업.

    같은 종목의 같은 구간을 두 소스에서 받아 종가를 맞춰 본다. 액면분할·무상증자를
    한쪽만 반영하면 겹치는 날짜의 종가가 정수배로 어긋난다. 역사적 신고가 판정이
    이 결과에 달려 있으므로, 결과를 docs/DECISIONS.md 에 적고 소스를 고정한
    다음에 다음 단계로 간다.

    분할 이력이 있는 종목을 넣어야 의미가 있다. 인자 없이 돌리면 액면분할 이력이
    알려진 종목 몇 개를 기본값으로 쓴다.
    """
    from .engine.newhigh import split_guard
    from .ingest import dart, datago, naver
    from .ingest.http import Fetch
    cfg = load()
    ratio = cfg['integrity']['split_guard_ratio']
    end = '2039-12-31'
    has_key = bool(os.environ.get('DATAGO_KEY'))
    log('수정주가 교차 검증')
    log('  기준 소스  네이버 siseJson')
    log(f'  대조 소스  공공데이터포털 getStockPriceInfo '
        f'{"(DATAGO_KEY 있음)" if has_key else "(DATAGO_KEY 없음 — 단독 점검만 수행)"}')
    log('')
    verdicts = []
    for c in codes:
        try:
            a = naver.fetch_ohlcv(c, start, end)
        except Fetch as ex:
            log(f'  {c}  네이버 수집 실패: {ex}')
            continue
        sa, da, na, _ = split_guard(a, ratio)
        line = (f'  {c}  네이버 {a[0]["asof"]}~{a[-1]["asof"]} {len(a)}봉 · '
                f'{"점프 " + na if sa else "점프 없음"}')
        log(line)
        # 점프가 있으면 공시로 사유를 확정한다. 시세만 봐서는 '주식 수가 바뀐
        # 것' 과 '그날 크게 움직인 것' 이 갈리지 않는다 — D-001 이 미결인 채로
        # 12종목을 역사적 판정에서 빼고 있던 이유가 그것이다.
        if sa and da:
            log('       ' + _action_verdict(dart, c, da))
        if not has_key:
            verdicts.append((c, 'naver-only', sa, None, None))
            continue
        try:
            b = datago.fetch_ohlcv(c, start, end)
        except Exception as ex:                       # noqa: BLE001
            log(line + f' / 공공데이터 실패: {ex}')
            continue
        sb, _, nb, _ = split_guard(b, ratio)
        ba = {r['asof']: r['close'] for r in a}
        both = [(d, ba[d], r['close']) for r in b if (d := r['asof']) in ba]
        diff = [(d, x, y) for d, x, y in both if x and y and abs(x / y - 1) > 0.005]
        # 소스가 같은 종목을 준 것이 맞는지 이름으로 확인한다. 코드로 질의해도
        # 응답이 다른 종목이면 '불일치 1,634일' 이 데이터 문제처럼 보이는데
        # 실제로는 다른 회사를 비교한 것이다. 둘을 눈으로 갈라야 한다.
        nm = {x.get('name') for x in b if x.get('name')}
        log(f'       공공 {b[0]["asof"]}~{b[-1]["asof"]} {len(b)}봉 · '
            f'{"점프 " + nb if sb else "점프 없음"}'
            + (f' · 종목명 {", ".join(sorted(nm)[:2])}' if nm else ''))
        share = f'{len(diff) / len(both) * 100:.0f}%' if both else '–'
        log(f'       겹치는 {len(both)}일 중 종가 불일치 {len(diff)}일 ({share})'
            + (f' · 예: {diff[0][0]} 네이버 {diff[0][1]:,.0f} / 공공 {diff[0][2]:,.0f}'
               if diff else ''))
        verdicts.append((c, 'both', sa, sb, len(diff)))
    log('')
    log('읽는 법 — 점프와 공시')
    log('  점프 있음 + 공시 있음  → 수정주가 미반영이다. 역사적 판정에서 빼는 게 맞다.')
    log('  점프 있음 + 공시 없음  → 실제 등락이다. 가드가 과하다 — 되돌려야 한다.')
    log('  점프 없음              → 수정주가다. 대조군이 여기 있어야 판정을 믿을 수 있다.')
    log('  조회 실패              → 판단하지 않는다. 못 본 것과 없는 것은 다르다.')
    log('')
    log('읽는 법 — 종가 불일치 (여기서 한 번 잘못 읽었다)')
    log('  공공데이터는 그날 실제로 체결된 값이라 **수정주가가 아니다**. 네이버는')
    log('  수정주가다. 그래서 창 안에 기업행위가 한 번이라도 있으면 그 이전 날이')
    log('  전부 어긋난다 — 불일치 1,634/1,635 는 고장이 아니라 "오래전에 분할·')
    log('  병합이 있었다" 는 뜻이다. 실제로 삼성전자는 마지막 분할이 창(2020~)')
    log('  밖이라 불일치가 0일이었다.')
    log('  불일치 비율은 마지막 기업행위가 얼마나 오래됐는지로 읽어라. 소스가')
    log('  틀렸는지는 종목명이 같은지로 본다 — 이름이 다르면 다른 회사를 비교한 것이다.')
    log('')
    log('결과를 docs/DECISIONS.md D-001 에 적고 소스를 고정한 다음 진행하라.')
    return 0


def _action_verdict(dart, code, day):
    """점프가 난 날 주변에 주식 수가 바뀌는 공시가 있었는지 묻는다.

    조회 자체가 실패하면 '공시 없음' 이라고 적지 않는다. 없는 것과 못 본 것을
    섞으면 그 다음 판단이 통째로 틀린다 (CLAUDE.md 2장 6번).
    """
    from datetime import date as _date
    from datetime import timedelta as _td
    try:
        d = _date.fromisoformat(day)
    except ValueError:
        return f'공시 대조 못 함 — 점프 날짜 {day!r} 를 못 읽었다'
    w = _td(days=dart.ACTION_WINDOW)
    try:
        acts = dart.stock_actions(code, (d - w).isoformat(), (d + w).isoformat())
    except Exception as e:                            # noqa: BLE001
        return f'공시 대조 실패 — {str(e)[:120]}'
    if not acts:
        return (f'공시 없음 ({day} ±{dart.ACTION_WINDOW}일) — 실제 등락으로 보인다. '
                '역사적 판정에서 빼는 것은 과하다')
    near = ', '.join(f'{a["date"]} {a["title"]}' for a in acts[:3])
    return f'공시 {len(acts)}건 — {near}'


# 액면분할 이력이 알려진 종목들. 실데이터 의심 목록이 없을 때의 대조군이다.
# 분할이 없는 종목만 넣으면 '이상 없음'만 나와서 아무것도 검증하지 못한다.
KNOWN_SPLITS = ['005930', '051910', '032830', '005380', '128940']


def default_verify_codes(limit=12):
    """검증할 종목. 실데이터에서 실제로 걸린 의심 종목을 먼저 본다.

    D-001 이 미결인 이유는 "분할 종목이 어떻게 되나" 가 아니라 "지금 역사적
    판정에서 빠지고 있는 그 종목들이 진짜 미반영인가" 다. 아는 분할 종목만
    돌리면 그 질문에 답하지 못한다. `universe.json` 의 의심 목록을 먼저 쓰고,
    비어 있을 때만 대조군으로 떨어진다.
    """
    asof = _asof()
    sus = ((B.read(asof, 'universe.json') or {}).get('adjusted_price_suspects')
           if asof else None) or []
    codes = [x['code'] for x in sus if x.get('code')][:limit]
    if codes:
        log(f'  의심 종목 {len(codes)}건을 state/{asof} 에서 읽었다')
        # 대조군을 하나 섞는다. 전부 의심이면 "다 미반영" 인지 "판정이 과한지"
        # 구분이 안 된다. 삼성전자는 분할 이력이 있고 수정주가가 확실하다.
        return codes + ['005930']
    log('  의심 종목 목록이 없다 — 분할 이력이 알려진 대조군으로 돌린다')
    return KNOWN_SPLITS


def cmd_serve(host, port, open_browser=True, site=None):
    """만들어 둔 사이트를 로컬에 띄운다. site 를 주면 그 폴더를 연다(데모용)."""
    from .web import serve as _serve
    try:
        _serve.serve(site or SITE, host=host, port=port,
                     open_browser=open_browser, log=log)
    except FileNotFoundError as e:
        log(f'  {e}')
        return 1
    return 0


# 데모가 쓰는 사이트 폴더. **실제 사이트와 분리한다.**
#
# DB 는 `.demo` 로 나눠 놨으면서 사이트는 docs/ 를 그대로 썼다. 그래서
# `--demo` 를 한 번 돌리면 그날 만든 진짜 보드(index.html · artifact.html)가
# 합성 데이터로 덮이고, 보관함에 2025-02-24 같은 합성 날짜가 끼어 날짜
# 선택기에 실제 날짜와 나란히 남았다. 그 상태로 커밋하면 발행된 주소의
# 보드가 합성 데이터가 된다. 실제로 docs/d/2025-02-24.html 이 그렇게
# 레포에 들어가 있었다.
#
# 페이지 안에 "합성 데이터입니다" 라고 적혀 있긴 하지만, 그건 읽은 사람에게만
# 통한다. 애초에 섞이지 않게 하는 것이 맞다.
DEMO_SITE = os.environ.get('BOARD_DEMO_SITE', SITE + '-demo')


def cmd_demo():
    """합성 데이터로 수집부터 렌더까지. 네트워크 없이 화면을 확인한다."""
    from .tests import demo
    rc = demo.main(DB_PATH + '.demo', DEMO_SITE, log=log)
    log(f'  실제 사이트({SITE})는 건드리지 않았습니다')
    return rc


# ─────────────────────────── 미국장 ───────────────────────────
# 국장과 파일·DB·state 를 나눈다. 정의(신고가 판정)만 공유한다 — docs/US.md.
US_DB = os.environ.get('US_DB', os.path.join(ROOT, 'us_board.db'))
US_CFG = os.path.join(ROOT, 'config', 'us.yaml')
US_OUT = os.environ.get('US_OUT', os.path.join(SITE, 'us', 'index.html'))


def us_cfg():
    """미국장 설정. 소스 어댑터만 환경변수로 덮을 수 있게 한다.

    러너에서 stooq 가 일일 한도에 걸리면 워크플로가 `US_HISTORY=yahoo` 로
    그날만 갈아 끼운다. 설정 파일을 고쳐 커밋하는 것보다 낫다 — 커밋한 값은
    다음 날에도 남는다. 임계값은 여기서 못 바꾼다. 그건 config/us.yaml 하나다.
    """
    cfg = dict(load(US_CFG))
    src = dict(cfg['sources'])
    for key, env in (('history', 'US_HISTORY'), ('universe', 'US_UNIVERSE')):
        v = os.environ.get(env)
        if v:
            src[key] = v
    cfg['sources'] = src
    return cfg


def cmd_us_check():
    from .us import sources as S
    cfg = us_cfg()
    log('미국장 소스 점검')
    fails = []
    for name, ok, note in S.probe(cfg):
        log(f'  {"PASS" if ok else "FAIL"}  {name:<16} {note[:120]}')
        if not ok:
            fails.append(name)
    log('')
    log(f'유니버스 소스 {cfg["sources"]["universe"]} · 일봉 소스 {cfg["sources"]["history"]}')
    if fails:
        log(f'실패 {len(fails)}건 — docs/US.md 의 "소스가 막혔을 때" 를 보라')
    return 1 if fails else 0


def cmd_us_init(years=None, limit=None):
    from .us import db as UDB
    from .us import pipeline as P
    cfg = us_cfg()
    conn = UDB.connect(US_DB)
    log(f'미국장 최초 적재 — {US_DB}')
    asof, fails, meta = P.init(conn, cfg, years or 2, log=log, limit=limit)
    log(f'  기준일 {asof} · 일봉 실패 {len(fails)}종목')
    for t, why in fails[:10]:
        log(f'    {t}: {why}')
    return 0


def cmd_us_daily(skip_ingest=False, skip_render=False, limit=None):
    from .us import brief as BR
    from .us import build as B
    from .us import db as UDB
    from .us import pipeline as P
    from .us import render as R
    cfg = us_cfg()
    conn = UDB.connect(US_DB)
    missing = []
    if not skip_ingest:
        log('미국장 수집')
        _, missing, meta = P.daily(conn, cfg, log=log, limit=limit)
        # 미분류로 남은 산업 문자열을 그대로 남긴다. 규칙은 이 파일을 보고 늘린다.
        _write_unmapped(conn, B, meta)
    asof, board = B.run(US_DB, cfg=cfg, log=log)
    if missing:
        board['missing'] = list(board.get('missing') or []) + missing
        B.write(asof, 'board.json', board)
    rows = (B.read(asof, 'universe.json') or {}).get('rows') or []
    text = BR.render(board, rows, cfg)
    B.write_text(asof, 'brief.txt', text)
    log('')
    log(text)
    if not skip_render:
        out = R.write(board, rows, cfg, US_OUT)
        log('')
        log(f'  화면 → {out}')
    return 0


def _write_unmapped(conn, B, meta):
    """미분류 산업 목록을 state 에 남긴다. 규칙을 늘릴 근거가 된다."""
    from .us import db as UDB
    asof = UDB.last_asof(conn)
    if not asof:
        return
    B.write(asof, 'unmapped.json',
            dict(asof=asof, n_unmapped=meta.get('unmapped'),
                 n_blank=meta.get('blank'),
                 # 배정 근거별 종목 수. 'name'(회사 이름 폴백)이 많으면 industry
                 # 규칙을 더 손봐야 한다는 뜻이다 — 약한 신호에 기대고 있다.
                 by_source=meta.get('by_source') or {},
                 rows=meta.get('unmapped_rows') or []))


def cmd_us_brief(asof=None):
    """이미 만든 state 만 읽어 브리프를 다시 낸다. 수집도 집계도 하지 않는다."""
    from .us import brief as BR
    from .us import build as B
    from .us import db as UDB
    cfg = us_cfg()
    if not asof:
        conn = UDB.connect(US_DB)
        asof = UDB.last_asof(conn)
    board = B.read(asof, 'board.json') if asof else None
    if not board:
        log('state 가 없다. --us-daily 를 먼저 돌려라.')
        return 1
    rows = (B.read(asof, 'universe.json') or {}).get('rows') or []
    log(BR.render(board, rows, cfg))
    return 0


def cmd_us_send(what='brief', only_fresh=False):
    """브리프를 텔레그램으로 보낸다. 국장 --send 와 같은 자격증명·같은 코드다.

    only_fresh 는 예약 실행용이다. 보드 기준일이 최근 영업일이 아니면(휴장·수집
    실패) 스스로 생략한다 — 같은 브리프가 휴일마다 다시 가면 안 된다.
    """
    from datetime import date, timedelta
    from .report import telegram as TG
    from .us import brief as BR
    from .us import build as B
    from .us import db as UDB
    conn = UDB.connect(US_DB)
    asof = UDB.last_asof(conn)
    if not asof:
        log('state 가 없다. --us-daily 를 먼저 돌려라.')
        return 1
    if only_fresh:
        # 기준일이 사흘보다 오래되면 보내지 않는다. 주말·공휴일을 넘기는 폭이다.
        try:
            stale = (date.today() - date.fromisoformat(asof)).days
        except ValueError:
            stale = 0
        if stale > 3:
            log(f'  기준일 {asof} 이 {stale}일 전이다 — 발송을 생략한다')
            return 0
    board = B.read(asof, 'board.json')
    if not board:
        log(f'  {asof} state 가 없다. --us-daily 를 먼저 돌려라.')
        return 1
    rows = (B.read(asof, 'universe.json') or {}).get('rows') or []
    # 발송본은 HTML 이다. 평문으로 보내면 ②의 표가 가변폭 글꼴에서 무너지고
    # 절 머리가 본문과 구분되지 않는다. 조각은 **절 경계**에서만 나눈다 —
    # 태그가 안 닫히면 텔레그램이 메시지를 통째로 거절한다.
    chunks = BR.telegram_chunks(board, rows, us_cfg())
    rc = 0
    for i, chunk in enumerate(chunks, 1):
        # 종류는 notifier legacy_kinds(cmd_us_send → board.us)가 정한다 — 발송 대역 시험이 옛
        # 호출 모양(kind 없음)을 고정한다(test_us_pipeline.TestSendPath)
        ok, why = TG.send(chunk, parse_mode='HTML')
        log(f'  브리프 {i}/{len(chunks)} {"성공" if ok else "실패"} — {why}')
        if not ok:
            rc = 1
            break
    if what == 'files' and os.path.exists(US_OUT):
        ok2, why2 = TG.send_document(US_OUT, caption=f'미국장 신고가 보드 {asof}',
                                     kind='board.us')
        log(f'  화면 첨부 {"성공" if ok2 else "실패"} — {why2}')
        rc = rc or (0 if ok2 else 1)
    return rc


def cmd_us_search(fetch=True):
    """미국장 시스템 조합 탐색 → state_us/search.json + 로그 요약 (us/btsearch.py).

    국장 --search 와 같은 방법·기준이다. 최근 holdout_days 를 봉인하고 그 앞에서만
    고른다. 긴 일봉은 us_backtest.db 에 따로 쌓는다(보드 DB 는 2년뿐이다).
    """
    import json
    from .us import btsearch as BS
    from .us import db as UDB
    cfg = us_cfg()
    conn = UDB.connect(BS.DB_PATH)
    try:
        if fetch:
            log(f'미국장 조합 탐색 — 일봉 수집 → {BS.DB_PATH}')
            fails, n = BS.fetch(conn, cfg, log=log)
            log(f'  수집 실패 {len(fails)}/{n}종목' +
                (' — ' + ', '.join(f'{t}: {w}' for t, w in fails[:3]) if fails else ''))
        pay = BS.run(conn, cfg, log=log)
    finally:
        conn.close()
    out = os.path.join(ROOT, 'state_us')
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, 'search.json'), 'w', encoding='utf-8') as f:
        json.dump(dict(pay, source='board-us-search', generated_at=B.now_kst()), f,
                  ensure_ascii=False, indent=1)
    for line in BS.summary_lines(pay, cfg):
        log('  ' + line)
    return 0


def cmd_us_verify():
    """미국장 DB 를 눈으로 검증한다. 고치지 않고 **사실만** 찍는다.

    보는 것은 넷이다.
      · 몇 종목의 일봉을 들고 있나, 봉 수 분포는 어떤가
      · 기준일이 하나로 모이나 (흩어지면 수집이 절반만 된 것이다)
      · 52주 창(252거래일)을 채운 종목이 몇이나 되나 — 이게 부족하면 ②의
        추이와 52주 라벨이 통째로 과소 집계된다
      · 스냅샷(시총·섹터)과 일봉이 같은 종목 집합을 보고 있나

    마지막에 재구축이 필요한지 한 줄로 판정한다. 판정만 하고 실행하지 않는다 —
    지우고 다시 받는 것은 사람이 결정한다.
    """
    from .us import db as UDB
    cfg = us_cfg()
    if not os.path.exists(US_DB):
        log(f'DB 가 없다: {US_DB}')
        log('→ 재구축 필요: python3 -m board.run --us-init')
        return 1
    conn = UDB.connect(US_DB)
    counts = UDB.bar_counts(conn)
    if not counts:
        log('일봉이 한 행도 없다.')
        log('→ 재구축 필요: python3 -m board.run --us-init')
        return 1

    need = cfg['newhigh']['lookback']['w52'] + cfg['brief']['trend_days'] + 10
    ns = sorted(v['n'] for v in counts.values())
    total = sum(ns)
    asof = UDB.last_asof(conn)
    snaps = UDB.snapshot(conn, asof) if asof else {}
    lasts = {}
    for t, v in counts.items():
        lasts[v['last']] = lasts.get(v['last'], 0) + 1
    top_last = sorted(lasts.items(), key=lambda kv: -kv[1])[:3]
    full = sum(1 for n in ns if n >= need)
    d60 = cfg['newhigh']['lookback']['d60']
    ok60 = sum(1 for n in ns if n >= d60 + 1)

    log(f'미국장 DB — {US_DB}')
    log(f'  기준일         {asof}')
    log(f'  일봉 종목      {len(counts):,}종목 · {total:,}행')
    log(f'  봉 수 분포     최소 {ns[0]} · 중앙 {ns[len(ns) // 2]} · 최대 {ns[-1]}')
    log(f'  52주 창 충족   {full:,}/{len(counts):,}종목 ({full / len(counts) * 100:.0f}%) '
        f'· 기준 {need}봉')
    log(f'  60일 창 충족   {ok60:,}/{len(counts):,}종목')
    log(f'  마지막 봉 날짜 ' + ' · '.join(f'{d} {n}종목' for d, n in top_last))
    log(f'  스냅샷         {len(snaps):,}종목 (시총·섹터)')
    for basis in (cfg['newhigh']['default_basis'],):
        lab = UDB.labels_on(conn, asof, basis) if asof else {}
        log(f'  라벨({basis})   {len(lab):,}종목')
    missing_snap = [t for t in counts if t not in snaps]
    if missing_snap:
        log(f'  ** 일봉은 있는데 스냅샷이 없는 종목 {len(missing_snap)} '
            f'({", ".join(missing_snap[:5])}…)')

    log('')
    bad = []
    if full / len(counts) < 0.5:
        bad.append(f'52주 창을 채운 종목이 절반도 안 된다 ({full}/{len(counts)})')
    if len(top_last) > 1 and top_last[0][1] < len(counts) * 0.8:
        bad.append(f'마지막 봉 날짜가 흩어져 있다 (최빈 {top_last[0][0]} {top_last[0][1]}종목)')
    if not snaps:
        bad.append('스냅샷이 없다 — 시총·섹터를 모르면 유니버스 하한을 못 건다')
    if bad:
        for x in bad:
            log(f'  ✗ {x}')
        log('→ 재구축 권장: python3 -m board.run --us-init')
        return 1
    log('  ✓ 재구축이 필요해 보이지 않는다')
    return 0


def cmd_us_demo():
    """합성 데이터로 집계→브리프→화면까지. 네트워크도 DB 도 필요 없다."""
    from .us import brief as BR
    from .us import build as B
    from .us import demo as D
    from .us import render as R
    cfg = us_cfg()
    series, snaps, asof = D.make()
    rows, board = B.build(series, snaps, asof, cfg,
                          missing=['합성 데이터 — 이 화면의 숫자는 전부 가짜다'], log=log)
    log('')
    log(BR.render(board, rows, cfg))
    out = os.path.join(os.path.dirname(ROOT), 'docs-demo', 'us', 'index.html')
    R.write(board, rows, cfg, out)
    log('')
    log(f'  데모 화면 → {out} (실제 사이트는 건드리지 않았습니다)')
    return 0


XDIGEST_CFG = os.path.join(ROOT, 'config', 'xdigest.yaml')


def xdigest_cfg(path=None):
    """보드 설정 + 다이제스트 설정. 반환 dict.

    `config/xdigest.yaml` 을 읽지 않으면 `xdigest.scope`·`xdigest.limits` 가 조용히
    죽은 설정이 된다 — 계약 6장 3번이 다이제스트 설정을 그 파일에 두라고 했고
    `xdigest/analyze.limits()`·`run()` 은 `cfg['xdigest']` 를 본다. 파일이 없으면
    코드 기본값으로 돈다(파일이 없다는 사실 자체는 결손이 아니다).

    계약 부록은 파일 자체가 다이제스트 설정이다(`focus:` 가 최상위). `xdigest:`
    머리를 쓴 파일도 받는다 — 어느 모양이든 설정이 죽으면 안 된다.
    """
    import yaml as _yaml

    cfg = dict(load())          # load() 는 캐시된 dict 다 — 그대로 고치지 않는다
    p = path or XDIGEST_CFG
    if not os.path.exists(p):
        return cfg
    with open(p, encoding='utf-8') as f:
        y = _yaml.safe_load(f) or {}
    inner = y.get('xdigest') if isinstance(y.get('xdigest'), dict) else y
    cfg['xdigest'] = {**(cfg.get('xdigest') or {}), **(inner or {})}
    return cfg


def cmd_xdigest_analyze(dry_run=False, asof=None):
    """다이제스트 분석 — `posts.json` → themes · facts · verify (XDIGEST.md 3-2·3-3).

    기준일 디렉터리는 `state/xdigest/YYYYMMDD/` 다(보드 state 와 섞지 않는다).
    분석이 실패해도 0 을 돌려준다 — 3-6 은 그 경우에도 머리 세 줄 + `분석 실패`
    결손으로 다이제스트를 보내라고 하고, 사유는 파일과 로그에 남는다.

    설정은 `config/settings.yaml`(보드 공용) + `config/xdigest.yaml`(다이제스트)
    이다. 후자를 읽지 않으면 `xdigest.scope`·`xdigest.limits` 가 조용히 죽은
    설정이 된다 — 계약 6장 3번이 다이제스트 설정을 그 파일에 두라고 했고
    `analyze.limits()`·`run()` 은 `cfg['xdigest']` 를 본다.
    """
    import json as _json
    from datetime import datetime, timedelta, timezone

    from .xdigest import analyze as XA
    cfg = xdigest_cfg()
    if os.path.exists(XDIGEST_CFG):
        log(f'다이제스트 설정 {XDIGEST_CFG} 를 함께 읽음')
    day = asof or datetime.now(timezone(timedelta(hours=9))).strftime('%Y-%m-%d')
    # 경로는 bsky.state_path 한 곳에서 받는다 — 미리보기(--xdigest-preview)가
    # 자리를 임시 디렉터리로 옮기면 여기도 따라가야 한다.
    from .ingest import bsky as _BS
    src = _BS.state_path(day, 'posts.json')
    d = os.path.dirname(src)
    if not os.path.exists(src):
        log(f'{src} 가 없다. 수집을 먼저 돌려라.')
        return 1
    with open(src, encoding='utf-8') as f:
        posts = _json.load(f)
    log(f'다이제스트 분석 — 기준일 {posts.get("asof") or day}'
        + (' (드라이런)' if dry_run else ''))
    themes, facts, meta = XA.run(posts, cfg, log=log, dry_run=dry_run)
    if dry_run:
        p = os.path.join(d, 'analyze.prompt.txt')
        with open(p, 'w', encoding='utf-8') as f:
            f.write(meta['system'][0]['text'] + '\n\n' + '=' * 70 + '\n'
                    + meta['prompts']['assign'])
            # ⑥ 별도 구획 프롬프트(D-NEXT-Q). ① 출력 없이 만들어지므로 함께 남긴다.
            for k, v in meta['prompts'].items():
                if k != 'assign':
                    f.write('\n\n' + '=' * 70 + f'\n[{k}]\n' + v)
        log(f'  → {p} (① max_tokens {meta["max_tokens"]:,})')
        return 0
    for name, payload in (('themes.json', themes), ('facts.json', facts),
                          ('verify.json', meta.get('verify'))):
        p = os.path.join(d, name)
        with open(p, 'w', encoding='utf-8') as f:
            _json.dump(payload, f, ensure_ascii=False, indent=1)
        log(f'  → {p}')
    if meta.get('error'):
        log(f'  분석 실패 — {meta["error"]} (렌더가 결손 줄에 적는다)')
    return 0


def serve_mod():
    from .web import serve as _serve
    return _serve


def build_parser():
    """CLI 를 조립해 돌려준다. **모드를 더하는 곳은 여기 하나다.**

    `main` 안에 있던 것을 떼어냈다. 이 CLI 가 이 저장소를 부리는 유일한 문이고
    (`.github/workflows/board.yml` 의 mode 들이 전부 이 플래그로 내려온다),
    처음 보는 사람도 `--help` 로 무엇이 있는지 알아야 한다. 떼어내기 전에는
    파서를 만들려면 `main` 을 돌려야 해서 '설명 없는 플래그' 를 시험이 잡을 수
    없었다 — 실제로 아홉 개가 설명 없이 있었다(`tests/test_cli_help.py`).
    """
    ap = argparse.ArgumentParser(prog='board', description='국장 신고가 보드')
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--check', action='store_true',
                   help='자격증명·소스·DB 상태를 고치지 않고 찍는다')
    g.add_argument('--init', action='store_true',
                   help='과거 시세 초기 적재 (구간은 --years, 기본은 상장 이후 전 구간)')
    g.add_argument('--daily', action='store_true',
                   help='하루치 전체 — 인박스→수집→집계→서술→렌더')
    g.add_argument('--engine', action='store_true',
                   help='--daily 에서 수집만 건너뛴다 (이미 받은 DB 로 집계부터)')
    g.add_argument('--render', action='store_true',
                   help='보드를 그려 사이트에 올린다 (수집·집계 없이)')
    g.add_argument('--stockflows', action='store_true',
                   help='52주 이상 신고가 종목의 종목별 수급만 다시 받는다')
    g.add_argument('--search', action='store_true',
                   help='시스템 조합 탐색 — 최근 1년 봉인, 그 앞에서만 고른다 (engine/search.py)')
    g.add_argument('--screen', action='store_true',
                   help='매매 시스템 점검 — 통과 기준을 넘는 시스템만 남긴다 (engine/systems.py)')
    g.add_argument('--backtest', action='store_true',
                   help='스윙 시그널 백테스트 → state/backtest.json (engine/backtest.py)')
    g.add_argument('--signals', action='store_true',
                   help='스윙 시그널(돌파 확인·감시) 계산 → signals.json (docs/SIGNALS.md)')
    g.add_argument('--test', action='store_true',
                   help='tests/test_*.py 를 전부 찾아 돌린다')
    g.add_argument('--demo', action='store_true',
                   help='합성 데이터로 수집부터 렌더까지 — 네트워크 없이 화면 확인')
    g.add_argument('--serve', action='store_true', help='로컬 서버로 사이트를 연다')
    g.add_argument('--write', action='store_true', help='서술 생성만')
    g.add_argument('--classify', action='store_true', help='종목→섹터 배치 분류')
    g.add_argument('--excel', action='store_true', help='랭킹 표를 엑셀로')
    g.add_argument('--financials', action='store_true',
                   help='히트맵 툴팁용 DART 재무(매출·영업이익) 갱신')
    g.add_argument('--news', action='store_true', help='섹터 뉴스 수집')
    g.add_argument('--triggers', action='store_true',
                   help='52주 이상 신고가 종목의 당일 재료(기사·공시·X 포워딩)만 다시 받는다')
    g.add_argument('--x-probe', action='store_true',
                   help='X 자동 수집 경로 실측 (Actions 에서 mode=x-probe)')
    g.add_argument('--bsky-probe', action='store_true',
                   help='블루스카이 계정·주제 물량 실측 (Actions 에서 mode=bsky-probe)')
    g.add_argument('--xdigest-collect', action='store_true',
                   help='X 24시간 다이제스트 수집 (XDIGEST.md 3-1)')
    g.add_argument('--xdigest-preview', action='store_true',
                   help='X 다이제스트 미리보기 — 임시 디렉터리에서 수집·분석·렌더 (표식·state 무관)')
    g.add_argument('--guru', action='store_true',
                   help='글로벌 투자 구루 브리핑 — 웹 검색 조사 → 종합 → brief.txt (docs/GURU.md)')
    g.add_argument('--xdigest-check', action='store_true',
                   help='다이제스트 소스만 점검 — 블루스카이가 열려 있나')
    g.add_argument('--trigger-probe', action='store_true',
                   help='트리거 소스 프로브 — 네이버 질의별 비율·Google News·DART·비공식 URL·웹훅')
    g.add_argument('--send', nargs='?', const='rankings',
                   choices=['rankings', 'draft', 'files', 'note', 'xdigest', 'guru', 'signals', 'backtest',
                            'screen', 'search'],
                   help='텔레그램 발송')
    g.add_argument('--xdigest-render', action='store_true',
                   help='X 다이제스트 렌더 (facts.json → digest.txt. docs/XDIGEST.md 3-4)')
    g.add_argument('--inbox', action='store_true',
                   help='텔레그램 인박스 — 봇에 공유된 X 게시물을 state/inbox.json 으로')
    g.add_argument('--xdigest-analyze', action='store_true',
                   help='다이제스트 분석 — posts.json → themes·facts·verify '
                        '(--dry-run 이면 ① 프롬프트만)')
    g.add_argument('--us-check', action='store_true', help='미국장 소스 점검')
    g.add_argument('--us-init', action='store_true', help='미국장 최초 적재')
    g.add_argument('--us-daily', action='store_true', help='미국장 수집→집계→브리프')
    g.add_argument('--us-build', action='store_true', help='미국장 집계만 다시')
    g.add_argument('--us-brief', action='store_true', help='미국장 브리프만 다시 출력')
    g.add_argument('--us-demo', action='store_true', help='미국장 합성 데이터 데모')
    g.add_argument('--us-search', action='store_true',
                   help='미국장 시스템 조합 탐색 (긴 일봉 수집 → 최근 1년 봉인 → 판정)')
    g.add_argument('--us-verify', action='store_true',
                   help='미국장 DB 상태 점검 (고치지 않고 사실만 찍는다)')
    g.add_argument('--us-send', nargs='?', const='brief', choices=['brief', 'files'],
                   help='미국장 브리프를 텔레그램으로 발송 (files 면 화면 HTML 도 첨부)')
    g.add_argument('--verify-adjust', nargs='*', metavar='CODE',
                   help='수정주가를 KIS·네이버와 교차 검증 (CLAUDE.md 9장 미확정 1번)')
    g.add_argument('--diagnose-banner', nargs='*', metavar='CODE',
                   help='배너에 남은 빨간 줄의 정체를 원자료로 확인')
    g.add_argument('--kis-probe', nargs='*', metavar='MARKET',
                   help='KIS 시장 수급 파라미터 값을 후보 격자로 실측 (기본 0001 1001)')
    g.add_argument('--reclassify', nargs='+', metavar='SECTOR',
                   help='이미 배정된 섹터를 골라 다시 분류 (예: --reclassify 반도체)')
    ap.add_argument('--years', type=int, default=None,
                    help='--init 이 받을 과거 구간(년). 기본은 상장 이후 전 구간')
    ap.add_argument('--no-render', action='store_true',
                    help='--daily / --engine / --us-daily 에서 렌더 생략')
    ap.add_argument('--asof', default=None,
                    help='--us-brief · --xdigest-render · --send xdigest 의 기준일 (YYYY-MM-DD)')
    ap.add_argument('--no-write', action='store_true', help='--daily 에서 서술 생략')
    ap.add_argument('--no-news', action='store_true', help='--daily 에서 뉴스 생략')
    ap.add_argument('--no-inbox', action='store_true',
                    help='--send files 에서 인박스 읽기 생략 (같은 실행의 --daily 가 이미 읽었을 때)')
    ap.add_argument('--only-fresh', action='store_true',
                    help='--send files 에서 보드 기준일이 오늘이 아니면 생략 (예약 실행용)')
    ap.add_argument('--once', action='store_true',
                    help='--send files · --send xdigest 에서 그 기준일을 이미 보냈으면 생략 (재시도용)')
    ap.add_argument('--send-preview', action='store_true',
                    help='--xdigest-preview 결과를 다이제스트 방으로 보냄 (첫 통 머리에 미리보기 표시)')
    ap.add_argument('--dry-run', action='store_true',
                    help='--write / --classify 에서 API 를 부르지 않고 계획만 출력')
    ap.add_argument('--live', action='store_true',
                    help='--classify 에서 Batch 대신 실시간 호출')
    ap.add_argument('--limit', type=int,
                    help='--classify 에서 처리할 종목 수 제한. '
                         '--us-init / --us-daily 에서는 유니버스를 거래대금 상위 N종목으로 자른다')
    ap.add_argument('--port', type=int, default=None, help='--serve 포트 (기본 8787)')
    ap.add_argument('--site', default=None,
                    help='--serve 로 열 사이트 폴더 (기본 docs/. 데모는 docs-demo/)')
    ap.add_argument('--host', default='127.0.0.1',
                    help='--serve 바인딩. 기본은 이 컴퓨터에서만. '
                         '같은 공유기의 다른 기기에도 열려면 0.0.0.0')
    ap.add_argument('--no-open', action='store_true',
                    help='--serve 에서 브라우저를 열지 않는다')
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)

    if a.check:
        return cmd_check()
    if a.init:
        return cmd_init(a.years)
    if a.daily:
        return cmd_daily(skip_render=a.no_render, skip_news=a.no_news,
                         write=False if a.no_write else None)
    if a.engine:
        return cmd_daily(skip_ingest=True, skip_render=a.no_render,
                         skip_news=a.no_news, write=False if a.no_write else None)
    if a.write:
        return cmd_write(dry_run=a.dry_run)
    if a.classify:
        return cmd_classify(dry_run=a.dry_run, live=a.live, limit=a.limit)
    if a.excel:
        return cmd_excel()
    if a.financials:
        return cmd_financials(force=a.live)
    if a.news:
        return cmd_news()
    if a.triggers:
        return cmd_triggers(a.asof)
    if a.x_probe:
        return cmd_x_probe()
    if a.bsky_probe:
        return cmd_bsky_probe()
    if a.guru:
        from .guru import pipeline as GP
        return GP.build(log=log)
    if a.xdigest_check:
        return cmd_xdigest_check()
    if a.xdigest_preview:
        return cmd_xdigest_preview(send=a.send_preview)
    if a.xdigest_collect:
        return cmd_xdigest_collect(a.asof)
    if a.trigger_probe:
        return cmd_trigger_probe(a.asof)
    if a.send:
        return cmd_send(a.send, only_fresh=a.only_fresh, once=a.once, asof=a.asof,
                        inbox=not a.no_inbox)
    if a.xdigest_render:
        return cmd_xdigest_render(a.asof)
    if a.inbox:
        return cmd_inbox()
    if a.xdigest_analyze:
        return cmd_xdigest_analyze(dry_run=a.dry_run, asof=a.asof)
    if a.us_check:
        return cmd_us_check()
    if a.us_init:
        return cmd_us_init(a.years, a.limit)
    if a.us_daily:
        return cmd_us_daily(skip_render=a.no_render, limit=a.limit)
    if a.us_build:
        return cmd_us_daily(skip_ingest=True, skip_render=a.no_render)
    if a.us_brief:
        return cmd_us_brief(a.asof)
    if a.us_verify:
        return cmd_us_verify()
    if a.us_search:
        return cmd_us_search(fetch=not a.dry_run)
    if a.us_demo:
        return cmd_us_demo()
    if a.us_send:
        return cmd_us_send(a.us_send, only_fresh=a.only_fresh)
    if a.stockflows:
        return cmd_stockflows()
    if a.search:
        return cmd_search(fetch=not a.dry_run, live=a.live)
    if a.screen:
        return cmd_screen(fetch=not a.dry_run, live=a.live)
    if a.backtest:
        return cmd_backtest(fetch=not a.dry_run, live=a.live)
    if a.signals:
        # --dry-run 이면 네트워크를 쓰지 않고 이미 받은 파일로만 다시 계산한다.
        return cmd_signals(a.asof, fetch=not a.dry_run)
    if a.render:
        return cmd_render()
    if a.test:
        return cmd_test()
    if a.demo:
        return cmd_demo()
    if a.serve:
        return cmd_serve(a.host, a.port or serve_mod().DEFAULT_PORT,
                         open_browser=not a.no_open, site=a.site)
    if a.verify_adjust is not None:
        return cmd_verify_adjust(a.verify_adjust or default_verify_codes())
    if a.diagnose_banner is not None:
        # 기본값은 D-056 이 보류로 남긴 두 종목이다.
        return cmd_diagnose_banner(a.diagnose_banner or ['007610', '520101'])
    if a.kis_probe is not None:
        return cmd_kis_probe(a.kis_probe or ['0001', '1001'])
    if a.reclassify:
        # 재분류는 대상이 수백 종목 이하라 실시간이 맞다. 배치는 대기가 길다.
        return cmd_reclassify(a.reclassify, dry_run=a.dry_run, live=True)
    return 1


if __name__ == '__main__':
    sys.exit(main())
