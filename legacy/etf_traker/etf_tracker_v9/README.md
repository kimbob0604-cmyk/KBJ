# 국내 ETF 구성종목 일일 변동 트래커

국내 상장 ETF가 어떤 종목을 새로 담고, 빼고, 비중을 늘리고 줄였는지 매일 추적해서
테마별로 묶어 텔레그램으로 보냅니다.

```
*#바이오헬스ETF*  _19개 ETF 추적_
📈 에이비엘바이오 `298380`  +77.7% ⚡
   └ TIME K바이오액티브
📉 한미약품 `128940`  -29.2% ⚡
   └ TIME K바이오액티브
```

> **먼저 읽으세요 →** `운영가이드.md` 에 GitHub Actions로 서버 없이 무료로 굴리는 방법이
> 15분 체크리스트로 정리돼 있습니다. 아래는 내 PC에서 직접 돌릴 때의 설치법입니다.

---

## 설치

```bash
# 1) 파일 압축 해제 후 폴더로 이동
cd etf_tracker

# 2) 의존성 (requests 하나뿐입니다)
pip install -r requirements.txt

# 3) 설정 파일 만들기
cp .env.example .env
```

## 텔레그램 봇 만들기

`.env` 에 채울 값 두 개를 준비합니다.

**① 봇 토큰**

1. 텔레그램에서 **@BotFather** 를 검색해 대화를 시작합니다
2. `/newbot` 을 보냅니다
3. 봇 이름과 아이디를 정하면 토큰을 줍니다 — `1234567890:AAHxxxx...` 형태
4. 이 값을 `.env` 의 `TELEGRAM_BOT_TOKEN=` 뒤에 붙여넣습니다

**② 대화방 ID**

개인으로 받을 경우:

1. 방금 만든 봇을 검색해서 **아무 메시지나 하나 보냅니다** (이걸 안 하면 봇이 나에게 말을 걸 수 없습니다)
2. **@userinfobot** 을 검색해 `/start` 를 보냅니다
3. 알려주는 `Id` 숫자를 `TELEGRAM_CHAT_ID=` 뒤에 붙여넣습니다

그룹으로 받을 경우:

1. 그룹을 만들고 내 봇을 초대합니다
2. **@RawDataBot** 도 잠깐 초대하면 `"chat":{"id":-1001234567890}` 을 보여줍니다
3. 그 `id` 값(`-100`으로 시작)을 넣고 RawDataBot 은 내보냅니다

**③ 연결 확인**

```bash
python tracker.py --check
```

봇 이름이 뜨고 테스트 메시지가 오면 준비 끝입니다.

---

## 실행

```bash
# 최초 1회 — ETF 목록 구축 + 오늘자 스냅샷 (5~10분)
python tracker.py --init

# 다음 영업일부터 매일
python tracker.py --run
```

첫날은 비교 대상이 없어서 알림이 오지 않습니다. **이틀치가 쌓여야 변동이 나옵니다.**

### 매일 자동 실행

```bash
crontab -e
```

```cron
# 평일 오전 8시 (PDF는 전영업일 기준으로 아침에 갱신됩니다)
0 8 * * 1-5  /path/to/etf_tracker/run.sh >> /path/to/etf_tracker/log.txt 2>&1
```

`run.sh` 는 `etf.db` 가 없으면 알아서 `--init` 을 먼저 돌립니다.

---

## 자주 쓰는 명령

```bash
python tracker.py --check              # 설정·소스·텔레그램 점검
# (KBJ P3: --verify·verify.py 삭제 — 검산은 python -m kbj.services.engine.verify)
python tracker.py --themes             # 테마별 ETF 분포
python tracker.py --run --no-send      # 발송 없이 콘솔로만 확인
python tracker.py --research 조선       # 특정 테마 심층 조회
python tracker.py --run --min-funds 2  # 2개 이상 ETF에서 동시 발생한 것만
python tracker.py --run --passive      # 시장대표·팩터 등 패시브 테마까지 전부
python tracker.py --backfill 2026-07-01 2026-08-03   # 과거 구간 채우기
```

