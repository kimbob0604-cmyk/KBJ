# ADR 0034 — 데모 모드: 별도 compose·합성 세계·고정 데모 계정·외부 호출 0 (2026-10-14)

상태: **확정**(2026-10-14 P9 WRAP — P9 묶음 DEMO 초안, 끝 'WRAP 확정 메모'). 근거: docs/p9_design.md §2·§4.3·§11 Q3·Q4, ADR 0008(로그인 API·CSRF),
ADR 0011(공개 레포 안전), DATA_TIERS §1·§4.

## 1. 맥락

KBJ 는 공개 레포지만 화면 대부분이 로그인 등급(KIS·KRX 등 원자료)이라 남이 그대로 띄워 볼 수 없다. 원자료·픽스처는
레포에 넣을 수 없고(합성만), 키·DB 없이 화면을 보여 줄 길이 필요하다. P3 부터 `scripts/demo_p3.sh`(로컬 서버 +
Playwright)가 있었으나 파이썬·node 설치가 필요했고 비밀번호가 실행마다 바뀌었다.

## 2. 결정

1. **별도 compose·별도 이미지**: `docker compose -f docker-compose.demo.yml up --build` → `http://127.0.0.1:8765/`.
   이름 `kbj-demo`, 서비스 `demo` 하나, 이미지 `kbj-demo:local`(`Dockerfile.demo`). 운영 compose·운영 이미지(`Dockerfile`)와
   파일을 섞지 않는다 — 운영 이미지 입력(`.dockerignore`)은 `tests`·`scripts` 를 빼므로 데모 서버·합성 세계가 들어가지 않는다.
2. **합성 세계만**: 데모 서버(`scripts/demo_server.py`)는 시험용 합성 세계를 메모리 저장소 + fakeredis 에 싣고 운영과
   같은 FastAPI 앱(`world.app`)을 띄운다 — api_world(금액 규모만 키운 사본)·p5 매크로(데모 날짜로 9주·2개월 옮김)·
   p6 수출입·p7 진단·GEX 엔진 사이클·p8 규칙·백테스트·일지. 서버 시계 고정(`KBJ_DEMO_AT=close` = 합성 마지막 거래일
   18:00 KST). 키·`.env`·DB·외부 호출 없음(시험 `tests/unit/demo/test_demo_offline.py` 가 소켓을 감싸 확인).
3. **고정 데모 계정**: 사용자 `demo` / 비밀번호 `kbj-demo-only`(README 에 적는다 — §11 Q3). 값은 데모 compose 의
   기본값(`${KBJ_DEMO_PASSWORD:-kbj-demo-only}`)에만 있고 이미지·운영 compose·`kbj/`·`config/` 에는 없다
   (`check_public_outputs.py` ④ `demo_account_in_ops`). 합성 데이터 전용이라 비밀이 아니다 — 운영 로그인과 섞지 않는다.
   로컬 `scripts/demo.sh` 는 실행마다 무작위 비밀번호(그대로).
4. **묶는 주소**: 서버는 기본 `127.0.0.1`. `--bind 0.0.0.0` 은 `KBJ_DEMO_CONTAINER=1`(이미지가 넣는다)일 때만 허용,
   그 밖 주소는 종료 2. 호스트 쪽은 compose 가 `127.0.0.1:8765` 에만 연다. `Settings.api_host` 는 루프백 그대로라
   쿠키 비보안 개발 모드 검증이 유지된다.
5. **Origin**: 데모 설정은 `public_base_url` 을 두지 않는다 → CSRF Origin 검사가 요청 자신의 출처와 비교해
   `127.0.0.1`·`localhost` 어느 쪽으로 열어도 로그인·쓰기(페이지 11·12)가 된다. 다른 출처는 그대로 403(시험).
