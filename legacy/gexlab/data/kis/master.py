"""KIS 지수선물옵션 종목 마스터 — KBJ P2: 정본은 `kbj.data.private.kis.master`(설계 §1.2).

배포 주소는 KBJ 모듈의 비공개 상수에만 있다(legacy 에는 없다 — check_canonical
`kis_master`). 내려받기는 `http_master_downloader`·`download_fo_master`. 승격한 시험:
`tests/unit/test_kis_master.py`
→ kbj `tests/unit/kis/test_kis_master.py`.
"""

from kbj.data.private.kis.master import (
    FO_MASTER_FILE,
    MASTER_CONNECT_S,
    MASTER_MAX_BYTES,
    MASTER_READ_S,
    MASTER_TOTAL_S,
    MRKT_CLS_FAMILY,
    Family,
    MasterDownloader,
    MasterRow,
    download_fo_master,
    expiry_code,
    http_master_downloader,
    master_text_from_zip,
    parse_master,
    parse_master_line,
    parse_master_zip,
    rows_by_series,
    series_for,
    series_of_codes,
)

__all__ = [
    "FO_MASTER_FILE",
    "MASTER_CONNECT_S",
    "MASTER_MAX_BYTES",
    "MASTER_READ_S",
    "MASTER_TOTAL_S",
    "MRKT_CLS_FAMILY",
    "Family",
    "MasterDownloader",
    "MasterRow",
    "download_fo_master",
    "expiry_code",
    "http_master_downloader",
    "master_text_from_zip",
    "parse_master",
    "parse_master_line",
    "parse_master_zip",
    "rows_by_series",
    "series_for",
    "series_of_codes",
]
