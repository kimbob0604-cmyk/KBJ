# flowlab

board 엔진 산출물(`board/state/{날짜}/*.json`) 위에 수급·이벤트 레이어를 얹는 부가 모듈.
엔진 소스는 수정하지 않는다. state 는 읽기만 하고, 추가 기록은 `flows.json` 하나뿐이다.

스크리닝 기준은 엔진과 같다 — 시가총액 1,000억 이상, 거래대금 50억 이상,
고가 기준 52주(252일)/60일, 근접 갭 5% 이내. 값은 `config.py` 상수에 있고
`board/config/settings.yaml` 과 어긋나면 실행 시 경고가 뜬다.

## 실행

```bash
python3 -m flowlab flows                   # 수급 레이어 (최신 state 날짜)
python3 -m flowlab flows --date 20260904   # 특정 날짜
python3 -m flowlab study                   # 3년 이벤트 스터디
python3 -m flowlab study --years 5
python3 -m flowlab verify                  # 검증 4종
python3 -m flowlab selftest                # 파서·계산 단위 검증 (네트워크 불필요)
python3 -m flowlab --source demo backfill --days 60   # (demo) 과거 날짜 state + flows 누적
python3 -m flowlab history                 # 누적 flows 한 표 + 이벤트 스터디 조인
```

의존성: `pandas numpy requests lxml beautifulsoup4 openpyxl` (공매도 쓸 때만 `pykrx` 대신 KRX 계정)

네이버로 나가는 접속이 막힌 환경에서는 `--source demo` 로 배관만 돌린다.
합성 소스는 board 데모 DB(`board/board.db.demo`)의 일봉을 쓰고, 산출물에
`source="demo"` 가 박히며 리포트 상단·하단에 그대로 노출된다. 발행 폴더(`docs/`)
대신 `docs-demo/` 로 나간다.

## 모듈

| 파일 | 역할 |
|---|---|
| `config.py` | 경로·임계치·`.env` 로딩. 엔진 기준값 상수 |
| `naver.py` | 네이버 수집기. `daily_ohlcv`(siseJson) · `investor_flows`(frgn 표) |
| `demo.py` | 오프라인 합성 소스. `naver` 와 같은 시그니처 |
| `prices.py` | 일봉 캐시 `flowlab/cache/prices/{source}/{code}.csv.gz`, 증분 갱신 |
| `flows.py` | **모듈 A** 수급 레이어 |
| `eventstudy.py` | **모듈 B** 신고가 이벤트 스터디 |
| `prior.py` | B 결과를 A 에 되먹임 (거래량 구간별 과거 승률 부착) |
| `krx.py` | 공매도 (KRX 계정 있을 때만 동작) |
| `backfill.py` | (demo 전용) 데모 DB 복사본에 날짜별 스냅샷을 채우고 **엔진을 날짜마다 호출**해 과거 state 를 만든 뒤 flows 를 누적 |
| `history.py` | `board/state/*/flows.json` 을 한 표로 모으고 이벤트 스터디와 (종목, 날짜) 조인 |
| `report.py` | HTML 리포트 + 일간 코멘트용 ~함 체 텍스트 |
| `verify.py` / `tests.py` | 검증 · 단위 검증 |
| `__main__.py` | CLI |

## 산출물

| 경로 | 내용 |
|---|---|
| `board/state/{날짜}/flows.json` | 종목별 수급 지표 + 집계 |
| `docs/flows-{날짜}.html` | 수급 리포트 (표·바차트) |
| `flowlab/out/flows-{날짜}.md` | 일간 코멘트에 그대로 붙이는 ~함 체 문단 |
| `docs/eventstudy.html` | 이벤트 스터디 리포트 |
| `flowlab/out/eventstudy.json` | 축별 집계 |
| `flowlab/out/eventstudy_events.csv.gz` | 이벤트 원본 (재집계용) |
| `flowlab/out/flows_history.csv.gz` | 날짜별 flows.json 누적 원본 |
| `flowlab/out/flows_history.json` | 날짜별 등급 분포 추이 + 수급 등급별 신고가 후행 성과 (이벤트 스터디 조인) |
| `docs/flows-history.html` | 누적 리포트 — 날짜별 등급 구성 차트 · 날짜별 표 · 등급별 후행 성과 |

## 모듈 A — 수급 레이어

`newhigh.json` 의 `achieved` + `proximity` 종목에 네이버 투자자별 순매매를 결합한다.

- `inst_/frgn_/net_{1,5,20}d_eok` — 일별 순매매량 × 그날 종가 합산, 억원
- `intensity_5d_bp` — 5일 순매수 / 시가총액, bp
- `concentration_1d_pct` — 당일 (기관+외인) 순매수 / 당일 거래대금
- `frgn_rate_chg_{5,20}d_pp` — 외국인 보유율 변화
- `flow_grade` — 쌍끌이 / 기관주도 / 외인주도 / 개인주도 (5일 기준)
- `supported` — 개인주도가 아니고 강도 ≥ 20bp
- `prior_bucket` / `prior_win_20d_pct` — 이벤트 스터디에서 온 과거 통계
- `days_{n}d` — 그 창에 실제로 쓴 거래일 수. 5일에 못 미치면 그대로 드러난다

