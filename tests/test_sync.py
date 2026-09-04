import inspect

from provider_directory.cli import build_parser
from provider_directory.sync import run_sync


def _plan(**overrides):
    base = {
        "window_start": 202308,
        "window_end": 202407,
        "prior_window_start": 202208,
        "prior_window_end": 202307,
        "warehouse_max_period": 202409,
        "warehouse_source": "az.period.period_code",
        "target_window_start": 202308,
        "target_window_end": 202407,
        "drop_periods": [],
        "add_periods": [],
        "slide_available": False,
        "get_reads_mart_only": True,
    }
    base.update(overrides)
    return base


def test_cli_sync_defaults_to_claims_clock():
    args = build_parser().parse_args(["sync", "--state", "AZ", "--dry-run"])
    assert args.cmd == "sync"
    assert args.state == "AZ"
    assert args.dry_run is True
    assert args.cms is False
    args = build_parser().parse_args(["sync", "--open-payments"])
    assert args.open_payments is True


def test_run_sync_never_calls_phase1():
    source = inspect.getsource(run_sync)
    assert "run_phase1(" not in source
    assert '"phase1": False' in source
    assert "slide=True" in source


def test_run_sync_claims_noop(monkeypatch):
    from provider_directory import sync as sync_mod

    monkeypatch.setattr(sync_mod, "_window_plan", lambda *a, **k: _plan())
    monkeypatch.setattr(sync_mod, "ensure_mart_database", lambda *a, **k: None)

    def boom(*_a, **_k):
        raise AssertionError("should not rebuild")

    monkeypatch.setattr(sync_mod, "run_phase6", boom)
    monkeypatch.setattr(sync_mod, "run_extras", boom)
    monkeypatch.setattr(sync_mod, "overlay_cms", boom)
    out = run_sync(object(), claims=True, mart_db="az_pd", claims_db="az", market_state="AZ")
    assert out["claims_action"] == "noop"
    assert out["phase1"] is False
    assert "extras" not in out


def test_run_sync_dry_run_slide(monkeypatch):
    from provider_directory import sync as sync_mod

    monkeypatch.setattr(
        sync_mod,
        "_window_plan",
        lambda *a, **k: _plan(slide_available=True, add_periods=[202408], drop_periods=[202308]),
    )

    def boom(*_a, **_k):
        raise AssertionError("dry-run must not write")

    monkeypatch.setattr(sync_mod, "run_phase6", boom)
    monkeypatch.setattr(sync_mod, "run_extras", boom)
    monkeypatch.setattr(sync_mod, "overlay_cms", boom)
    out = run_sync(
        object(),
        claims=True,
        dry_run=True,
        mart_db="az_pd",
        claims_db="az",
        market_state="AZ",
    )
    assert out["claims_action"] == "would_slide"
    assert out["dry_run"] is True
