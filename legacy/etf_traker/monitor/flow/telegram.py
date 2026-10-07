#!/usr/bin/env python3
"""
수급 리포트 → 텔레그램.

board/report/telegram.py 는 `sendMessage` 와 `sendDocument` 만 있다. 차트는
**첨부가 아니라 사진으로** 보내야 대화창에서 바로 보인다 — 첨부로 보내면 받은
사람이 파일을 눌러 열어야 한다.

그래서 `sendMediaGroup` 으로 4장을 한 묶음으로 보내고, 숫자와 문장은 이어서
`sendMessage` 로 보낸다. 캡션은 1,024자 제한이라 본문을 캡션에 우겨넣지 않는다.

토큰·채팅방 조달과 오류 메시지의 키 마스킹은 board 쪽을 그대로 쓴다.

KBJ P2 (설계 §5.9·§5.10 #39): 텔레그램을 직접 부르지 않는다. 사진 묶음은 KBJ notifier 대기열
(`kbj.services.notifier.client.legacy_send_media`, 종류 `flows.report`)에 넣고, 10장씩 묶기·캡션
상한·재시도는 notifier 가 한다. 봇 토큰·chat id 는 읽지 않는다(token·chat_id 인자는 무시).
"""
from __future__ import annotations

import os
import sys

from kbj.services.notifier.client import legacy_send_media

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from board.report import telegram as TG  # noqa: E402

TIMEOUT = 60

# 한 묶음에 넣을 수 있는 사진 수. 텔레그램 제한이다(묶기는 notifier 가 한다 — 옛 값).
MEDIA_GROUP_MAX = 10


def send_photos(paths, caption='', token=None, chat_id=None, silent=False):
    """사진 여러 장을 한 묶음으로. 반환 (ok, 사유).

    캡션은 **첫 장에만** 붙는다(텔레그램 규약). 없는 파일은 조용히 건너뛰지
    않는다 — 못 보낸 것을 보냈다고 적으면 받은 사람이 찾다가 끝난다.
    """
    TG._ignored(token, chat_id)
    paths = [p for p in (paths or [])]
    missing = [p for p in paths if not os.path.exists(p)]
    if missing:
        return False, f'보낼 그림이 없다: {", ".join(os.path.basename(p) for p in missing)}'
    if not paths:
        return False, '보낼 그림이 없다'
    ok, why = legacy_send_media(paths, caption or '', source='et.flow', kind='flows.report',
                                silent=bool(silent))
    return (True, f'그림 {len(paths)}장') if ok else (False, why)


def send_report(rep, charts, text, token=None, chat_id=None, silent=False):
    """차트 + 본문. 그림이 실패하면 본문도 보내지 않는다.

    그림 없이 숫자만 가면 '차트 캡쳐를 보냈다' 는 약속이 반쯤 깨진 채로 도착한다.
    차라리 통째로 실패하고 로그에 남기는 편이 낫다.
    """
    head = f"{rep['name']} {rep['code']} · 수급 {len(rep['dates'])}거래일"
    ok, why = send_photos(charts, caption=head, token=token, chat_id=chat_id,
                          silent=silent)
    if not ok:
        return False, why
    ok2, why2 = TG.send(text, token=token, chat_id=chat_id, silent=silent, kind='flows.report')
    if not ok2:
        return False, f'그림은 갔으나 본문 실패: {why2}'
    return True, f'{why} · {why2}'


def send_text(text, token=None, chat_id=None, silent=False):
    """짧은 알림 한 줄. 그림 없이 본문만 보낸다.

    '오늘은 대상이 없었다' 처럼 리포트가 아닌 사실을 알릴 때 쓴다. 아무것도
    안 보내면 '대상이 없었다' 와 '실행이 안 됐다' 가 받는 쪽에서 같아 보인다.
    """
    return TG.send(text, token=token, chat_id=chat_id, silent=silent)


# 본문 하단에 출처·한계 문구를 붙일 것인가.
#
# 사용자가 받아 보고 **빼 달라고 했다** — 본인 대화방이고 매번 같은 네 줄이
# 붙으면 읽는 데 방해가 된다. 그래서 끈다.
#
# 사실이 사라지는 것은 아니다. 어느 경로로 만들었는지(`rep['source']`)와
# 기간합계 대조를 했는지(`rep['verified']`)는 리포트 데이터에 그대로 있고
# 실행 로그가 매번 찍는다. 한계 문구 원문은 analyze.LIMITS 와 README 에 있다.
# 이 리포트를 다른 사람에게도 보내게 되면 여기를 True 로 돌린다.
FOOTER = False


def won(eok):
    """억원 값을 사람이 읽는 단위로. 1조부터는 조로 적는다."""
    if eok is None:
        return None
    if abs(eok) >= 10_000:
        return f'{eok / 10_000:,.2f}조'
    return f'{eok:,.0f}억'


