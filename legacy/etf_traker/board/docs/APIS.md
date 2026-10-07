# 필요한 API

> **현재 상태 (자격증명 확보 후)**
>
> | | 상태 |
> |---|---|
> | KRX 오픈API | 키 있음 · `ingest/krx.py` · 실호출 미검증 |
> | 한국투자증권 KIS | 키 있음 · `ingest/kis.py` · 실호출 미검증 |
> | DART OpenAPI | 키 있음 · `ingest/dart.py` · 실호출 미검증 |
> | 네이버 검색 API | **시크릿 없음** — ID 만 있음. 뉴스 불가 |
> | Anthropic API | **키 없음** — 서술 불가 |
> | 텔레그램 / 노션 | 미설정 |
>
> `python3 -m board.run --check` 가 전부 실제로 찔러 보고 결과를 찍는다.

신고가 보드를 굴리는 데 필요한 외부 데이터와, 각각을 어디서 받는지.
**A는 지금 코드가 이미 요구하는 것**, B는 대시보드의 빈칸을 채우는 것, C는 18시 리포트(T4)까지 갈 때 필요한 것이다.

> 이 환경은 외부 접속이 막혀 있어 아래 엔드포인트를 **실호출로 검증하지 못했다.**
> 코드는 다 붙여 놨으니 `python3 -m board.run --check` 를 먼저 돌려 응답 형태를 확인하고 써라.
> 응답 키 이름이 다르면 `ingest/http.py` 의 `pick()` 후보에 키를 추가하면 된다.

---

## A. 필수 — 지금 코드가 요구하는 것

### A1. 전 종목 일별 시세 (시·고·저·**종가**·거래량·거래대금·시가총액)

신고가 판정의 전부다. **고가가 반드시 있어야 한다.** CLAUDE.md 4장이 리포트 기본값을
고가 기준으로 못박았기 때문에, 종가만 주는 소스로는 이 보드를 만들 수 없다.

| | 네이버 금융 | 공공데이터포털 금융위원회_주식시세정보 |
|---|---|---|
| 인증 | **불필요** | 인증키 필요 (무료·즉시 발급) |
| 엔드포인트 | `m.stock.naver.com/api/stocks/marketValue/{KOSPI\|KOSDAQ}`<br>`api.finance.naver.com/siseJson.naver` | `apis.data.go.kr/1160100/service/GetStockSecuritiesInfoService/getStockPriceInfo` |
| 호출 수 | 일봉은 **종목당 1회** (약 2,800회/일) | 하루치 전 종목이 **1회**(페이징 3회) |
| 고가 | 있음 (`siseJson` 2번 컬럼) | 있음 (`hipr`) |
| 거래대금 | 목록 API 에만 있음 | 있음 (`trPrc`) |
| 시가총액 | 있음 | 있음 (`mrktTotAmt`) |
| 수정주가 | **미확인** | **미반영** (분할 전 원가격 그대로) |
| 안정성 | 공개 API 아님. 사이트 개편이면 깨짐 | 공식 API. 스펙 고정 |
| 구현 | `ingest/naver.py` | `ingest/datago.py` |

**권장** — 주 소스를 공공데이터포털로, 폴백을 네이버로. 호출 수가 2,800배 차이 나고
차단 위험이 없다. 인증키 신청이 귀찮으면 네이버만으로도 돈다(현재 기본값).

발급: data.go.kr → "금융위원회_주식시세정보" 활용신청 → 일반 인증키(Decoding)를
`DATAGO_KEY` 환경변수에 넣는다. URL 인코딩된 키를 넣으면 이중 인코딩돼서 실패한다.

> **수정주가는 아직 미확정이다** (CLAUDE.md 9장 1번). 역사적 신고가 판정이 여기 걸린다.
> `python3 -m board.run --verify-adjust` 로 두 소스를 대조하고 결과를 `DECISIONS.md` D-001 에
> 적은 다음 소스를 고정하라. 그 전까지는 `engine/newhigh.py` 의 `split_guard` 가
> 이상 시계열을 탐지해 역사적 판정에서 빼고, 그 종목 수를 화면 상단에 띄운다.

### A2. 업종 분류 (1층 · 커버리지 보장용)

| | 상태 |
|---|---|
| **KRX 업종분류 원본** (`data.krx.co.kr`) | **불가.** 2025-12-27부터 로그인 필수 (`etf_tracker_v9/README.md` 참고) |
| 네이버 업종 (`finance.naver.com/sise/sise_group.naver?type=upjong`) | 구현됨. 인증 불필요. **KRX 업종과 이름은 비슷하나 같은 분류가 아니다** |
| 공공데이터포털 금융위원회_기업기본정보 | 업종 정보 포함. 인증키 필요 |
| DART OpenAPI 기업개황 (`opendart.fss.or.kr/api/company.json`) | 표준산업분류코드 제공. 인증키 무료 |

