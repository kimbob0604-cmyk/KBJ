# MIGRATION — dart-report (KBJ P4 승격)

근거: `docs/p4_design.md` §1.5·§4.6·§8.1, D-P4-5~8, `docs/adr/0014-dart-report-promotion-golden.md`.
P2 의 `dartreport/client.py`(정본 DART 클라이언트 다시 내보내기)는 그대로다 — 기록은 `../MIGRATION.md` 'P2'.

## 1. 순서 (D-P4-6 — P3 board 와 같은 틀)

1. **골든 먼저**: shim 전 커밋(`607b761`)의 이 폴더를 `git archive` 로 풀어 legacy 로 기대 산출을
   캡처했다 — `tests/golden/dart_report/make_golden.py`(G1 빌더 8사례 + G2 합성 세계 2회사 파이프라인).
2. **이식**: 함수 본문 그대로 `kbj/engines/financials/{statements,costs,bridge,orders}.py`(순수 계산 —
   원문·재무제표는 Protocol 인자, `print` 는 `log` 인자)와 `kbj/reports/dart_excel/{excel,pipeline}.py`
   (생성 시각 주입·`run.main` 을 수집/산출로 나눔)로 옮겼다.
3. **비교 녹색**: `uv run pytest tests/golden/dart_report` — 셀 값·서식·시트 구조·차트 XML(정규화),
   DART 호출 순서·로그 줄·`build_workbook` 입력까지 legacy 와 같다. 수식 평가 오류 0(스모크 197개).
4. **shim**: `dartreport/{statements,costs,bridge,orders,excel}.py` 는 kbj 정본 객체를 다시 내보낸다
   (`tests_bridge.py` 가 `is` 로 확인).

## 2. 바뀐 파일

| 파일 | 처리 | 이유 |
|---|---|---|
| `dartreport/statements.py`·`costs.py`·`bridge.py`·`orders.py`·`excel.py` | kbj 다시 내보내기(밑줄 이름 포함 — `run.py` 진단이 쓴다) | 두 벌 금지(CLAUDE.md §3) |
| `config/mapping.yaml` | 레포 `config/dart_report/mapping.yaml` 로 옮김(내용 같음 — 골든 META `mapping_sha256`) + 이 경로는 그 파일을 가리키는 **심볼릭 링크** | 숫자·동의어는 설정 한 곳. `tests_smoke.py` 가 `'config/mapping.yaml'` 을 그대로 읽게(스모크는 고치지 않는다) |
| `run.py` | `--mapping` 기본값만 레포 경로(작업 디렉터리와 무관) | D-P4-6 ④ |
| `app.py` | **삭제** | 페이지 8 '리포트' 탭(같은 입력의 화면 블록 + 엑셀)이 대신. 오류 화면의 키 노출(:106)도 함께 사라진다 |
| `requirements.txt` | `streamlit` 줄 삭제 | app.py 만 썼다(레포 `pyproject.toml` legacy 그룹 정리는 묶음 S) |
| `tests_bridge.py` | 신규(다리 시험 1) | `dartreport.*` 이름 = kbj 객체, 매핑 기본값, app.py 없음 |
| `README.md`·`CLAUDE.md`·`인수인계.md` | app.py·매핑 위치 줄만 | — |

시험: `tests_smoke.py` 출력 `built. quarters= 14 annual= 4 bridge steps= 9` — 이식 전과 같다
(`scripts/test_legacy.sh dart-report`). `tests_bridge.py` 출력 `bridge ok: 5 modules`
(kbj 쪽 `tests/unit/reports/test_legacy_shim.py` 가 두 스크립트를 돈다 — `pytest -m legacy`).

## 3. legacy 동작(버그 포함) — 골든이 고정한 것 (D-P4-8)

고치려면 새 골든 세트 + 새 ADR(이식과 수정을 한 커밋에 섞지 않는다).

| # | 위치 | 동작 | 골든에서 보이는 곳 |
|---|---|---|---|
| 1 | `statements._pick` | 손익 누적값이 0 이면 `or` 로 당기(3개월)값을 쓴다 | 합성 사례 없음(단위 시험이 고정) |
| 2 | `bridge._bucketize_bridge` | `negative_keywords` 의 '손' 이 '당기손익-공정가치측정금융자산평가이익' 같은 **이익**에도 걸려 음수 | G2 994010 FVPL −216.5억(잔차 309억의 큰 몫), G1 c2·c7 |
| 3 | `bridge.build_bridge` | 법인세가 수익(음수)이어도 `-abs` 로 비용 단계 — 워터폴 끝이 순이익과 어긋난다 | G1 c7 |
| 4 | `orders.fetch_orders`·`orders_table` | 정정공시를 거르지 않는다(CLAUDE.md 는 'rcept_no 큰 쪽만'이라 적었다) — 원 계약·정정 두 행, 수주 합계 이중 | G2 994010 253.45억 = 123.45 + 130 |
| 5 | `orders.orders_table` | NIPA 건이 없으면 `nipa_eok` 가 정수 0(표시 차이 없음) | G2 994010 |
| 6 | `costs.cost_timeseries` | 미매핑 행을 보고서(누적 주석)마다 모아 'RAW_미매핑계정' 금액이 누적값의 합(중복 합산 — 금액 의미 없음) | G1 c2·c3·c6·c7·c8 |
| 7 | `statements.fetch_periods` | CFS→OFS 폴백이 보고서마다라 한 해 안에서 기준이 섞이면 차분이 CFS−OFS(경고 없이 fs_div_used 'CFS, OFS' 만) | G1 c2. KBJ `pub_fin.quarterly`(정규화)는 기준별로 따로 환산한다 |
| 8 | `costs.find_periodic_reports` | 보고서 제목의 월(03·06·09·12)로 분기를 정한다 — 12월 결산 가정. 3월 결산 회사의 '사업보고서 (2026.03)' 는 1Q 로 잘못 분류 | 합성 세계 #7(3월 결산)은 리포트 대상이지만 G2 회사가 아니다. KBJ 재무 수집은 `period.report_of_title`(결산월)로 따로 판정 |
| 9 | `excel._sheet_annual_costs` | 차트 앵커 `A{OPM행+3}` 가 주석 세 줄(OPM행+2~+4)과 겹친다 | G1 전 사례(드로잉 XML 앵커) |
| 10 | `excel._sheet_summary` | '생성 시각' 이 벽시계(naive) | KBJ 는 `generated_at` 주입(골든은 자리표시로 정규화) |
| 11 | `run.main` | 주석·브릿지·수주 단계의 **모든** 예외를 경고 줄로 바꾼다 | KBJ 파이프라인은 수집 실패·예산 닫힘(`FetchError`·`BudgetExhausted`)은 올린다 — 의도된 변경(절대 규칙 4) |
