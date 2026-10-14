# ADR 0035 — 공개 산출물 검사: 우산 스크립트·원시세 정의·fixture 출처 표식 (2026-10-14)

상태: **확정**(2026-10-14 P9 WRAP — P9 묶음 DEMO 초안, 끝 'WRAP 확정 메모'). ADR 0011 §2-3(공개 레포 안전 검사) 보충.
근거: docs/p9_design.md §3, DATA_TIERS §1·§4, CLAUDE.md 절대 규칙 2·5.

## 1. 맥락

공개 레포·공개 사이트(Pages)·README 미디어·데모 이미지가 각각 다른 검사(또는 검사 없음)에 기대고 있었다.
`check_public_safety.py` 는 비밀 형태만 보고 원시세를 보지 않았고, 공개 JSON 의 시세 칸 검사는 `trade/stock/` 한 폴더뿐,
fixture 가 합성인지는 관례(`fixtures/synthetic/`)뿐이었다.

## 2. 결정

1. **우산 `scripts/check_public_outputs.py`** — 네 단계, 기존 검사를 import 해서 부른다(규칙 두 벌 금지).
   출력은 `단계 경로:줄: 규칙 — 설명`(값 없음), 종료 0/1/2, `--json`·`--site DIR`·`--skip-site`.
   - ① repo: `check_public_safety.scan` + `fixture_provenance`.
   - ② site: `--site`(빌드한 dist-public) 또는 `public_export build --no-db` 임시 데이터의 `check_tree` + 공개 모양 웹
     픽스처(`web/test/fixtures/**/public/**`). 모든 파일에 `SECRET_SHAPES`, JSON 에 source 칸 `LOGIN_SOURCES`·원시세 칸.
     번들 JS 에는 `LOGIN_SOURCES` 를 쓰지 않는다 — 화면 문구('KRX 데이터는 로그인' 등)가 정당하게 들어 있다(오탐).
   - ③ media: `docs/media/**` 종류(.gif·.png·MANIFEST.txt)·크기(GIF ≤ 3MB·PNG ≤ 1MB)·PNG 글자 청크·GIF 주석에 경로·
     비밀 형태 0·MANIFEST 의 파일 목록과 칸(command·commit·world·seed·server_clock).
   - ④ demo: 데모 dockerignore 가 `*` 로 시작하는 허용 목록이고 `.env*`·legacy·state·`*.key`·`*.pem`·토큰 캐시를 열지
     않음(경로 표본으로 판정), 데모 compose 규칙(ADR 0034 — 루프백 포트·env_file·volumes 없음·환경변수 이름·이미지 이름),
     운영 파일에 데모 계정 값 없음.
2. **원시세 정의**: 로그인 등급 출처(KIS·KRX·금융위 주식/지수 시세·Yahoo·ETF 운용사·컨센서스)에서 온 가격·거래량·
   거래대금·시총·NAV·투자자별 수급 값. 합성 생성기가 만든 값은 원시세가 아니다.
3. **`price_fields`(키 이름 규칙)**: 공개 JSON 어디에도 `close·open·high·low·price·volume·turnover·mktcap·nav·net_value·
   chg_pct` 키가 없어야 한다. 예외는 코드 표 `PRICE_FIELD_ALLOW`(파일·키·값 모양·사유 — 공개 출처 근거가 있는 것만):
   `calendar.json` 의 `open`·`close`(장 시각 'HH:MM' 글자만), `overhang_recent.json` 의 `price`(DART 주요사항보고서의
   전환·행사가액, 숫자만). 규칙은 `kbj/services/public_export/manifest.py price_problems` 와 `web/scripts/merge-public-data.mjs
   priceProblems` 두 곳에 같은 표로 있고 대조 시험이 묶는다 — 그래서 VM 내보내기(`public_export check`)·Pages 합치기·
   우산이 같은 규칙을 쓴다. 옛 `trade/stock/` 시세 칸 규칙(D-P6-4)은 이 일반 규칙의 한 사례가 됐다.
   설계 초안의 '허용 목록 파일(public_safety_allow.txt 형식)' 대신 코드 표로 둔 까닭: 운영 이미지에는 `scripts/` 가 없어
   VM 의 `public_export check` 가 파일을 읽을 수 없고, JS 와 같은 표를 시험으로 묶기 쉽다.
4. **`fixture_provenance`**: `tests/**`·`web/test/**` 의 데이터 파일(.json·.jsonl·.csv·.yaml·.yml·.xml·.gz)은 경로에
   `synthetic` 이 있거나 `scripts/fixture_provenance.txt`(경로 패턴 | `synthetic:<생성기 경로>`·`synthetic:hand-written`·
   `public:<출처>` | 사유)에 걸려야 한다. 생성기 경로는 실제 파일이어야 하고, 아무 파일에도 안 걸리는 줄도 실패
   (묵은 줄). 새 fixture 를 더하면서 출처를 안 적으면 실패한다 — 실데이터가 몰래 들어오는 길을 막는다.
5. CI 잡 `public-outputs`(WRAP)가 기존 `public-safety` 잡을 대신한다.

## 3. 결과

- 키 이름 규칙이라 값이 합성인지 실데이터인지는 보지 않는다 — 공개 파일에 가격 모양 칸이 생기면 예외 표에 근거를
  적어야 통과한다(의도된 마찰).
- 시험 데이터 파일 중 `synthetic` 밖 526개(웹 픽스처 444·골든 82 — 설계 추정 527)는 출처 목록 23줄로 덮인다(2026-10-14 실측 — DEMO 구현 기록).

## WRAP 확정 메모 (2026-10-14)

- CI: `public-safety` 잡을 `public-outputs`(`uv run python scripts/check_public_outputs.py` — python 만)로 바꿨다.
- `legacy/` 를 비운 뒤(ADR 0038) ① 레포 파일 검사 대상에 legacy 가 없다 — `check_public_safety.py --exclude legacy`
  옵션 설명은 남겨 두되 쓸 일이 없다.
- `price_fields` 예외는 코드 표 `PRICE_FIELD_ALLOW`(manifest.py·merge-public-data.mjs, 대조 시험) 그대로 — 운영
  이미지에 `scripts/` 가 없다(§2-3).