현재는 네이버 업종을 쓰고 `sectors.json` 에 `taxonomy: "naver_upjong"` 을 달아 뒀다.
KRX 원본을 붙이려면 위 셋 중 하나를 골라야 한다. **결정 필요.**

### A3. 지수 (코스피 · 코스닥)

- 네이버 `siseJson.naver?symbol=KOSPI` — 구현됨, 인증 불필요
- 공공데이터포털 금융위원회_지수시세정보 `getStockMarketIndex` — 구현됨, 인증키 필요

---

## B. 대시보드의 빈칸

### B1. 투자자별 매매동향 (기관 · 외국인 · 개인 · 기타법인) — **제일 큰 구멍**

프로토타입 헤더의 `기타법인 +1.04조 / 기관 +7,604억` 줄이 이것이고, 탐지기 5번
(`flow_alignment`)과 섹터 스코어의 "수급 일치 여부" 항목이 전부 여기에 달려 있다.
**현재 인증 없이 받을 수 있는 경로가 없다.**

| 후보 | 문제 |
|---|---|
| KRX 정보데이터시스템 | 로그인 필수화. 불가 |
| pykrx | KRX 스크래핑 기반이라 위와 같은 이유로 위험. CLAUDE.md 10장도 "폴백 소스를 둔다"고 적어 둠 |
| 한국투자증권 KIS Developers | 계좌 개설 + 앱키/시크릿. 무료. 국내주식 투자자별 매매동향 제공 |
| 키움증권 REST API | 계좌 + 앱키. 무료 |
| 네이버 금융 종목별 투자자 페이지 | 종목 단위 스크래핑은 가능. 시장 전체 합계는 별도로 합산해야 함 |
| 네이버 `m.stock.naver.com/api/stock/{code}/trend?pageSize=N` | **종목별 폴백으로 구현됨** (D-083). 기관·외국인 순매매량(주). 개인은 응답에 없는 날이 있다 — 없으면 만들지 않는다. KIS 접속 불가·종목 단위 실패 시 `ingest/stockflows.py` 가 쓴다 |

**사용자 결정이 필요하다.** 증권 계좌를 열어 KIS 앱키를 발급받을 생각이 있는지에 따라
갈린다. 그때까지는 헤더에 "소스 미확보"로 표기하고 비워 둔다 — 추정치로 채우지 않는다.

관련: CLAUDE.md 9장 2번(KRX 확정치 갱신 시각 16:10 안전한지)은 소스가 정해진 뒤에나
실측할 수 있다.

### B2. 환율 USD/KRW

- 네이버 `m.stock.naver.com/front-api/marketIndex/prices` — 구현됨, 인증 불필요
- 한국수출입은행 환율 API — 인증키 무료. 영업일 11시 이후 고시
- 한국은행 ECOS — 인증키 무료. 시계열이 필요하면 이쪽

### B3. `cycle_spread` 테마용 외부 지수 (SCFI · BDI · 정제마진)

CLAUDE.md 9장 4번. **무료 공개 API 가 사실상 없다.** SCFI는 상하이해운거래소 유료,
BDI는 발틱거래소 유료다. 현실적인 선택지:

1. 해운·정유 테마의 `index_divergence` 탐지기를 끄고 등락률만 본다 (지금 상태)
2. 주간 발표 수치를 뉴스에서 파싱해 수동 갱신
3. 유료 구독

---

## C. 18시 리포트(T4)까지 갈 때

### C1. 뉴스 — 네이버 검색 API

- `openapi.naver.com/v1/search/news.json`
- Client ID / Client Secret (developers.naver.com, 무료, 일 25,000회)
- **저작권**: 제목·링크·요약만 반환된다. 원문을 긁어 저장하지 마라. CLAUDE.md 9장 3번의
  "링크·헤드라인만 보관" 방침과 맞는다. 소스별 조건은 여전히 미확인이다.
- **매체명을 주지 않는다.** `originallink` 의 도메인이 매체다. 표시명은
  `settings.yaml triggers.finance_outlets / outlets` 의 도메인 표로 바꾸고, 없으면 도메인을
  그대로 적는다. 기간 필터도 없다 — `pubDate` 로 당일만 남긴다.
- 종목별 트리거(D-085)의 질의는 `'{이름} {코드}'`·`'{이름} 신고가'` 다. `'{이름} 주가'`
  는 자동 생성 시세 기사(topstarnews.net · joongangenews.com · job-post.co.kr)를 맨 위로
  끌어온다.

### C1-b. 뉴스 2차 — Google News RSS (비공식, 키 없음)

