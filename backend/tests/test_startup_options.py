from startup_options import packaged_ui_requested, resolve_use_ui


def test_source_defaults_to_headless():
    assert packaged_ui_requested([], frozen=False) is False
    assert resolve_use_ui(
        ui_requested=False,
        headless_requested=False,
        argv=[],
        frozen=False,
    ) is False


def test_frozen_build_defaults_to_ui():
    assert packaged_ui_requested([], frozen=True) is True
    assert resolve_use_ui(
        ui_requested=False,
        headless_requested=False,
        argv=[],
        frozen=True,
    ) is True


def test_headless_overrides_frozen_default_and_ui_flag():
    assert packaged_ui_requested(["--headless"], frozen=True) is False
    assert resolve_use_ui(
        ui_requested=True,
        headless_requested=True,
        argv=["--ui", "--headless"],
        frozen=True,
    ) is False


def test_explicit_ui_still_works_for_source_run():
    assert packaged_ui_requested(["--ui"], frozen=False) is True
    assert resolve_use_ui(
        ui_requested=True,
        headless_requested=False,
        argv=["--ui"],
        frozen=False,
    ) is True