**과거 날짜로 돌릴 때 `as_of` 절단이 필수다.** frgn 표는 오늘부터 역순이라
절단을 빠뜨리면 미래 거래일이 창에 섞인다. 페이지 수는 `_pages_needed()` 가
as_of 와 오늘 사이 영업일만큼 자동으로 늘린다.

## 모듈 B — 이벤트 스터디

52주·60일 신고가와 근접을 최근 N년에서 뽑아 소속 지수 대비 후행 초과수익을
축별로 집계한다. 축은 거래량 배수(20일 평균 대비) · 신선도(신규/연속) ·
연속 일수 · 라벨 · 근접 갭 구간별 5일 내 돌파 전환율.

한계는 산출 JSON 의 `limits` 와 리포트 하단에 함께 적는다 — 최신 유니버스
기준이라 상장폐지 종목이 빠지고(생존편향), 과거 시총은 현재 상장주식수 ×
당시 종가 근사이며, `hist` 는 창 길이 한계로 제외한다. 일간 |변동| 60% 초과
종목은 단절 이후 구간만 쓴다.

## flows 누적 (실데이터 없이)

실데이터에서는 엔진 `--daily` 가 매일 state 를 하나씩 쌓고, 그 위에 `flows` 를 돌리면
`board/state/{날짜}/flows.json` 이 날짜별로 누적된다. 네트워크가 막힌 환경에서는
`backfill` 이 같은 모양을 흉내낸다.

- 데모 DB 를 `flowlab/cache/` 로 복사하고, 원본에 마지막 날 것만 있는 종목 스냅샷을
  날짜별로 채운다 (거래대금 = 종가×거래량, 시총 = 최신 상장주식수×그날 종가 근사)
- 그 복사본으로 엔진의 `board.engine.build.run(db, asof)` 을 날짜순으로 호출한다.
  newhigh.json 은 **엔진이 쓴다** — 여기서 손으로 만들지 않는다. 날짜순이라 이튿날의
  신규/이어감 판정도 선다
- 날짜마다 `flows` 를 돌린다. 데모 수급 표에도 as_of 이후 행이 있으므로 절단·페이지
  확대 경로가 실제로 밟힌다 (`verify --date` 로 날짜별 확인)
- `history` 가 전부 모아 이벤트 스터디와 조인한다. 후행 20일이 지나야 조인되므로
  마지막 20거래일은 표에 안 들어간다. 구간 n<30 은 `small_sample` 로 표시

엔진 데모 DB 자체는 건드리지 않는다. 실데이터 state 와 섞이지 않도록 `source` 열로
구분하고, 합성 날짜는 실데이터 통계에 합산하지 않는다.

## 보드 대시보드 링크

`docs/index.html` 헤더(랭킹 엑셀 옆)에 `수급` · `수급 누적` · `이벤트 스터디` 링크가 붙는다.
링크는 **파일이 사이트 루트에 실제로 있을 때만** 달린다 — `board/web/site.py` 가 발행할 때
확인하고, 표식(`<!--reports-->…<!--/reports-->`) 을 남긴다. 엔진 `--daily` 가 보드를 먼저
올린 뒤 flowlab 이 리포트를 만들면 `flows` / `study` / `history` 가 끝날 때
`site.relink()` 로 그 표식 사이만 다시 채운다. 보관본(`d/{날짜}.html`)은 `../` 경로.

## 데이터 경로

- 가격: `https://api.finance.naver.com/siseJson.naver?symbol={code|KOSPI|KOSDAQ}&requestType=1&startTime=&endTime=&timeframe=day`
  — 한 요청에 전체 일봉 + 외국인소진율. 지수도 같은 방식
- 수급: `https://finance.naver.com/item/frgn.naver?code={code}&page={n}` (euc-kr, 1p = 20거래일)
- 공매도: KRX 계정 필요. `board/.env` 에 `KRX_ID` / `KRX_PW` 를 넣으면 `krx.py` 가 활성화된다
- KRX(data.krx.co.kr)는 로그인 없이 JSON 을 주지 않는다(모든 엔드포인트 `LOGOUT`).
  pykrx 의 투자자별·공매도 함수도 같은 이유로 빈 표를 돌려준다.
  KRX Data Marketplace OpenAPI 는 지수·주식·채권·파생만 있고 투자자별·공매도가 없다

## 검증

`python3 -m flowlab verify` 가 네 가지를 다시 돌린다. 코드를 고치면 이걸 통과시킨다.

1. **수급 금액** — `flows.json` 의 5일 기관·외인 억원을 원천 표에서 독립 재계산해 대조.
   수기 기준값이 있으면 `--ref 999990:inst5=12.3,frgn5=45.6` 로 함께 대조
2. **신고가 판정** — 단순 루프로 고가·종가 두 기준을 라벨별 룩백으로 각각 재현. 엔진 달성 ⊆ 루프 (누락 0).
   엔진이 저장한 당일 종가·고가가 원천 일봉과 다른 건은 '원천 상이'로 따로 세어 WARN 으로 낸다
   (판정 로직이 아니라 수집 데이터의 문제). 유니버스 전체의 당일 종가 불일치 비율도 함께 찍는다
3. **후행·초과수익** — 이벤트 한 건을 인덱싱만으로 다시 계산해 대조
4. **as_of 절단** — 창에 미래 거래일이 섞이지 않는지, 페이지 수가 필요량 이상인지
