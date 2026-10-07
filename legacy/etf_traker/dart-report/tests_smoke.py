import sys, random, os
sys.path.insert(0,'.')
# KBJ P1: KBJ .gitignore 가 legacy/**/out/ 을 무시해 새 클론에는 out/ 이 없다. 출력 폴더만 만든다.
os.makedirs('out', exist_ok=True)
from dartreport.excel import build_workbook
from dartreport.costs import load_mapping, BUCKET_ORDER
from dartreport.statements import Period, to_quarterly, to_annual, EOK
from dartreport.bridge import build_bridge

random.seed(7)
mapping = load_mapping('config/mapping.yaml')

# --- 합성 Period (2023~2026 2Q) : 누적 기준 원 단위
labels=[(y,q) for y in range(2023,2027) for q in ['1Q','2Q','3Q','4Q']]
labels=[t for t in labels if not (t[0]==2026 and t[1] in ('3Q','4Q'))]
periods=[]
for y,q in labels:
    n={'1Q':1,'2Q':2,'3Q':3,'4Q':4}[q]
    base=300*n
    p=Period(year=y,quarter=q,fs_div='CFS')
    p.cumulative={
      'revenue':base*EOK,'operating_income':(base*0.05)*EOK,
      'pretax_income':(base*0.07)*EOK,'tax':(base*0.015)*EOK,
      'net_income':(base*0.055)*EOK,'cogs':(base*0.7)*EOK,
    }
    p.balance={'total_assets':(2000)*EOK,'total_equity':1200*EOK,'cash':300*EOK}
    periods.append(p)

quarters=to_quarterly(periods); annual=to_annual(periods)

# --- 합성 비용구조
def costs(label, scale):
    r={'label':label}
    vals=[scale*x for x in (0.62,0.12,0.01,0.09,0.04,0.03,0.03)]
    for b,v in zip(BUCKET_ORDER, vals): r[b]=round(v,1)
    return r
qcosts=[costs(q['label'], (q.get('revenue') or 0)*0.97) for q in quarters]
acosts=[costs(a['label'], (a.get('revenue') or 0)*0.97) for a in annual]

steps=build_bridge(periods[-5], {'FVPL 평가이익':59.3*EOK,'순이자손익':-4.6*EOK,
                                 '순외환손익':-2.3*EOK,'배당·기타':-1.7*EOK})
from datetime import date as _d
from dartreport.orders import Order, orders_table
orders = orders_table([
 Order(rcept_no='20250820000123', rcept_dt='20250820', report_nm='단일판매·공급계약체결',
       amount=356*EOK, counterparty='카카오', content='NVIDIA Infiniband 네트워크',
       start=_d(2025,8,20), end=_d(2030,12,31)),
 Order(rcept_no='20260703000456', rcept_dt='20260703', report_nm='단일판매·공급계약체결',
       amount=1040*EOK, counterparty='네이버클라우드', content='2026 NIPA 고성능 AI 스토리지',
       start=_d(2026,7,3), end=_d(2027,3,31)),
])

build_workbook('out/_smoke.xlsx',
  meta={'corp_name':'테스트','stock_code':'000000','corp_code':'00000000',
        'fs_div_used':'CFS','period_label':'2023–2026'},
  annual=annual, annual_costs=acosts, quarters=quarters, qcosts=qcosts,
  bridge_steps=steps, bridge_label='2025년', orders=orders, mapping=mapping,
  unmapped=[('알수없는수수료', 1.2e8),('잡비',3.4e7)],
  warnings=['스모크 테스트 실행'])
print('built. quarters=',len(quarters),'annual=',len(annual),'bridge steps=',len(steps))
