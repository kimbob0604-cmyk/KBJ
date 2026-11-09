# ADR 0042 — 공개 사이트(kbj-site): 맥이 페이지 1~10 을 정적 스냅샷으로 GitHub Pages 에 올리고, 맥 로컬은 로그인 없이 (2026-11-09)

상태: **채택**(사용자 결정 2026-11-09). 근거: 사용자 요청 "beoms dashboard 참고하고 그런식으로 수정해줘. 그리고 로그인
기능은 빼버리면 좋을듯" → "https://kimbob0604-cmyk.github.io/beoms-dashboard/ 이런식으로" → 공개 범위 질문에 **"1~10페이지
전부 공개"**(KIS·KRX·컨센서스·ETF 운용사 자료의 약관상 재배포 금지 위험을 알린 뒤 고른 선택).
바꾸는 결정: `docs/DATA_TIERS.md` §3(구조로 나누기)의 예외, CLAUDE.md §2 의 공개 사이트 범위, ADR 0041(로컬 로그인).
구현: `kbj/services/api/site.py`(내보내기)·`python -m kbj.services.api site`, `config/site.yaml`, `scripts/local/site.sh`
(`kbj.sh site`), web `build:site`(`src/boot/site.ts`·`src/data/snapshot.ts`), 설정 `KBJ_WEB_LOGIN`(맥 로컬 false).

## 1. 맥락

- 맥이 꺼지거나 재부팅되면(10-22·10-26·10-27) 화면도 볼 수 없다. 오라클 무료 VM 은 이 계정에서 만들 수 없다(ADR 0041).
- 사용자의 다른 프로젝트 Beom's Dashboard 는 맥이 수집하고 **정적 JSON + 화면을 GitHub Pages 에 올려** 로그인 없이 어디서나
  본다(맥이 꺼져 있어도 마지막 화면). 사용자는 KBJ 도 같은 방식을 원한다.
- KBJ 는 공개/로그인 등급을 구조로 나눠 왔다(DATA_TIERS §3). 공개판(`build:public`)은 공개 등급 자료만 — 시세·수급·ETF 는
  자물쇠다. 사용자는 이 구분 대신 **페이지 1~10 전부**를 공개하기로 정했다.

## 2. 결정

1. **공개 사이트 = 로그인판 위젯 + 정적 스냅샷**. 새 빌드 모드 `site`(`npm run build:site` → `dist-site`)는 로그인판과 같은
   위젯(등급 'login' — 자물쇠 없음)을 `data/snapshot.ts` 로 그린다. 키는 API 경로 그대로, 파일은
   `data/<경로>[@<키>-<값>.…].json`(site.py `site_file` 과 snapshot.ts `siteFile` 이 같은 규칙 — 시험이 같은 표로 묶는다).
   쓰기 채널 없음. 페이지 **11·12·13(개인 기능)은 붙이지 않는다**. 엑셀 내려받기 없음. 검색은 상세가 있는 종목 목록
   (`stock/search-index.json`)을 화면에서 거른다.
2. **내보내기는 맥의 api 컨테이너 안**에서 audit 와 같은 세션 우회로 GET 을 부른다(서버·쿠키를 건드리지 않음). 개인 경로
   (auth·webhook·rules·backtest·journal·portfolio·watchlist·ops·health)는 부르지 않고, 응답 안의 `is_watch` 는 false 로 지운다.
   404 `no_data` 본문은 파일로 남겨 화면이 같은 안내 문구를 보인다. 그 밖 실패는 결과 줄에 센다.
3. **묶음**: `full`(인자 없는 경로 전부 + variants + 화면에 나온 종목 상세 `max_stocks` + 테마 상세 + 검색 목록 — 07:40·16:40·
   20:40 뒤 한 번씩), `live`(장중 10분 — 시세·수급·GEX 경로만 덮어쓰기). launchd `com.kbj.site` 가 10분마다
   `kbj.sh site publish auto`.
4. **배포 저장소는 따로**: 공개 저장소 `kimbob0604-cmyk/kbj-site` 의 `gh-pages` 에 커밋 하나로 강제 푸시(Beom's 와 같은 방식 —
   날마다 바뀌는 JSON 으로 히스토리가 불어나지 않게, 바뀐 게 없으면 올리지 않음). **KBJ 코드 저장소에는 여전히 원자료를 넣지
   않는다**(CLAUDE.md §2 그대로 — 데이터는 kbj-site 에만).
5. **맥 로컬은 로그인 없이**: `KBJ_WEB_LOGIN=false`(docker-compose.local.yml). 설정 검증이 루프백·컨테이너 게시(호스트
   127.0.0.1)일 때만 허용한다. 쿠키 세션이 없으면 프로세스마다 하나인 로컬 세션 — 쓰기는 `/api/auth/me` 의 CSRF 토큰과 같은
   출처(Origin)를 그대로 본다(다른 사이트가 맥 API 에 쓰지 못한다). VM(기본 compose)은 로그인 그대로.

## 3. 결과·위험

- **약관 위험은 사용자가 감수했다**: KIS·KRX(금융위 주식시세 포함)·컨센서스·ETF 운용사·Nasdaq 등 로그인 등급 원천의 값이 누구나
  보는 주소에 오른다(DATA_TIERS §1 '로그인' 행). 원천이 문제를 제기하면 `kbj.sh site schedule off` 뒤 kbj-site 저장소를
  지우거나 비공개로 돌리고, 그 원천의 경로를 `SKIP` 에 더한다.
- 수집은 여전히 맥에서만 돈다 — 맥이 꺼지면 화면은 마지막으로 올린 시각에 멈춘다(배지·원천 줄의 기준 시각이 그것을 보인다).
- GitHub Pages 한도(사이트 1GB·시간당 빌드 약 10회): 10분 간격, 종목 상세 `max_stocks` 로 크기를 묶는다.
- full 내보내기는 DB 부하가 크다(종목마다 경로 약 15개) — 장중에는 돌리지 않는다.
- 공개 빌드(`build:public` — Pages 워크플로·공개 번들 검사)는 그대로 둔다(지우지 않음).