### 알림이 너무 많거나 적을 때

`.env` 에서 조절합니다.

```bash
ACTION_PP=3.0     # 기본 2.0. 올리면 알림이 줄어듭니다
MIN_FUNDS=2       # 기본 1. 2로 두면 복수 ETF 동시 발생만 알립니다
```

실측 기준(2026-07-28 → 08-03, 403개 ETF):

| 설정 | 알림 건수 | 텔레그램 |
|---|---:|---|
| 기본 (`MIN_FUNDS=1`) | 129건 | 2개 메시지 |
| `MIN_FUNDS=2` | 12건 | 1개 메시지 |

처음엔 기본값으로 며칠 받아보고, 많다 싶으면 `MIN_FUNDS=2`로 바꾸는 걸 권합니다.

---

## 리포트 읽는 법

| 기호 | 뜻 |
|---|---|
| 🆕 | 신규편입 — 없던 종목이 들어옴 |
| ❌ | 전량제외 — 있던 종목을 다 팔았음 |
| 📈 📉 | 비중확대 / 축소 — 수량 기준, 주가효과 제거 후 |
| 🔼 🔽 | TOP10 진입 / 이탈 — 상위 10종목만 보이는 소스라 완전 매도인지는 알 수 없음 |
| ⚡ | 액티브 ETF — 지수가 아니라 **운용사가 판단**해서 바꾼 것 |

**⚡ 가 붙은 것에 주목하세요.** 인덱스 ETF의 종목 교체는 운용사 판단이 아니라 지수 정기변경을
따라간 것뿐입니다. 진짜 시그널은 액티브 ETF와, 여러 ETF에서 동시에 발생한 변동입니다.
리포트는 이미 그 순서로 정렬됩니다.

---

## 데이터 품질 검증

`python tracker.py --verify` 로 10개 항목을 **실측**합니다. 어댑터를 추가했거나
운용사 사이트가 바뀐 뒤에는 반드시 돌려보세요. 2026-08-04 기준 전항목 통과:

```
[1] 유니버스 커버리지
  PASS  추적대상 = 국내ETF - 파생 - 해외오분류   432 - 파생29 - 해외2 = 401 / 추적 401
[2] 수집 성공률
  PASS  전 추적 ETF 데이터 보유                401/401
[3] 테마 분류
  PASS  테마 미할당 0건                      401개 전부 테마 보유
  PASS  섹터테마 확정률                       멀티전략(비섹터) 1개 = 0.2%
[4] 데이터 품질 — 비주식 혼입
  PASS  비주식 코드 0건                      고유코드 1,355개 중 미확인 0건
[5] 비중 합계 — 데이터 유실 탐지
  PASS  전체종목 소스 비중합 85~101.5%          218개 검사, 이탈 0건
  PASS  비중 단위 오류 0건                    합계 5% 미만 0건
  PASS  200 추종형 종목수 정상                 9개 중 절단 0건
[6] 소스간 교차검증 (KOSPI200)
  PASS  200 추종 ETF 구성종목 일치             4개 소스, 최저 일치율 99.0%
[7] 우선주 코드 정규화
  PASS  우선주 코드 유효                      20종 확인

=== 결과: 10개 통과 / 0개 실패 ===
```

### 각 항목이 무엇을 잡는가

| 항목 | 잡아내는 결함 |
|---|---|
| 유니버스 | 운용사 목록 조회 실패로 통째로 누락되는 경우 |
| 수집 성공률 | 일시적 네트워크 오류로 조용히 빠지는 ETF |
| 테마 분류 | 새 ETF가 규칙에 안 걸려 리포트에서 사라지는 경우 |
| 비주식 혼입 | 선물·옵션·채권·CP·현금이 종목으로 잡혀 롤오버마다 가짜 신호 발행 |
| 데이터 유실 | 페이지네이션 절단으로 200종목이 10종목만 오는 경우 |
| 단위 오류 | 비중을 0.33(소수)로 받아놓고 33%로 쓰는 경우 |
| 교차검증 | 특정 소스만 다른 데이터를 주는 경우 |
| 우선주 코드 | 소스마다 삼성전자우를 005931/005935로 달리 잡아 집계가 갈리는 경우 |

