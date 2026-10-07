# rankings.json 계약

`engine/rankings.py` 가 만들고 `export/excel.py`·`report/telegram.py`·`web/render.py`
셋이 읽는다. 이 파일이 세 소비자 사이의 유일한 계약이다.

**수치는 전부 두 벌로 나간다.** `*_raw` 는 float(정렬·색칠용), 접미사 없는 쪽은
사람이 읽는 문자열(표시용). 소비자는 표시용 문자열을 다시 포맷하지 않는다.
`null` 은 계산되지 않았다는 뜻이고, 0 이 아니다 (CLAUDE.md 2장 1번).

```jsonc
{
  "as_of": "2026-08-26",
  "prev_asof": "2026-08-25",
  "market": "KR",                  // KR | US
  "source": "krx+kis",
  "generated_at": "2026-08-26T18:00:00+09:00",
  "taxonomy": "wics_like",         // 섹터 분류 체계 이름
  "missing": ["..."],              // 빠진 데이터. 표 상단/리포트 머리에 그대로 노출

  // ── 표 1·2: 섹터 랭킹 + 각 섹터 상위 종목 ──────────────────
  "sector_boards": [
    {
      "key": "1d",                 // 1d | 7d | 1m ...
      "label": "금일",
      "ret_label": "금일 상승률",
      "weighting": "mktcap",       // mktcap | equal — 화면에 표기해야 한다
      "sectors": [
        {
          "rank": 1,
          "name": "비철금속",
          "ret": "+7.92%", "ret_raw": 7.92,
          "n": 23,                             // 구성 종목 수
          "breadth": {"up": 19, "flat": 0, "down": 4},
          "top": [                             // 1등~5등. 부족하면 짧게 나간다
            {"rank": 1, "code": "006110", "name": "삼아알미늄",
             "ret": "+29.90%", "ret_raw": 29.9,
             "mktcap": "1,043억", "mktcap_raw": 1043.0}
          ]
        }
      ]
    }
  ],

  // ── 표 3: 종목 랭킹 (좌우 두 표) ───────────────────────────
  "stock_boards": [
    {
      "key": "ret_1w",
      "title": "1w 주가수익률 순위",
      "sort_by": "ret_1w",
      "columns": [                             // 표시 순서. 소비자가 그대로 그린다
        {"key": "mktcap",  "label": "시가총액", "kind": "amount"},
        {"key": "ret_1d",  "label": "1d",  "kind": "pct"},
        {"key": "ret_1w",  "label": "1w",  "kind": "pct"},
        {"key": "ret_1m",  "label": "1m",  "kind": "pct"},
        {"key": "vol_3d_1m", "label": "거래량 3일/1달", "kind": "ratio"}
      ],
      "rows": [
        {
          "rank": 1, "code": "950220", "name": "네오이뮨텍",
          "cross": true,                       // 다른 표에도 등장 → 하이라이트
          "cells":     {"mktcap": "1,043억", "ret_1d": "+7.0%",  "vol_3d_1m": "162%"},
          "cells_raw": {"mktcap": 1043.0,    "ret_1d": 7.0,      "vol_3d_1m": 162.0}
        }
      ]
    }
  ],

  // 두 stock_board 에 모두 등장한 종목코드. cross=true 의 근거
  "cross_codes": ["950220", "..."]
}
```

## kind 별 표시 규칙

| kind | 예 | 색칠 |
|---|---|---|
| `pct` | `+7.92%` | 0 기준 다이버징. 국내는 상승 적색, 미국장 표는 사용자 엑셀대로 상승 적색 |
| `ratio` | `162%` | 100% 기준 다이버징 |
| `amount` | `1,043억` / `3.20조` | 색 없음 |
| `int` | `23` | 색 없음 |

## 소비자별 사용 범위

| | 쓰는 것 |
|---|---|
| `export/excel.py` | 전부. 시트 하나당 board 하나 |
| `web/render.py` | 전부. 탭 하나에 board 여러 개 |
| `report/telegram.py` | `sector_boards` 상위·하위 일부, `cross_codes` 에 해당하는 행만 |
