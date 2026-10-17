# ADR 0041 — 로컬 실행(개인용, Mac 한 대): 브리지 네트워크 오버레이 + 루프백 게시 + 스크립트 한 벌 (2026-10-17)

상태: **제안**(사용자가 맥에서 첫 기동을 확인하면 확정). 근거: ADR 0040(VM 배포 — 오라클 무료 ARM VM 을 이 계정에서
만들 수 없음: 한도 `LimitExceeded`·공식 문서가 춘천 A1 제외), ADR 0008(로그인 API·쿠키), 사용자 결정 "공유 말고 나만 쓰는 로컬 버전".
구현: `docker-compose.local.yml`, `scripts/local/kbj.sh`, `scripts/vm/lib.sh`(KBJ_MODE=local), 설정 `KBJ_API_CONTAINER_BIND`,
안내서 `docs/local_run.md`, 시험 `tests/test_vm_kit.py`(로컬 절).

## 1. 맥락

- 오라클 Always Free A1 은 이 계정(춘천)에서 만들 수 없다(2 OCPU·12GB 로도 `The limit for this tenancy has been exceeded`).
  사용자는 서버 비용 없이 **본인만** 쓰는 대시보드를 원한다. 공유·도메인·HTTPS 는 필요 없다.
- 기본 compose 의 `api` 는 `network_mode: host`(VM 에서 Caddy 가 127.0.0.1 로 들어오게 — ADR 0008). Docker Desktop(Mac)의
  host 네트워크는 도커 내부 VM 이라 브라우저(맥)가 닿지 않는다. 또 `__Host-` Secure 쿠키는 http 에서 Safari 가 받지 않는다.

## 2. 결정

1. **오버레이 `docker-compose.local.yml`**: api 만 바꾼다 — `network_mode` 를 `!reset`(compose 2.24+)으로 지우고 브리지 네트워크,
   `ports: 127.0.0.1:8000:8000`(호스트는 루프백에만 — LAN 에 열리지 않음), DB·Redis 주소는 서비스 이름(`db`·`redis`).
   기본 `docker-compose.yml`·VM 오버레이는 바꾸지 않는다. 비밀은 넣지 않는다(.env).
2. **쿠키·바인딩**: 컨테이너 안 `KBJ_API_HOST=0.0.0.0` 은 루프백이 아니라서 `KBJ_WEB_COOKIE_SECURE=false` 가 설정 검증에서 막힌다.
   새 설정 `KBJ_API_CONTAINER_BIND`(기본 false)가 true 일 때만 이 조합을 허용한다. 호스트 쪽 노출은 오버레이의 루프백 게시가
   보장한다. 기본값은 그대로 안전하다(TLS 뒤 `__Host-` 쿠키). 출처 검사는 `KBJ_PUBLIC_BASE_URL` 을 비워 요청 자신의 주소와 비교한다.
3. **스크립트**: `scripts/vm/lib.sh` 를 `KBJ_MODE=local` 로 재사용(오버레이 선택·sudo 없음·안내 문구 `kbj_cmd`)하고, 맥 기본
   bash 3.2 에서 돌도록 bash 4+ 전용 문법(`mapfile`·`${x,,}`·빈 배열 확장)을 걷어냈다(시험이 막는다). 진입점은 `scripts/local/kbj.sh`
   하나(start·stop·status·keys·password·telegram·backup·update·awake·open) — 키 입력·비밀번호 해시·백업·텔레그램은 VM 스크립트를 그대로 쓴다.
4. **텔레그램**: 웹훅(받기)은 공개 HTTPS 주소가 필요해 로컬에서는 막는다(`telegram.sh webhook` 거부). 보내기(notifier outbox)는 된다.
5. **잠자기**: 수집은 맥이 깨어 있을 때만 돈다. `awake on` 이 `caffeinate -i -m -s` 를 띄운다(덮개를 닫으면 잠드는 것은 맥의 한계 — 안내서에 적는다).
6. **필수 키**: 로컬은 텔레그램을 필수에서 뺀다(KIS 앱키·시크릿, 로그인, DB·Redis 비밀번호, 내보내기 역할만).

## 3. 결과·남은 것

- 맥에서 `start` 한 줄로 http://127.0.0.1:8000 에 로그인판 대시보드가 뜬다(첫 빌드 10~20분).
- [확인 필요 — 사용자 맥 첫 기동] Apple Silicon 에서 timescale·redis·앱 이미지의 실제 기동과 빌드 시간·메모리,
  Docker Desktop 의 `!reset` 지원 버전, 잠든 구간의 따라잡기 동작.
- 실데이터 실측 전 꺼 둔 작업(관세청·컨센서스·지수 정기변경·VKOSPI)과 GX 전환(런북 ④)은 그대로다.
