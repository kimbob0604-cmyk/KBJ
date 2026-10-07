"""엑셀 리포트 빌더 — 업로드해주신 6개 블록을 시트로 재현.

시트 구성
  요약           표지 · 데이터 출처 · 경고
  연간비용구조    이미지 1
  분기실적        이미지 2 (막대 + OPM 보조축 선)
  이익브릿지      이미지 3 (워터폴)
  수주현황        이미지 4
  분기비용구조    이미지 5 + 이미지 6 (표 + 100% 누적 차트)
  RAW_*          원천 데이터 / 미매핑 계정 진단

원칙: 값은 파란 글씨(DART 원천), 계산은 검은 글씨 **수식**. 하드코딩 결과를 넣지 않는다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference, Series
from openpyxl.chart.axis import ChartLines
from openpyxl.chart.label import DataLabelList
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .bridge import waterfall_layout
from .costs import BUCKET_ORDER

FONT = "Arial"
BLUE = "0000FF"      # 원천 입력값
BLACK = "000000"     # 수식
GREEN = "008000"     # 타 시트 참조
HDR_FILL = PatternFill("solid", fgColor="D9E1F2")
TOT_FILL = PatternFill("solid", fgColor="DDEBF7")
OP_FILL = PatternFill("solid", fgColor="E4DFEC")
WARN_FILL = PatternFill("solid", fgColor="FFF2CC")

NUM = '#,##0;[Red]-#,##0;-'
NUM1 = '#,##0.0;[Red]-#,##0.0;-'
PCT = '0%;[Red]-0%;-'
PCT1 = '0.0%;[Red]-0.0%;-'

THIN = Side(style="thin", color="BFBFBF")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def _labels(show=True, num_fmt=None):
    """데이터 레이블 설정. 모든 플래그를 명시적으로 지정합니다.

    openpyxl 은 지정하지 않은 플래그를 None 으로 두는데, 일부 뷰어(LibreOffice 등)는
    None 을 '켜짐'으로 해석해 계열명·항목명이 값 위에 겹쳐 찍힙니다. 그래서 끌 것도
    False 로 못박습니다.
    """
    d = DataLabelList()
    d.showVal = bool(show)
    d.showSerName = False
    d.showCatName = False
    d.showLegendKey = False
    d.showPercent = False
    d.showBubbleSize = False
    if num_fmt:
        d.numFmt = num_fmt
    return d


def _style(cell, *, bold=False, color=BLACK, fill=None, fmt=None, align="right", size=10):
    cell.font = Font(name=FONT, size=size, bold=bold, color=color)
    if fill:
        cell.fill = fill
    if fmt:
        cell.number_format = fmt
    cell.alignment = Alignment(horizontal=align, vertical="center")
    cell.border = BOX
    return cell


def _title(ws, row, text, span=8):
    c = ws.cell(row=row, column=1, value=text)
    c.font = Font(name=FONT, size=13, bold=True)
    c.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[row].height = 22
    return row + 1


def _note(ws, row, text):
    c = ws.cell(row=row, column=1, value=text)
    c.font = Font(name=FONT, size=9, italic=True, color="808080")
    return row + 1


# --------------------------------------------------------------------------- 요약


def _sheet_summary(wb: Workbook, meta: dict[str, Any], warnings: list[str]) -> None:
    ws = wb.create_sheet("요약")
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 70

    r = _title(ws, 1, f"{meta['corp_name']} ({meta['stock_code']}) 재무 리포트")
    r += 1
    for label, value in [
        ("종목코드", meta["stock_code"]),
        ("DART 고유번호", meta["corp_code"]),
        ("연결/별도", meta.get("fs_div_used", "-")),
        ("대상 기간", meta["period_label"]),
        ("생성 시각", datetime.now().strftime("%Y-%m-%d %H:%M")),
        ("데이터 출처", "금융감독원 DART OpenAPI (opendart.fss.or.kr)"),
        ("단위", "억원 (별도 표기 없는 한)"),
    ]:
        _style(ws.cell(row=r, column=1, value=label), bold=True, fill=HDR_FILL, align="left")
        _style(ws.cell(row=r, column=2, value=value), align="left")
        r += 1

    r += 1
    r = _title(ws, r, "읽기 전 확인사항")
    for line in [
        "· 분기 값은 DART 누적 공시를 차분해 산출했습니다 (2Q = 반기누적 − 1Q누적 등).",
        "· 비용 성격별 분해는 정형 API에 없어 정기보고서 주석 표를 파싱한 결과입니다.",
        "  계정명이 회사마다 달라, RAW_미매핑계정 시트를 확인해 config/mapping.yaml 을 보강하세요.",
        "· 이익브릿지의 '기타 영업외손익'은 주석으로 설명되지 않은 잔차입니다.",
        "  이 값이 크면 브릿지 매핑을 손봐야 한다는 신호입니다.",
        "· 파란 글씨 = DART 원천값, 검은 글씨 = 수식.",
    ]:
        c = ws.cell(row=r, column=1, value=line)
        c.font = Font(name=FONT, size=10)
        r += 1

    if warnings:
        r += 1
        r = _title(ws, r, "경고")
        for w in warnings:
            c = ws.cell(row=r, column=1, value=f"⚠ {w}")
            c.font = Font(name=FONT, size=10, color="C00000")
            c.fill = WARN_FILL
            r += 1


# --------------------------------------------------------------------------- 이미지 1


def _sheet_annual_costs(wb: Workbook, annual: list[dict], annual_costs: list[dict]) -> None:
    """연간 비용구조 표 (이미지 1)."""
    ws = wb.create_sheet("연간비용구조")
    ws.column_dimensions["A"].width = 16
    cost_by_label = {c["label"]: c for c in annual_costs}
    cols = [a for a in annual if a["label"] in cost_by_label] or annual

    r = _title(ws, 1, "연간 비용구조 (억원)")
    hdr = r + 1
    _style(ws.cell(row=hdr, column=1, value=""), fill=HDR_FILL)
    for i, a in enumerate(cols):
        ws.column_dimensions[get_column_letter(2 + i)].width = 12
        _style(
            ws.cell(row=hdr, column=2 + i, value=a["label"]),
            bold=True, fill=HDR_FILL, align="center",
        )

    rows_order = ["매출액"] + BUCKET_ORDER
    row_at: dict[str, int] = {}
    rr = hdr + 1
    for name in rows_order:
        row_at[name] = rr
        _style(ws.cell(row=rr, column=1, value=name), bold=(name == "매출액"), align="left")
        for i, a in enumerate(cols):
            if name == "매출액":
                v = a.get("revenue")
            else:
                v = cost_by_label.get(a["label"], {}).get(name)
            cell = ws.cell(row=rr, column=2 + i, value=None if v is None else round(v, 1))
            _style(cell, bold=(name == "매출액"), color=BLUE, fmt=NUM)
        rr += 1

    # 비용합계 = SUM(구성비용)
    first, last = row_at[BUCKET_ORDER[0]], row_at[BUCKET_ORDER[-1]]
    total_row = rr
    _style(ws.cell(row=rr, column=1, value="비용합계"), bold=True, fill=TOT_FILL, align="left")
    for i in range(len(cols)):
        col = get_column_letter(2 + i)
        _style(
            ws.cell(row=rr, column=2 + i, value=f"=SUM({col}{first}:{col}{last})"),
            bold=True, fill=TOT_FILL, fmt=NUM,
        )
    rr += 1

    # 영업이익 = 매출액 - 비용합계
    _style(ws.cell(row=rr, column=1, value="영업이익"), bold=True, fill=OP_FILL, align="left")
    for i in range(len(cols)):
        col = get_column_letter(2 + i)
        _style(
            ws.cell(row=rr, column=2 + i, value=f"={col}{row_at['매출액']}-{col}{total_row}"),
            bold=True, fill=OP_FILL, fmt=NUM,
        )
    rr += 1

    _style(ws.cell(row=rr, column=1, value="OPM"), align="left")
    for i in range(len(cols)):
        col = get_column_letter(2 + i)
        _style(
            ws.cell(row=rr, column=2 + i,
                    value=f"=IFERROR({col}{rr-1}/{col}{row_at['매출액']},\"\")"),
            fmt=PCT1,
        )
    opm_row = rr  # OPM 블록은 rr 을 증가시키지 않고 끝나므로 rr 이 곧 OPM 행입니다.

    # 차트: 연도별 비용 누적 + OPM 보조축
    cats = Reference(ws, min_col=2, max_col=1 + len(cols), min_row=hdr, max_row=hdr)
    ac = BarChart()
    ac.type, ac.grouping, ac.overlap = "col", "stacked", 100
    for name in BUCKET_ORDER:
        pr = row_at[name]
        ac.series.append(Series(
            Reference(ws, min_col=2, max_col=1 + len(cols), min_row=pr, max_row=pr), title=name))
    ac.set_categories(cats)
    ac.y_axis.title = "억원"
    ac.y_axis.majorGridlines = ChartLines()
    ac.dLbls = _labels(show=False)

    al = LineChart()
    al.series.append(Series(
        Reference(ws, min_col=2, max_col=1 + len(cols), min_row=opm_row, max_row=opm_row),
        title="OPM"))
    al.set_categories(cats)
    al.y_axis.axId = 250
    al.y_axis.title = "OPM"
    al.y_axis.numFmt = PCT1
    al.y_axis.majorGridlines = None
    al.y_axis.crosses = "max"
    for s in al.series:
        s.smooth = False
        s.marker.symbol = "circle"
    ac.y_axis.crosses = "autoZero"
    ac += al
    ac.title = "연도별 비용 구조와 마진"
    ac.legend.position = "b"
    ac.legend.overlay = False
    ac.height, ac.width = 10, 20
    ws.add_chart(ac, f"A{rr + 3}")

    rr += 2
    _note(ws, rr, "주: 비용 항목은 정기보고서 주석 '비용의 성격별 분류'에서 파싱. 출처 DART.")
    _note(ws, rr + 1, "주: 영업이익은 매출액−비용합계로 역산한 값이며, 손익계산서 공시 영업이익과 차이가 날 수 있습니다.")
    _note(ws, rr + 2, "주: 마지막 연도가 반기/3분기 누적이면 연간 수치와 직접 비교하지 마세요.")


# --------------------------------------------------------------------------- 이미지 2


def _sheet_quarterly_pl(wb: Workbook, quarters: list[dict]) -> None:
    """분기 실적 표 + 막대/선 복합 차트 (이미지 2)."""
    ws = wb.create_sheet("분기실적")
    ws.column_dimensions["A"].width = 10
    headers = ["분기", "매출액", "영업이익", "당기순이익", "OPM", "순이익률"]
    for i, h in enumerate(headers, start=1):
        ws.column_dimensions[get_column_letter(i)].width = 13
        _style(ws.cell(row=1, column=i, value=h), bold=True, fill=HDR_FILL, align="center")

    for j, q in enumerate(quarters):
        r = 2 + j
        _style(ws.cell(row=r, column=1, value=q["label"]), align="center")
        for k, key in enumerate(["revenue", "operating_income", "net_income"], start=2):
            v = q.get(key)
            _style(ws.cell(row=r, column=k, value=None if v is None else round(v, 1)),
                   color=BLUE, fmt=NUM)
        _style(ws.cell(row=r, column=5, value=f"=IFERROR(C{r}/B{r},\"\")"), fmt=PCT)
        _style(ws.cell(row=r, column=6, value=f"=IFERROR(D{r}/B{r},\"\")"), fmt=PCT)

    last = 1 + len(quarters)
    cats = Reference(ws, min_col=1, min_row=2, max_row=last)

    bar = BarChart()
    bar.type = "col"
    bar.grouping = "clustered"
    bar.add_data(Reference(ws, min_col=2, max_col=4, min_row=1, max_row=last), titles_from_data=True)
    bar.set_categories(cats)
    bar.y_axis.title = "억원"
    bar.y_axis.majorGridlines = ChartLines()
    bar.gapWidth = 60
    # 14분기 x 3계열 = 42개 레이블은 서로 겹칩니다. 값은 바로 옆 표에서 읽으세요.
    bar.dLbls = _labels(show=False)

    line = LineChart()
    line.add_data(Reference(ws, min_col=5, min_row=1, max_row=last), titles_from_data=True)
    line.set_categories(cats)
    line.y_axis.axId = 200
    line.y_axis.title = "OPM"
    line.y_axis.numFmt = PCT
    line.y_axis.majorGridlines = None
    line.y_axis.crosses = "max"   # 보조축을 오른쪽에. 없으면 두 축이 왼쪽에 겹쳐 찍힙니다.
    for s in line.series:
        s.smooth = False
        s.marker.symbol = "circle"

    bar.y_axis.crosses = "autoZero"
    bar += line
    bar.title = "분기 실적 추이"
    bar.legend.position = "b"
    bar.legend.overlay = False
    bar.height, bar.width = 10, 26
    ws.add_chart(bar, f"H2")

    _note(ws, last + 2, "주: 분기 단독값 = 당기 누적 − 직전 분기 누적 (DART 공시는 누적 기준).")


# --------------------------------------------------------------------------- 이미지 3


def _sheet_bridge(wb: Workbook, steps: list[dict], period_label: str) -> None:
    """영업이익 → 당기순이익 워터폴 (이미지 3)."""
    ws = wb.create_sheet("이익브릿지")
    ws.column_dimensions["A"].width = 22
    if not steps:
        _title(ws, 1, "이익브릿지 — 데이터 부족")
        _note(ws, 2, "영업이익/세전이익/순이익 중 일부를 DART에서 가져오지 못했습니다.")
        return

    layout = waterfall_layout(steps)
    r = _title(ws, 1, f"{period_label} 영업이익 → 당기순이익 브릿지 (억원)")
    hdr = r + 1
    for i, h in enumerate(["항목", "값", "받침", "증가", "감소", "합계", "비고"], start=1):
        ws.column_dimensions[get_column_letter(i)].width = 14 if i > 1 else 22
        _style(ws.cell(row=hdr, column=i, value=h), bold=True, fill=HDR_FILL, align="center")

    for j, s in enumerate(layout):
        r = hdr + 1 + j
        _style(ws.cell(row=r, column=1, value=s["name"]), align="left")
        _style(ws.cell(row=r, column=2, value=round(s["value"], 1)), color=BLUE, fmt=NUM1)
        _style(ws.cell(row=r, column=3, value=round(s["base"], 1)), fmt=NUM1)
        _style(ws.cell(row=r, column=4, value=round(s["up"], 1)), fmt=NUM1)
        _style(ws.cell(row=r, column=5, value=round(s["down"], 1)), fmt=NUM1)
        _style(ws.cell(row=r, column=6, value=round(s["total"], 1)), fmt=NUM1)
        note = s.get("note", "")
        c = ws.cell(row=r, column=7, value=note)
        _style(c, align="left", fmt=None)
        if note:
            c.fill = WARN_FILL

    last = hdr + len(layout)
    chart = BarChart()
    chart.type = "col"
    chart.grouping = "stacked"
    chart.overlap = 100
    cats = Reference(ws, min_col=1, min_row=hdr + 1, max_row=last)

    for col, name, hidden in [(3, "받침", True), (6, "합계", False), (4, "증가", False), (5, "감소", False)]:
        ser = Series(Reference(ws, min_col=col, min_row=hdr + 1, max_row=last), title=name)
        if hidden:
            ser.graphicalProperties.noFill = True
            ser.graphicalProperties.line.noFill = True
            # 받침에 레이블이 붙으면 0 과 누적합이 허공에 찍힙니다.
            ser.dLbls = _labels(show=False)
        else:
            # 0 은 빈칸으로. 워터폴은 계열마다 대부분의 값이 0 이라 '-' 가 잔뜩 찍힙니다.
            ser.dLbls = _labels(show=True, num_fmt='#,##0.0;[Red]-#,##0.0;')
        chart.series.append(ser)
    chart.set_categories(cats)
    chart.title = "이익의 질(質) — 영업이익에서 순이익까지"
    chart.y_axis.title = "억원"
    chart.height, chart.width = 10, 24
    chart.legend = None  # 받침/증가/감소는 표현 장치일 뿐이라 범례가 오히려 헷갈립니다.
    ws.add_chart(chart, "I2")

    _note(ws, last + 2, "주: '받침'은 워터폴 표현용 투명 계열입니다. 값 열이 실제 금액입니다.")


# --------------------------------------------------------------------------- 이미지 4


def _sheet_orders(wb: Workbook, table: dict[str, Any]) -> None:
    ws = wb.create_sheet("수주현황")
    r = _title(ws, 1, "수주 현황 (단일판매·공급계약체결 공시 기준)")

    hdrs = ["시작일", "종료일", "계약일수", "일환산매출액(억원)", "고객사", "사업내용",
            "수주액(억원)", "NIPA", "접수번호", "정정"]
    widths = [12, 12, 10, 18, 18, 46, 14, 8, 16, 8]
    rows = table["rows"]

    # 상단 요약 — 표와 항상 일치하도록 전부 수식으로 건다
    sr = r + 1
    for i, label in enumerate(["수주 합계(억원)", "NIPA(억원)", "NIPA 비중(%)"]):
        _style(ws.cell(row=sr, column=1 + i * 2, value=label),
               bold=True, fill=HDR_FILL, align="center")
    data_start = sr + 3
    data_end = data_start + max(len(rows), 1) - 1
    _style(ws.cell(row=sr + 1, column=1,
                   value=f"=SUM(G{data_start}:G{data_end})"), bold=True, fmt=NUM)
    _style(ws.cell(row=sr + 1, column=3,
                   value=f'=SUMIF(H{data_start}:H{data_end},"Y",G{data_start}:G{data_end})'),
           bold=True, fmt=NUM)
    _style(ws.cell(row=sr + 1, column=5, value=f"=IFERROR(C{sr+1}/A{sr+1},\"\")"), fmt=PCT)

    hr = sr + 2
    for i, h in enumerate(hdrs, start=1):
        ws.column_dimensions[get_column_letter(i)].width = widths[i - 1]
        _style(ws.cell(row=hr, column=i, value=h), bold=True, fill=HDR_FILL, align="center")

    if not rows:
        _style(ws.cell(row=data_start, column=1, value="해당 기간 계약체결 공시 없음"), align="left")
        _note(ws, data_start + 2, "주: 거래소공시(pblntf_ty=I) 중 '단일판매·공급계약체결' 건만 집계합니다.")
        return

    for j, row in enumerate(rows):
        rr = data_start + j
        for i, h in enumerate(hdrs, start=1):
            v = row.get(h)
            fmt = NUM1 if "억원" in h else (NUM if h == "계약일수" else None)
            align = "left" if h in ("고객사", "사업내용") else "center"
            if h == "NIPA" and v == "Y":
                _style(ws.cell(row=rr, column=i, value=v), fill=TOT_FILL, align="center")
                continue
            _style(ws.cell(row=rr, column=i, value=v), color=BLUE, fmt=fmt, align=align)

    _note(ws, data_end + 2, "주: 일환산매출액 = 수주액 ÷ 계약일수 × 365. 계약기간에 균등 인식 가정입니다.")
    _note(ws, data_end + 3, "주: 정정공시는 별도 행으로 남습니다 — 원계약과 중복 여부를 확인하세요.")


# --------------------------------------------------------------------------- 이미지 5 + 6


def _sheet_quarterly_costs(wb: Workbook, quarters: list[dict], qcosts: list[dict],
                           mapping: dict[str, Any]) -> None:
    ws = wb.create_sheet("분기비용구조")
    ws.column_dimensions["A"].width = 16
    ws.column_dimensions["B"].width = 10

    rev_by = {q["label"]: q.get("revenue") for q in quarters}
    cols = [c for c in qcosts if c["label"] in rev_by]
    if not cols:
        _title(ws, 1, "분기 비용구조 — 주석 파싱 결과 없음")
        _note(ws, 2, "config/mapping.yaml 의 note_titles / buckets 를 보강한 뒤 재실행하세요.")
        return

    behavior = {}
    for kind, buckets in mapping.get("cost_behavior", {}).items():
        for b in buckets:
            behavior[b] = kind

    r = _title(ws, 1, "분기 비용구조 (억원)")
    hdr = r + 1
    _style(ws.cell(row=hdr, column=1, value="(억원)"), bold=True, fill=HDR_FILL, align="center")
    for i, c in enumerate(cols):
        ws.column_dimensions[get_column_letter(2 + i)].width = 10
        _style(ws.cell(row=hdr, column=2 + i, value=c["label"]),
               bold=True, fill=HDR_FILL, align="center")
    bcol = 2 + len(cols)
    _style(ws.cell(row=hdr, column=bcol, value="구분"), bold=True, fill=HDR_FILL, align="center")
    ws.column_dimensions[get_column_letter(bcol)].width = 10

    rev_row = hdr + 1
    _style(ws.cell(row=rev_row, column=1, value="매출액"), bold=True, align="left")
    for i, c in enumerate(cols):
        v = rev_by.get(c["label"])
        _style(ws.cell(row=rev_row, column=2 + i, value=None if v is None else round(v, 1)),
               bold=True, color=BLUE, fmt=NUM)

    rr = rev_row + 1
    first_cost = rr
    for bucket in BUCKET_ORDER:
        _style(ws.cell(row=rr, column=1, value=bucket), align="left")
        for i, c in enumerate(cols):
            v = c.get(bucket)
            _style(ws.cell(row=rr, column=2 + i, value=None if v is None else round(v, 1)),
                   color=BLUE, fmt=NUM)
        _style(ws.cell(row=rr, column=bcol, value=behavior.get(bucket, "")), align="center")
        rr += 1
    last_cost = rr - 1

    total_row = rr
    _style(ws.cell(row=rr, column=1, value="비용합계"), bold=True, fill=TOT_FILL, align="left")
    for i in range(len(cols)):
        col = get_column_letter(2 + i)
        _style(ws.cell(row=rr, column=2 + i,
                       value=f"=SUM({col}{first_cost}:{col}{last_cost})"),
               bold=True, fill=TOT_FILL, fmt=NUM)
    rr += 1

    op_row = rr
    _style(ws.cell(row=rr, column=1, value="영업이익"), bold=True, fill=OP_FILL, align="left")
    for i in range(len(cols)):
        col = get_column_letter(2 + i)
        _style(ws.cell(row=rr, column=2 + i, value=f"={col}{rev_row}-{col}{total_row}"),
               bold=True, fill=OP_FILL, fmt=NUM1)
    rr += 1

    _style(ws.cell(row=rr, column=1, value="OPM"), align="left")
    for i in range(len(cols)):
        col = get_column_letter(2 + i)
        _style(ws.cell(row=rr, column=2 + i,
                       value=f"=IFERROR({col}{op_row}/{col}{rev_row},\"\")"), fmt=PCT)
    rr += 1

    # 공헌이익률 = (매출 - 변동비) / 매출
    var_rows = [first_cost + BUCKET_ORDER.index(b)
                for b, k in behavior.items() if k == "변동비" and b in BUCKET_ORDER]
    _style(ws.cell(row=rr, column=1, value="공헌이익률"), align="left")
    for i in range(len(cols)):
        col = get_column_letter(2 + i)
        var_sum = "+".join(f"{col}{vr}" for vr in sorted(var_rows)) or "0"
        _style(ws.cell(row=rr, column=2 + i,
                       value=f"=IFERROR(({col}{rev_row}-({var_sum}))/{col}{rev_row},\"\")"),
               fmt=PCT)
    rr += 2

    # ---- 이미지 6: 매출액 대비 비용 비중 (100% 누적용 데이터 블록)
    pct_title = rr
    _title(ws, pct_title, "매출액 대비 비용 비중")
    phdr = pct_title + 1
    _style(ws.cell(row=phdr, column=1, value="(매출액=100%)"), bold=True, fill=HDR_FILL, align="center")
    for i, c in enumerate(cols):
        _style(ws.cell(row=phdr, column=2 + i, value=c["label"]),
               bold=True, fill=HDR_FILL, align="center")

    pfirst = phdr + 1
    for k, bucket in enumerate(BUCKET_ORDER):
        pr = pfirst + k
        _style(ws.cell(row=pr, column=1, value=bucket), align="left")
        for i in range(len(cols)):
            col = get_column_letter(2 + i)
            src = first_cost + k
            _style(ws.cell(row=pr, column=2 + i,
                           value=f"=IFERROR({col}{src}/{col}{rev_row},\"\")"), fmt=PCT)
    plast = pfirst + len(BUCKET_ORDER) - 1

    # 차트 1: 비용 구성 100% 누적
    cats = Reference(ws, min_col=2, max_col=1 + len(cols), min_row=phdr, max_row=phdr)
    stack = BarChart()
    stack.type = "col"
    stack.grouping = "percentStacked"
    stack.overlap = 100
    for k in range(len(BUCKET_ORDER)):
        pr = pfirst + k
        ser = Series(Reference(ws, min_col=2, max_col=1 + len(cols), min_row=pr, max_row=pr),
                     title=BUCKET_ORDER[k])
        stack.series.append(ser)
    stack.set_categories(cats)
    stack.title = "분기별 비용 구성 (매출액 대비)"
    stack.y_axis.numFmt = PCT
    stack.dLbls = _labels(show=False)  # 7계열 x 14분기 = 98개 레이블은 읽을 수 없습니다.
    stack.legend.position = "b"
    stack.legend.overlay = False
    stack.height, stack.width = 10, 26
    ws.add_chart(stack, f"A{plast + 3}")

    _note(ws, plast + 1,
          "주: 변동비/고정비 구분은 config/mapping.yaml 의 cost_behavior 설정값입니다 (회계 기준이 아닌 분석 가정).")


# --------------------------------------------------------------------------- RAW


def _sheet_raw(wb: Workbook, quarters: list[dict], unmapped: list[tuple[str, float]]) -> None:
    ws = wb.create_sheet("RAW_분기재무")
    keys = ["label", "fs_div", "revenue", "cogs", "gross_profit", "sgna", "operating_income",
            "finance_income", "finance_cost", "other_income", "other_expense",
            "pretax_income", "tax", "net_income", "total_assets", "total_equity", "cash"]
    for i, k in enumerate(keys, start=1):
        ws.column_dimensions[get_column_letter(i)].width = 15
        _style(ws.cell(row=1, column=i, value=k), bold=True, fill=HDR_FILL, align="center")
    for j, q in enumerate(quarters):
        for i, k in enumerate(keys, start=1):
            v = q.get(k)
            _style(ws.cell(row=2 + j, column=i,
                           value=round(v, 2) if isinstance(v, (int, float)) else v),
                   color=BLUE, fmt=NUM1 if isinstance(v, (int, float)) else None,
                   align="center" if i <= 2 else "right")

    ws2 = wb.create_sheet("RAW_미매핑계정")
    ws2.column_dimensions["A"].width = 46
    ws2.column_dimensions["B"].width = 20
    _style(ws2.cell(row=1, column=1, value="미매핑 계정명"), bold=True, fill=HDR_FILL, align="center")
    _style(ws2.cell(row=1, column=2, value="금액(원)"), bold=True, fill=HDR_FILL, align="center")
    agg: dict[str, float] = {}
    for label, value in unmapped:
        agg[label] = agg.get(label, 0.0) + value
    for j, (label, value) in enumerate(sorted(agg.items(), key=lambda kv: -abs(kv[1]))):
        _style(ws2.cell(row=2 + j, column=1, value=label), align="left")
        _style(ws2.cell(row=2 + j, column=2, value=value), fmt=NUM)
    if not agg:
        _style(ws2.cell(row=2, column=1, value="없음 — 모든 계정이 매핑되었습니다."), align="left")
    else:
        _note(ws2, len(agg) + 3,
              "이 계정들은 전부 '기타' 버킷으로 흘러갔습니다. config/mapping.yaml 에 추가하세요.")


# --------------------------------------------------------------------------- 진입점


def build_workbook(path: str, *, meta: dict, annual: list[dict], annual_costs: list[dict],
                   quarters: list[dict], qcosts: list[dict], bridge_steps: list[dict],
                   bridge_label: str, orders: dict, mapping: dict,
                   unmapped: list[tuple[str, float]], warnings: list[str]) -> str:
    wb = Workbook()
    wb.remove(wb.active)

    _sheet_summary(wb, meta, warnings)
    _sheet_annual_costs(wb, annual, annual_costs)
    _sheet_quarterly_pl(wb, quarters)
    _sheet_bridge(wb, bridge_steps, bridge_label)
    _sheet_orders(wb, orders)
    _sheet_quarterly_costs(wb, quarters, qcosts, mapping)
    _sheet_raw(wb, quarters, unmapped)

    for ws in wb.worksheets:
        ws.sheet_view.showGridLines = False
    wb.save(path)
    return path