def pick_lines(pick):
    """왜 이 종목이 뽑혔는지, 그리고 그 거래가 덩치에 비해 얼마나 큰지.

    다섯 종목을 나란히 받으면 '왜 얘냐' 가 먼저 떠오른다. 근거가 리포트 밖에
    있으면 확인할 길이 없어 본문 머리에 적는다.

    **시총 대비 거래대금**을 같이 적는 이유는, 같은 1,000억이라도 시총 3천억
    종목에서는 손이 통째로 바뀐 것이고 10조 종목에서는 평상시이기 때문이다.
    거래대금이 종가x거래량 추정치면 그렇게 적는다 — 실측과 섞지 않는다.
    """
    if not pick.get('label'):
        return []
    # 어느 기준으로 선 신고가인지 반드시 적는다. 보드 기본은 **고가** 기준이라
    # 장중에 뚫고 종가는 밀린 날도 신고가다 — 기준을 안 적으면 '일간 수익률이
    # 마이너스인데 왜 신고가냐' 가 된다.
    head = f"{pick['label']} 신고가"
    if pick.get('basis'):
        head += f"({pick['basis']} 기준)"
    if pick.get('vol_mult') is not None:
        head += f" · 거래량 20일 평균의 {pick['vol_mult']}배"
    out = [head]

    cap, turn = pick.get('mktcap'), pick.get('turnover')
    bits = []
    if cap:
        bits.append(f'시총 {won(cap)}')
    if turn is not None:
        t = f'거래대금 {won(turn)}'
        if pick.get('turnover_est'):
            t += '(추정)'
        if cap:
            t += f' = 시총의 {turn / cap * 100:,.1f}%'
        bits.append(t)
    if bits:
        out.append(' · '.join(bits))
    return out


RET_LABEL = (('daily', '일간'), ('high', '장중 고가'),
             ('mom', '1개월'), ('ytd', '연초 대비'))


def return_line(ret):
    """수익률 한 줄. 못 잰 항목은 적지 않는다.

    기준일을 괄호로 함께 적는다 — '1개월 +18.4%' 만 적으면 어느 날과 견줬는지
    확인할 방법이 없다. 연초 대비의 기준은 작년 마지막 거래일이다.
    """
    bits = []
    for key, label in RET_LABEL:
        r = ret.get(key) or {}
        if r.get('pct') is None:
            continue
        # 연초 대비의 기준은 작년이다. 연도를 빼고 '12.30' 만 적으면 올해
        # 12월로 읽힌다 — 이 항목만 두 자리 연도를 붙인다.
        raw = r.get('base') or ''
        base = (raw[2:] if key == 'ytd' else raw[5:]).replace('-', '.')
        bits.append(f"{label} {r['pct']:+,.1f}%" + (f'({base})' if base else ''))
    return '수익률 ' + ' · '.join(bits) if bits else ''


def asof_gap(rep):
    """신고가 판정일과 이 리포트의 마지막 거래일이 다르면 말한다.

    신고가는 보드가 어제 저녁에 판정하고, 수급은 KIS 가 아직 오늘치를 안 준
    상태일 수 있다. 그러면 '52주 신고가' 와 '일간 수익률' 이 서로 다른 날의
    사실이 되는데, 아무 말이 없으면 같은 날로 읽힌다.
    """
    board = str((rep.get('pick') or {}).get('asof') or '').replace('-', '')
    last = str((rep.get('dates') or [''])[-1])
    if not board or board == '?' or not last or board == last:
        return ''
    def md(x):
        return f'{x[4:6]}.{x[6:8]}'
    return f'기준일이 다르다 — 신고가 {md(board)} · 수급·가격 {md(last)}'


def compose(rep, lines, numbers):
    """본문. 텔레그램은 Markdown 이라 표를 코드블록으로 감싼다."""
    out = []
    from .narrative import period_label
    # '한 달' 을 박아 두면 60거래일 리포트도 한 달이라고 적힌다. 실제로 받은
    # 거래일 수에서 만든다 (period_label).
    out.append(f"*{rep['name']}* `{rep['code']}` — 최근 {period_label(rep)} 수급")
    out.append(f"{rep['dates'][0][4:6]}.{rep['dates'][0][6:8]}"
              f"~{rep['dates'][-1][4:6]}.{rep['dates'][-1][6:8]} "
              f"{len(rep['dates'])}거래일 · 가격 기준 "
              f"{rep['baseDate'][4:6]}.{rep['baseDate'][6:8]} 종가=100")
    for l in pick_lines(rep.get('pick') or {}):
        out.append(f'_{l}_')
    ret = return_line(rep.get('returns') or {})
    if ret:
        out.append(f'_{ret}_')
    gap = asof_gap(rep)
    if gap:
        out.append(f'_{gap}_')
    out.append('')
    out.extend(f'· {l}' for l in lines)
    out.append('')
    out.append('```')
    out.append(numbers)
    out.append('```')
    if FOOTER:
        out.append('')
        from .analyze import LIMITS, UNVERIFIED
        out.append(f"_출처: {rep.get('source', '?')}_")
        if not rep.get('verified', True):
            out.append(f'_{UNVERIFIED}_')
        out.extend(f'_{l}_' for l in LIMITS)
    return '\n'.join(out)
