"""`python -m services.recorder` — legacy compose 진입점. 정본은 `kbj.services.recorder`(P7 ING)."""

from kbj.services.recorder.service import main

raise SystemExit(main())
