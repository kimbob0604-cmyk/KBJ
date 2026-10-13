"""`python -m kbj.reports.flows --at YYYY-MM-DD` — 수급 리포트 미리보기(보내지 않는다)."""

import sys

from kbj.reports.flows.run import main

sys.exit(main())