6. **이미지 입력은 허용 목록**: `Dockerfile.demo.dockerignore` 첫 줄 `*`, 그 뒤 `!kbj/**`·`!config/**`·데모 서버·
   데모가 import 하는 `tests.*` 파일(정적 추적 시험 `test_demo_image_inputs.py` 가 목록을 묶는다)·합성 골든 1개·`web/**`
   (설치본·빌드·시험 제외). 끝에 `**/.env*`·`**/*.key`·`**/*.pem`·`legacy`·`state` 를 다시 뺀다. 의존성은
   `uv sync --frozen --no-dev --group demo`(fakeredis[lua] 만 — pytest 등 없음).
7. **README 미디어**: `bash scripts/demo.sh --gif docs/media`(또는 `--compose --gif`) → Playwright 가 1280×800 장면 9장
   (페이지 1·2·3·3 GEX 확대·4·8·10·11·12)을 찍고 `scripts/make_demo_gif.py` 가 GIF 1개(960×600·장마다 1.6초·256색,
   3MB 넘으면 실패)와 정지 화면 4장(각 1MB 이하)·`MANIFEST.txt`(명령·커밋·합성 세계·시드·서버 시계)를 만든다.
   빈 장면(색 8개 이하)은 실패. CI 는 다시 찍지 않고 크기·메타데이터·MANIFEST 만 본다(ADR 0035 ③).

## 3. 결과·받아들인 것

- 컨테이너 안에서는 모든 요청이 브리지 게이트웨이 IP 로 보여 로그인 잠금(무차별 대입 방지)이 사실상 전체 잠금이 된다 —
  합성 데이터 데모라 받아들인다(틀린 비밀번호를 여러 번 넣으면 잠금 시간 동안 아무도 못 들어온다).
- 합성 세계 적재에 약 2분(진단 이력·GEX 사이클·백테스트를 실제 처리기로 돈다) — compose healthcheck `start_period 120s`.
- p7 진단 입력(국고채 3년·회사채 AA-·신용잔고·예탁금)은 데모 시계까지 공표된 점만 싣고, 같은 시리즈는 p5 매크로가
  덮지 않는다(두 합성 세계를 한 시리즈에 섞지 않는다).
- 페이지 9(테마)는 자리표시, 페이지 13(운영)은 OPS 뒤 WRAP 이 싣는다. Pg 모드(`demo.sh --db`)는 페이지 3·6·11·12 없음.
- 이미지 크기·amd64/arm64 는 메인이 실제 빌드로 확인한다 [확인 필요 — probe #74 초안].

## WRAP 확정 메모 (2026-10-14)

- 페이지 13(운영)을 데모에 싣는다: `scripts/demo_server.attach_ops` 가 합성 1주 운영 기록(`tests/unit/ops/ops_world.week_source`
  — 모든 작업 성공·브리핑 매일 1통·끊김 없음)을 데모 시계까지 7일로 둔다(시계 뒤 기록은 싣지 않는다). 메모리 모드만 —
  `--db` 모드는 운영 원천이 없어 404 no_data(.msg). `Dockerfile.demo.dockerignore` 허용 목록에 `ops_world.py` 를 더했다.
  `demo_shots.mjs` 의 페이지 목록에 13 을 더했다(오류 0 확인 — p9_design 'WRAP 구현 기록').
- CI 잡 `demo (compose)`: `docker compose -f docker-compose.demo.yml up -d --build` → `scripts/demo_smoke.py`(health →
  데모 계정 로그인 → 페이지 1·2·3·4·6·7·10·11·12·13 대표 API 200, 5·8 은 404 도 정상) → `down -v`.
- 이미지 크기·arm64·첫 기동 시간(합성 세계 적재 약 120초)은 메인이 실제 빌드로 잰다 — probe #74.
- GIF 는 다시 찍지 않았다(GIF 장면은 페이지 1·2·3·4·8·10·11·12 — 13 은 장면 목록에 없다). 화면이 크게 바뀌면
  `bash scripts/demo.sh --gif docs/media` 로 다시 찍는다(MANIFEST 자동).
