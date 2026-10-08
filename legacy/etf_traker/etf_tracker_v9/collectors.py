"""국내 ETF 구성종목(PDF) 수집기 — KBJ 정본 어댑터(`kbj.data.private.etf_issuers`)를 쓰는 shim.

KBJ P3 묶음 E3(docs/p3_design.md §1.5·D-P3-12): 운용사 어댑터 9곳(KODEX·TIGER·TIMEFOLIO·SOL 은 이
파일, ACE·HANARO·KoAct·PLUS·RISE 는 adapters/)의 파싱·HTTP 는 kbj 로 옮겼다(두 벌 금지). 여기는 옛
인터페이스만 맞춘다 — tracker.py 가 그대로 쓴다(verify.py 는 P3 묶음 S 가 지웠다).
  universe() -> [(fund_key, ticker, name), ...]
  holdings(fund_key, 'YYYY-MM-DD') -> ({종목코드: {name, qty, wt, val}}, 실제기준일 'YYYY-MM-DD')
HTTP 는 kbj `IssuerHttp.local()`(호스트별 리미터 config/limits.yaml `etf_issuers`, 시간 제한·크기
상한·일시 오류 재시도). 실패는 예외로 올라간다(tracker.snapshot 이 재시도·기록).

네이버 TOP10 폴백(`NaverTop10`)은 KBJ P3 묶음 S 가 지웠다(ADR 0001 U4 — 네이버 스크래핑 금지,
docs/p3_design.md §1.10·D-P3-12). 전용 어댑터가 없는 운용사의 구성종목은 kbj 에서도 비어 있다(KIS
FHKST121600C0 후보 — [실측 필요]).
"""
import re
from datetime import date as _date


from kbj.data.private.etf_issuers import base as _kbase
from kbj.data.private.etf_issuers.base import IssuerHttp
from kbj.data.private.etf_issuers.kodex import Kodex as _KKodex
from kbj.data.private.etf_issuers.sol import Sol as _KSol
from kbj.data.private.etf_issuers.tiger import Tiger as _KTiger
from kbj.data.private.etf_issuers.timefolio import TimeFolio as _KTimeFolio

UA = _kbase.UA
TIMEOUT = 30
KRCODE = _kbase.KRCODE     # 국내 종목코드 (신규 코드에 영문 포함: 0185L0 등)
isin_to_code = _kbase.isin_to_code


def _f(v):
    return _kbase.parse_num(v)


def _is_kr(code):
    """국내 상장 종목코드인지. 'ABT US EQUITY' 같은 해외 티커와 현금성 자산(KRD…) 배제"""
    return _kbase.is_kr_code(code)


def _ymd(d):
    v = _kbase.ymd(d)
    return v.isoformat() if v else None


def _rows_from_table(html, col_code=0, col_name=1, col_qty=2, col_val=3, col_wt=4):
    rows = _kbase.rows_from_table(html, 'legacy', col_code, col_name, col_qty, col_val, col_wt)
    return _plain(rows)


def _plain(rows):
    return {c: {'name': h.name, 'qty': h.qty, 'wt': h.wt, 'val': h.val} for c, h in rows.items()}


_HTTP = None


def _http():
    global _HTTP
    if _HTTP is None:
        _HTTP = IssuerHttp.local()
    return _HTTP


class LegacyAdapter:
    """kbj 어댑터 → 옛 인터페이스. 하위 클래스는 KBJ 에 kbj 어댑터 클래스를 둔다."""
    KBJ = None

    def __init_subclass__(cls, **kw):
        super().__init_subclass__(**kw)
        k = cls.KBJ
        if k is not None:
            cls.KEY, cls.NAME, cls.DEPTH, cls.HISTORY = k.KEY, k.NAME, k.DEPTH, k.HISTORY

    def __init__(self):
        self.a = self.KBJ(_http())

    def universe(self):
        return [(f.fund_key, f.ticker, f.name) for f in self.a.universe()]

    def holdings(self, fund_key, date):
        rows, real = self.a.holdings(fund_key, _date.fromisoformat(_ymd(date) or date))
        return _plain(rows), real.isoformat()


class Kodex(LegacyAdapter):
    KBJ = _KKodex


class Tiger(LegacyAdapter):
    KBJ = _KTiger


class TimeFolio(LegacyAdapter):
    KBJ = _KTimeFolio


class Sol(LegacyAdapter):
    KBJ = _KSol


def _unesc(s):
    return _kbase.unescape_basic(s)


_BUILTIN = (Kodex, Tiger, TimeFolio, Sol)
ADAPTERS = {c.KEY: c for c in _BUILTIN}


def _load_plugins():
    """adapters/ 폴더의 어댑터를 자동 등록한다(adapters/*.py 도 kbj 어댑터 shim)."""
    import importlib, pkgutil, os
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'adapters')
    if not os.path.isdir(d):
        return
    import sys
    sys.path.insert(0, os.path.dirname(d))
    for m in pkgutil.iter_modules([d]):
        if m.name.startswith('_'):
            continue
        try:
            mod = importlib.import_module(f'adapters.{m.name}')
        except Exception as e:
            print(f'[collectors] adapters/{m.name}.py 로드 실패: {type(e).__name__}: {e}')
            continue
        for obj in vars(mod).values():
            if (isinstance(obj, type) and hasattr(obj, 'KEY') and hasattr(obj, 'holdings')
                    and getattr(obj, 'KEY', None) and obj.__module__ == mod.__name__):
                ADAPTERS[obj.KEY] = obj


_load_plugins()
