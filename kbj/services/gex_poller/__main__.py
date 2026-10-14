"""`python -m kbj.services.gex_poller` — compose 진입점(GX `python -m services.poller` 승계, P9
GXI).

`--help` 는 설정·연결 없이 안내만 한다(완료 확인 명령).
"""

import sys

if any(a in ("-h", "--help") for a in sys.argv[1:]):
    print(
        "usage: python -m kbj.services.gex_poller\n\n"
        "GEX 옵션 체인·선물 전광판·최종거래일 REST 수집 상시 서비스(등록부 gex.poller, 하트비트\n"
        "gex-poller). 환경변수: KBJ_REDIS_URL·KBJ_DATABASE_URL·KBJ_KIS_APP_KEY·\n"
        "KBJ_KIS_APP_SECRET. "
        "토큰은 auth 가 Redis 에 둔 것을 읽기만 한다(ADR 0004). 마스터는 kis.master 작업이 둔\n"
        "Redis kis:master 를 읽는다."
    )
    raise SystemExit(0)

from kbj.services.gex_poller.service import main

raise SystemExit(main())
