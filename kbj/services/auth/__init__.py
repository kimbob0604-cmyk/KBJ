"""auth — KIS 접근토큰·웹소켓 접속키를 발급하는 유일한 곳(1분 1회, ADR 0001 U1·ADR 0004).

- `issuer` 발급자(이 레포에서 발급 요청을 보내는 유일한 파일) + 런타임 가드(`KBJ_SERVICE=auth` 만)
- `service` `AuthService`(30초 step, 만료 60분 전 갱신, 61초 발급 간격, 거절 신고 가드),
  `serve`, 진입점

다른 모듈은 이 패키지를 import 하지 않는다(import-linter 계약 — 설계 §9.6 ④). 토큰은
`kbj.data.private.kis.token.reader` 로 Redis 에서 읽기만 한다.
"""
