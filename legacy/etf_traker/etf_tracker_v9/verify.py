"""데이터 품질 자체 검증 — python tracker.py --verify 로 호출된다.\n\n10개 항목을 실측으로 확인한다. 어댑터를 추가하거나 사이트 구조가 바뀐 뒤 반드시 돌려볼 것.\n통과 기준은 모두 실측 근거가 있다 — 자세한 사유는 각 항목 주석 참고.\n"""
import os, sys, sqlite3, requests, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import themes as TH
from collectors import ADAPTERS
c=sqlite3.connect(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'etf.db'))
UA={'User-Agent':'Mozilla/5.0','Referer':'https://finance.naver.com/sise/etf.naver'}
PASS=[]; FAIL=[]
def chk(name, ok, detail):
    (PASS if ok else FAIL).append((name,detail)); print(f'  {"PASS" if ok else "FAIL"}  {name:<34} {detail}')

print('=== 최종 검증 ===\n[1] 유니버스 커버리지')
nv=requests.get('https://finance.naver.com/api/sise/etfItemList.nhn',headers=UA,timeout=30).json()['result']['etfItemList']
dom={x['itemcode']:x['itemname'] for x in nv if x['etfTabCode'] in (1,2)}
tracked={r[0] for r in c.execute('SELECT ticker FROM fund WHERE track=1')}
dropped={r[0]:r[1] for r in c.execute('SELECT ticker,name FROM fund WHERE track=0')}
excl=[v for k,v in dom.items() if TH.classify(v) is None]
acct=len(dom)-len(excl)-len(dropped)
chk('추적대상 = 국내ETF - 파생 - 해외오분류', len(tracked)==acct,
    f'{len(dom)} - 파생{len(excl)} - 해외{len(dropped)} = {acct} / 추적 {len(tracked)}')

print('\n[2] 수집 성공률')
asof=c.execute('SELECT MAX(asof) FROM holding').fetchone()[0]
miss=[r[0] for r in c.execute("""SELECT f.name FROM fund f WHERE f.track=1 AND NOT EXISTS
      (SELECT 1 FROM holding h WHERE h.fund_id=f.fund_id)""")]
chk('전 추적 ETF 데이터 보유', not miss, f'{len(tracked)-len(miss)}/{len(tracked)}' + (f' 누락:{miss[:3]}' if miss else ''))

print('\n[3] 테마 분류')
etc=[r[0] for r in c.execute("SELECT name FROM fund WHERE track=1 AND theme IN ('기타','멀티전략')")]
nth=[r[0] for r in c.execute("SELECT name FROM fund WHERE track=1 AND (theme IS NULL OR theme='')")]
chk('테마 미할당 0건', not nth, f'{len(tracked)}개 전부 테마 보유')
chk('섹터테마 확정률', len(etc)/max(len(tracked),1)<0.02, f'멀티전략(비섹터) {len(etc)}개 = {len(etc)/len(tracked)*100:.1f}%')

print('\n[4] 데이터 품질 — 비주식 혼입')
stocks=set()
for mkt in ['KOSPI','KOSDAQ']:
    for pg in range(1,45):
        try:
            j=requests.get(f'https://m.stock.naver.com/api/stocks/marketValue/{mkt}?page={pg}&pageSize=100',
                headers={'User-Agent':'Mozilla/5.0 (iPhone)','Referer':'https://m.stock.naver.com/'},timeout=15).json()
            it=j.get('stocks') or []
            if not it: break
            stocks|={x['itemCode'] for x in it}
        except Exception: break
etfs={r[0] for r in c.execute('SELECT code FROM etf_ticker')}
allcodes={r[0] for r in c.execute('SELECT DISTINCT code FROM holding')}
bad=allcodes-stocks-etfs
chk('비주식 코드 0건', not bad, f'고유코드 {len(allcodes):,}개 중 미확인 {len(bad)}건' + (f' {sorted(bad)[:5]}' if bad else ''))

print('\n[5] 비중 합계 — 데이터 유실 탐지')
# 어댑터는 현금·선물·옵션을 의도적으로 뺀다. ETF 에 따라 그 비중이 10%를 넘기도 한다
# (SOL 200TR = 선물 5.2 + 현금 5.1). 따라서 100 미만 자체는 정상이고,
# 페이지네이션 절단·단위 오류로 '데이터가 유실된' 경우만 잡아야 한다.
bad_wt=[]; n_chk=0
for fid,nm,depth in c.execute("SELECT fund_id,name,depth FROM fund WHERE track=1 AND depth='full'"):
    r=c.execute('SELECT SUM(wt) FROM holding WHERE fund_id=? AND asof=?',(fid,asof)).fetchone()[0]
    if not r: continue
    n_chk+=1
    if not (85<=r<=101.5): bad_wt.append((nm,round(r,2)))
chk('전체종목 소스 비중합 85~101.5%', not bad_wt,
    f'{n_chk}개 검사, 이탈 {len(bad_wt)}건' + (f' {bad_wt[:4]}' if bad_wt else ''))

# 단위 오류(0.33 vs 33) 탐지 — 합이 5 미만이면 소수 표기를 놓친 것
tiny=[(r[0],round(r[1],3)) for r in c.execute(
    """SELECT f.name, SUM(h.wt) s FROM fund f JOIN holding h USING(fund_id)
       WHERE f.track=1 AND h.asof=? GROUP BY f.fund_id HAVING s>0 AND s<5""",(asof,))]
chk('비중 단위 오류 0건', not tiny, f'합계 5%% 미만 {len(tiny)}건' + (f' {tiny[:3]}' if tiny else ''))

# 종목수 급감 탐지 — 같은 이름 계열에서 혼자만 10분의 1이면 페이지네이션 절단
cnt={r[0]:r[1] for r in c.execute(
    """SELECT f.name, COUNT(*) FROM fund f JOIN holding h USING(fund_id)
       WHERE f.track=1 AND f.depth='full' AND h.asof=? GROUP BY f.fund_id""",(asof,))}
idx200={k:v for k,v in cnt.items() if k.endswith(' 200') or k.endswith('200TR')}
trunc=[(k,v) for k,v in idx200.items() if v<150]
chk('200 추종형 종목수 정상', not trunc, f'{len(idx200)}개 중 절단 {len(trunc)}건' + (f' {trunc}' if trunc else ''))

print('\n[6] 소스간 교차검증 (KOSPI200)')
sets={}
for nm,fid in c.execute("SELECT name,fund_id FROM fund WHERE track=1 AND depth='full' AND name LIKE '% 200'"):
    s={r[0] for r in c.execute('SELECT code FROM holding WHERE fund_id=? AND asof=?',(fid,asof))}
    if len(s)>150: sets[nm]=s
    
if len(sets)>=3:
    base=max(sets.values(),key=len)
    worst=min(len(base&s)/max(len(base),len(s)) for s in sets.values())
    chk('200 추종 ETF 구성종목 일치', worst>=0.97, f'{len(sets)}개 소스, 최저 일치율 {worst*100:.1f}%')

print('\n[7] 우선주 코드 정규화')
pref={r[0]:r[1] for r in c.execute("SELECT code,name FROM holding WHERE name LIKE '%우' OR name LIKE '%우B'")}
badp=[(k,v) for k,v in pref.items() if k not in stocks]
chk('우선주 코드 유효', not badp, f'{len(pref)}종 확인' + (f' 오류:{badp[:3]}' if badp else ''))

print(f'\n=== 결과: {len(PASS)}개 통과 / {len(FAIL)}개 실패 ===')
for n,d in FAIL: print(f'  ! {n}: {d}')

sys.exit(1 if FAIL else 0)
