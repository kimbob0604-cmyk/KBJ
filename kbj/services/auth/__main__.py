"""`python -m kbj.services.auth` — auth 서비스 진입점(compose `auth`, `KBJ_SERVICE=auth`)."""

from kbj.services.auth.service import main

raise SystemExit(main())
