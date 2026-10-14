"""`python -m services.ws_gateway` — legacy compose 진입점. 정본은 `kbj.services.ws_gateway`(P7 ING)."""

from kbj.services.ws_gateway.service import main

raise SystemExit(main())