**비중 합계가 100%가 아닌 건 정상입니다.** 어댑터는 현금·선물·옵션을 의도적으로 뺍니다.
SOL 200TR은 선물 5.2% + 현금 5.1%를 빼서 89.7%, PLUS 한화그룹주는 원화예금 8.1%를 빼서
91.9%가 됩니다. 원본은 둘 다 100.0%이고, 종목 시그널에는 영향이 없습니다.

---

## 데이터 출처

운용사가 매일 공시하는 PDF(납입자산구성내역)를 각 사 공식 사이트에서 직접 받습니다.
자본시장법상 공개 의무가 있는 정보라 **인증키도 비용도 필요 없습니다.**

전용 어댑터가 없는 운용사는 네이버 금융에서 상위 10종목을 받아 메웁니다.

> KRX 정보데이터시스템(data.krx.co.kr)은 2025년 12월 27일부터 로그인이 필수라 쓰지 않습니다.
> 자세한 경위는 `검증보고서.md` 참고.

---

## 운용사 추가하기

`adapters/` 폴더에 파일 하나만 넣으면 자동 등록됩니다. 다른 파일은 건드릴 필요 없습니다.

```python
# adapters/myissuer.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from collectors import _session, _f, _is_kr, TIMEOUT

class MyIssuer:
    KEY, NAME, HISTORY, DEPTH = 'myissuer', '운용사명', True, 'full'

    def __init__(self):
        self.s = _session()

    def universe(self):
        # -> [(fund_key, ticker, name), ...]
        ...

    def holdings(self, fund_key, date):
        # date: 'YYYY-MM-DD'
        # -> ({종목코드: {'name':str,'qty':float,'wt':float,'val':float}}, 실제기준일)
        ...
```

넣은 뒤 `python tracker.py --init` 을 다시 돌리면 네이버 폴백에서 전용 어댑터로 승격됩니다.

---

## 문서

- `운영가이드.md` — **매일 자동으로 굴리는 법 (GitHub Actions, 여기부터 보세요)**
- `market.py` — 시세·순자산 수집 (네이버 ETF API + 일봉)
- `dash.py` — 대시보드 집계 (등락·수익률·자금흐름·거래급증·신규상장)
- `render.py` — 웹 대시보드 HTML 렌더링
- `report.py` — 텔레그램 전문 리포트 (본체)
- `live_update.py` — 장중 갱신 전용 진입점 (requests 만 있으면 동작)

```
아침 09:10  tracker.py --run    PDF 400여개 + 일봉 → docs/base.json (무겁다, 3~5분)
장중 15분마다  live_update.py     목록 API 1회 + base.json → docs/index.html (1.7초)
```
- `README.md` — 이 문서 (로컬 설치·명령어 레퍼런스)
- `사용가이드.md` — 커버리지, 테마 분류, 노이즈 제거 로직, 제약사항
- `검증보고서.md` — 데이터 소스 검증 경위 (KRX 차단, 대안 탐색 과정)

## 파일

```
tracker.py        수집 → 노이즈 제거 → 테마 리포트 → 텔레그램
collectors.py     운용사별 수집 어댑터 + adapters/ 자동 로더
themes.py         테마 분류 규칙
adapters/         추가 운용사 어댑터 (자동 등록)
.env              설정 (직접 만들어야 함 — .env.example 복사)
run.sh            cron 용 실행 래퍼
.github/workflows/ GitHub Actions 자동 실행 설정
.env.example      설정 템플릿 (.env 로 복사해서 사용)
etf.db            SQLite. 자동 생성됩니다
```
