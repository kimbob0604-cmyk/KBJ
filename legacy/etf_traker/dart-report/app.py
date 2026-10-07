"""종목코드만 넣으면 리포트가 나오는 웹 앱.

    export DART_API_KEY='...'
    streamlit run app.py     →  http://localhost:8501
"""

from __future__ import annotations

import io
import os
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from dartreport.bridge import build_bridge, fetch_bridge_details
from dartreport.client import DartClient
from dartreport.costs import (
    BUCKET_ORDER,
    annual_cost_structure,
    cost_timeseries,
    fetch_cost_notes,
    load_mapping,
)
from dartreport.excel import build_workbook
from dartreport.orders import fetch_orders, orders_table
from dartreport.statements import fetch_periods, to_annual, to_quarterly

st.set_page_config(page_title="DART 재무 리포트", page_icon="📊", layout="wide")
st.title("📊 DART 재무 리포트 생성기")

with st.sidebar:
    st.header("설정")
    key_in_env = bool(os.environ.get("DART_API_KEY"))
    if key_in_env:
        st.success("DART_API_KEY 환경변수 감지됨")
        api_key = None
    else:
        api_key = st.text_input(
            "DART 인증키", type="password",
            help="opendart.fss.or.kr 에서 무료 발급. 환경변수 DART_API_KEY 로 두면 매번 입력할 필요 없습니다.",
        )
    stock = st.text_input("종목코드", value="006110", max_chars=6)
    this_year = date.today().year
    y_from, y_to = st.slider("대상 연도", 2015, this_year, (this_year - 3, this_year))
    fs_div = st.radio("재무제표", ["CFS (연결)", "OFS (별도)"], horizontal=True)
    want_orders = st.checkbox("수주 공시 수집", value=True,
                              help="공시 원문을 건건이 받아 느립니다.")
    go = st.button("리포트 생성", type="primary", use_container_width=True)

if not go:
    st.info(
        "왼쪽에서 종목코드를 넣고 **리포트 생성**을 누르세요.\n\n"
        "결과는 6개 블록(연간 비용구조 · 분기 실적 · 이익 브릿지 · 수주 현황 · "
        "분기 비용구조 · 비용 비중)으로 구성된 엑셀 파일로 나옵니다."
    )
    st.stop()

if not (key_in_env or api_key):
    st.error("DART 인증키가 필요합니다.")
    st.stop()

mapping = load_mapping("config/mapping.yaml")
years = list(range(y_from, y_to + 1))
warnings: list[str] = []

try:
    client = DartClient(api_key=api_key or None)
    with st.status("DART 조회 중…", expanded=True) as status:
        st.write("고유번호 확인")
        corp_code, corp_name = client.corp_code(stock)
        st.write(f"**{corp_name}** ({stock})")

        st.write("재무제표 수집")
        periods = fetch_periods(client, corp_code, years, fs_div.split()[0])
        if not periods:
            st.error("재무제표를 받지 못했습니다. 연도 범위나 연결/별도를 바꿔보세요.")
            st.stop()
        quarters, annual = to_quarterly(periods), to_annual(periods)

        st.write("정기보고서 주석 파싱 (비용 성격별 분류)")
        notes = fetch_cost_notes(client, corp_code, years, mapping)
        qcosts, unmapped = cost_timeseries(notes, mapping)
        acosts = annual_cost_structure(notes, mapping)
        if not notes:
            warnings.append("비용 성격별 주석 표를 찾지 못했습니다 (mapping.yaml 보강 필요).")

        st.write("이익 브릿지")
        details = fetch_bridge_details(client, corp_code, years, mapping)
        full = [p for p in periods if p.quarter == "4Q"]
        target = full[-1] if full else periods[-1]
        steps = build_bridge(target, details.get((target.year, target.quarter)))

        if want_orders:
            st.write("수주 공시 수집")
            olist = fetch_orders(client, corp_code, f"{years[0]}0101", f"{this_year}1231")
            orders = orders_table(olist)
        else:
            orders = {"total_eok": 0.0, "nipa_eok": 0.0, "nipa_ratio": None, "rows": []}

        status.update(label="완료", state="complete")
except SystemExit as exc:
    st.error(str(exc))
    st.stop()
except Exception as exc:  # noqa: BLE001
    st.exception(exc)
    st.stop()

# ------------------------------------------------------------------ 화면 표시
qdf = pd.DataFrame(quarters).set_index("label")
c1, c2, c3 = st.columns(3)
latest = quarters[-1]
c1.metric(f"{latest['label']} 매출액", f"{latest.get('revenue') or 0:,.0f} 억")
c2.metric(f"{latest['label']} 영업이익", f"{latest.get('operating_income') or 0:,.0f} 억")
c3.metric(f"{latest['label']} OPM",
          f"{(latest.get('opm') or 0) * 100:,.1f}%" if latest.get("opm") is not None else "-")

tabs = st.tabs(["분기 실적", "비용 구조", "이익 브릿지", "수주 현황", "미매핑 계정"])

with tabs[0]:
    st.bar_chart(qdf[["revenue", "operating_income", "net_income"]])
    st.dataframe(qdf[["revenue", "operating_income", "net_income", "opm"]], use_container_width=True)

with tabs[1]:
    if qcosts:
        cdf = pd.DataFrame(qcosts).set_index("label")[BUCKET_ORDER]
        st.bar_chart(cdf)
        st.dataframe(cdf, use_container_width=True)
    else:
        st.warning("주석에서 비용 구조를 찾지 못했습니다.")

with tabs[2]:
    if steps:
        st.dataframe(pd.DataFrame(steps), use_container_width=True)
    else:
        st.warning("브릿지 구성에 필요한 계정이 부족합니다.")

with tabs[3]:
    if orders["rows"]:
        st.metric("수주 합계", f"{orders['total_eok']:,.0f} 억")
        st.dataframe(pd.DataFrame(orders["rows"]), use_container_width=True)
    else:
        st.info("해당 기간 계약체결 공시가 없거나 수집을 건너뛰었습니다.")

with tabs[4]:
    if unmapped:
        udf = pd.DataFrame(unmapped, columns=["계정명", "금액(원)"]).groupby("계정명").sum()
        st.dataframe(udf.sort_values("금액(원)", key=abs, ascending=False), use_container_width=True)
        st.caption("config/mapping.yaml 의 buckets 에 추가하면 '기타'에서 빠져나옵니다.")
    else:
        st.success("미매핑 계정 없음")

# ------------------------------------------------------------------ 다운로드
Path("out").mkdir(exist_ok=True)
path = f"out/{corp_name}_{stock}_재무리포트.xlsx"
build_workbook(
    path,
    meta={"corp_name": corp_name, "stock_code": stock, "corp_code": corp_code,
          "fs_div_used": fs_div.split()[0], "period_label": f"{years[0]}–{years[-1]}"},
    annual=annual, annual_costs=acosts, quarters=quarters, qcosts=qcosts,
    bridge_steps=steps,
    bridge_label=f"{target.year}년" if target.quarter == "4Q" else f"{target.year}년 {target.quarter} 누적",
    orders=orders, mapping=mapping, unmapped=unmapped, warnings=warnings,
)
st.download_button(
    "📥 엑셀 다운로드", data=io.BytesIO(Path(path).read_bytes()),
    file_name=Path(path).name,
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    type="primary",
)
for w in warnings:
    st.warning(w)
