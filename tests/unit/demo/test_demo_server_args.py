"""scripts/demo_server.py 인자·설정 — 묶는 주소·비밀번호·서버 시계.

docs/p9_design.md §2.3, ADR 0034.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

import demo_server as ds
from kbj.config.settings import Settings
from kbj.core.p5_rows import MacroSeriesRow
from kbj.core.quality import Quality
from kbj.core.time import KST

PW = "x" * ds.MIN_PASSWORD
OK_ENV = {ds.PASSWORD_ENV: PW}
ANY = ds.ANY_ADDR


def test_defaults_are_loopback_and_close() -> None:
    args = ds.parse_args([], OK_ENV)
    assert not isinstance(args, str)
    assert args.bind == ds.LOOPBACK == "127.0.0.1" and args.at == "close" and args.port == 8765


def test_any_address_only_inside_demo_container() -> None:
    msg = ds.parse_args(["--bind", ANY], OK_ENV)
    assert isinstance(msg, str) and ds.CONTAINER_ENV in msg
    ok = ds.parse_args(["--bind", ANY], {**OK_ENV, ds.CONTAINER_ENV: "1"})
    assert not isinstance(ok, str) and ok.bind == ds.ANY_ADDR
    assert isinstance(ds.parse_args(["--bind", ANY], {**OK_ENV, ds.CONTAINER_ENV: "true"}), str)


@pytest.mark.parametrize("addr", ["192.168.0.10", "::", "localhost", ""])
def test_other_addresses_refused(addr: str) -> None:
    assert isinstance(ds.parse_args(["--bind", addr], {**OK_ENV, ds.CONTAINER_ENV: "1"}), str)


def test_main_exits_2_without_starting(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """잘못된 인자는 서버를 띄우기 전에 종료 2(합성 세계를 만들지 않는다)."""
    monkeypatch.setenv(ds.PASSWORD_ENV, PW)
    monkeypatch.delenv(ds.CONTAINER_ENV, raising=False)

    def boom(*_a: object, **_k: object) -> None:
        raise AssertionError("세계를 만들면 안 된다")

    monkeypatch.setattr(ds, "build_demo_world", boom)
    assert ds.main(["--bind", ANY]) == 2
    monkeypatch.setenv(ds.PASSWORD_ENV, "short")
    assert ds.main([]) == 2
    err = capsys.readouterr().err
    assert "short" not in err  # 비밀번호 값은 출력하지 않는다


def test_password_minimum_and_fixed_demo_value_fits() -> None:
    assert isinstance(ds.parse_args([], {ds.PASSWORD_ENV: "x" * (ds.MIN_PASSWORD - 1)}), str)
    assert isinstance(ds.parse_args([], {}), str)
    assert len("kbj-demo-only") >= ds.MIN_PASSWORD  # README·데모 compose 의 고정 데모값(§11 Q3)


def test_at_from_env_and_flag_wins() -> None:
    a = ds.parse_args([], {**OK_ENV, ds.AT_ENV: "intraday"})
    assert not isinstance(a, str) and a.at == "intraday"
    b = ds.parse_args(["--at", "close"], {**OK_ENV, ds.AT_ENV: "intraday"})
    assert not isinstance(b, str) and b.at == "close"
    assert isinstance(ds.parse_args([], {**OK_ENV, ds.AT_ENV: "noon"}), str)


def test_demo_settings_keep_loopback_dev_mode() -> None:
    s = ds.demo_settings(PW, 8765)
    assert isinstance(s, Settings)
    assert s.api_host == "127.0.0.1" and s.web_cookie_secure is False
    assert s.public_base_url is None  # Origin = 요청 자신의 출처(127.0.0.1·localhost 둘 다)
    assert s.web_user == ds.DEMO_USER == "demo"
    assert PW not in repr(s)


def test_shift_rows_daily_and_monthly() -> None:
    at = datetime(2026, 11, 17, 6, 0, tzinfo=KST)
    daily = MacroSeriesRow(
        "FRED:DGS10", "2026-11-16", Decimal("4.1"), "%", "FRED", at, Quality.OK, released_at=at
    )
    rel = datetime(2026, 10, 5, 8, 0, tzinfo=KST)
    monthly = MacroSeriesRow(
        "KOSIS:DT_1J22003:cpi", "2026-09", Decimal("116"), "2020=100", "KOSIS", rel, Quality.OK,
        released_at=rel,
    )  # fmt: skip
    d, m = ds._shift_rows([daily, monthly], days=-63, months=-2)  # pyright: ignore[reportPrivateUsage]
    assert d.period == "2026-09-14" and d.as_of.date() == date(2026, 9, 15)
    assert date.fromisoformat(d.period).weekday() == date(2026, 11, 16).weekday()  # 요일 그대로
    assert m.period == "2026-07" and m.as_of == datetime(
        2026, 8, 5, 8, 0, tzinfo=KST
    )  # 공표일 5일 그대로
    assert m.value == monthly.value  # 합성 값은 그대로


def test_shift_rows_refuses_unknown_period_shape() -> None:
    at = datetime(2026, 11, 17, tzinfo=KST)
    q = MacroSeriesRow("ECOS:200Y001:gdp", "2026-Q3", Decimal("1"), "%", "ECOS", at, Quality.OK)
    with pytest.raises(ValueError, match="period"):
        ds._shift_rows([q], days=-63, months=-2)  # pyright: ignore[reportPrivateUsage]