- `https://news.google.com/rss/search?q=<질의>+when:1d&hl=ko&gl=KR&ceid=KR:ko`
- 공식 API 가 아니다. 러너에서 **미검증** — `python3 -m board.run --trigger-probe`
  (Actions mode=trigger-probe)가 200·XML·한국어·5회 연속 내성을 찍는다. 429 와 consent
  페이지 리다이렉트(200 이지만 HTML)는 `Fetch` 로 올리고 그 소스를 접는다.
- 항목의 `<title>` 은 '제목 - 매체' 꼴, `<source>` 가 매체, `pubDate` 는 GMT(KST 로 바꿔
  당일 판정). 링크는 google 리다이렉트 주소 그대로 저장한다.
- 구현: `board/ingest/gnews.py`. 2차 소스라 실패는 결손으로 적되 단계를 막지 않는다.

### C1-c. X(트위터)

**무료·합법·자동 검색 경로가 없다** (D-085). 공식 API 유료, Nitter·syndication 규약
위반·불안정, 쿠키 GraphQL 계정 정지 위험. 채택한 경로는 사용자가 X 게시물을 텔레그램
봇에 공유하면 봇이 읽는 인박스(`state/inbox.json`, 수집기는 별도 작업)뿐이다.

### C2. LLM — Anthropic API

CLAUDE.md 7장의 모델 배분 그대로.

| 용도 | 모델 | 비고 |
|---|---|---|
| 뉴스 클러스터링·테마 매칭 | `claude-haiku-4-5-20251001` | Batch API (입출력 50% 할인) |
| 종목 부트스트랩 분류 | `claude-haiku-4-5-20251001` | Batch API, 1회성 |
| 섹터 서술·장마감 총평 | `claude-sonnet-5` | 실시간 |

`themes.yaml` 과 문체 규칙은 매일 동일한 접두사이므로 프롬프트 캐시 접두사에 넣는다
(캐시 히트 시 입력가의 10%).

### C3. 발송

- Telegram Bot API — 이미 있다 (`TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`)
- Telegram Bot API `getUpdates` — **들어오는 쪽.** 사용자가 X 앱에서 봇 대화로 공유한
  게시물을 읽는다 (`ingest/tg_inbox.py` → `state/inbox.json`). 같은 토큰, 인증 추가 없음.
  갱신은 서버에 **24시간**만 남고 웹훅이 걸려 있으면 409 로 거부된다.
- X oEmbed `publish.twitter.com/oembed?url=<x url>&omit_script=1` — 공식·인증 없음.
  공유 메시지 본문이 URL 뿐일 때 게시물 본문·작성자를 채우는 데만 쓴다. 검색은 못 한다
  (X 검색 API 는 유료, 스크래핑은 규약 위반).
- Notion API — integration token + database ID

### C4. DART OpenAPI

- `opendart.fss.or.kr` · 인증키 무료
- 쓸 곳: 기업개황(업종코드 → A2 대안), 사업의 내용(테마 부트스트랩 분류의 입력),
  주요사항보고서(임상 단계 — CLAUDE.md 9장 5번)
- **공시목록 `list.json` 은 종목별로 부른다** (D-085): `corp_code` + `bgn_de=end_de=기준일`
  (`dart.disclosures_for`). 날짜 전체로 부르는 `disclosures()` 는 `corp_cls='Y'`(코스피)에
  1쪽(100건)뿐이라 코스닥 신고가 종목이 빠진다 — --check 의 통신 확인용으로만 남겼다.
  응답에 접수 **시각**이 없어(`rcept_dt` 는 날짜) 소비자는 수집 시각을 '공시는 HH:MM
  접수분까지' 로 적는다. 제목 키워드로 kind(contract·capital·buyback·owner·clinical·
  earnings·inquiry·other)를 단다 — 표기용이고 판정은 하지 않는다.

---

## 정리 — 무엇부터 신청할까

| 순위 | API | 인증 | 비용 | 없으면 |
|---|---|---|---|---|
| 1 | 공공데이터포털 주식시세정보 | 키 | 무료 | 네이버로 돌아가지만 호출 2,800회/일 + 개편 리스크 |
| 2 | **투자자별 수급 소스 결정** | 미정 | 미정 | 헤더 수급 줄·탐지기 5·스코어 수급항목 전부 빈칸 |
| 3 | 네이버 검색 API | 키 | 무료 | 뉴스 탭·섹터 요약 불가 |
| 4 | Anthropic API | 키 | 종량 | 일간 코멘트 탭 불가 |
| 5 | DART OpenAPI | 키 | 무료 | 테마 매핑이 seeds 347건에 머무름 |
| 6 | KRX 업종 대안 결정 | 미정 | 미정 | 1층 분류가 네이버 업종으로 남음 |

**1·3·4·5는 신청만 하면 되고, 2와 6은 사용자가 방향을 정해야 한다.**
