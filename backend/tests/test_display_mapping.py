import pytest

from screen.display_mapping import (
    legacy_desktop_region_to_output_local,
    output_local_region,
    parse_output_info,
    physical_size,
    resolve_output,
    resolve_output_index,
)


def test_parse_dxcam_output_info_and_physical_size():
    outputs = parse_output_info(
        "Device[0] Output[0]: Res:(2560, 1600) Rot:0 Primary:True\n"
        "Device[0] Output[1]: Res:(1920, 1080) Rot:0 Primary:False\n"
    )

    assert len(outputs) == 2
    assert physical_size((0, 0, 1707, 1067), 1.5) == (2560, 1600)
    assert outputs[0].primary is True


def test_output_local_region_never_includes_virtual_desktop_origin():
    # A selection at logical x=100..600 on a 150%-scaled screen becomes
    # 150..900 regardless of whether Windows placed that screen at +1920 or
    # -1707 on the virtual desktop.
    assert output_local_region((100, 50, 600, 250), 1.5) == (150, 75, 900, 375)


def test_legacy_virtual_offsets_convert_to_output_local():
    assert legacy_desktop_region_to_output_local(
        (2020, 100, 2520, 500), (1920, 0, 1920, 1080)
    ) == (100, 100, 600, 500)
    assert legacy_desktop_region_to_output_local(
        (-1600, 100, -1100, 500), (-1700, 0, 1700, 1000)
    ) == (100, 100, 600, 500)


def test_resolve_remaps_unique_monitor_after_output_order_changes():
    outputs = parse_output_info(
        "Device[0] Output[0]: Res:(1920, 1080) Rot:0 Primary:False\n"
        "Device[0] Output[1]: Res:(2560, 1600) Rot:0 Primary:True\n"
    )

    assert (
        resolve_output_index(
            outputs,
            saved_output_idx=0,
            expected_size=(2560, 1600),
            primary=True,
        )
        == 1
    )


def test_resolve_can_remap_to_nonzero_device():
    outputs = parse_output_info(
        "Device[0] Output[0]: Res:(1920, 1080) Rot:0 Primary:True\n"
        "Device[1] Output[0]: Res:(2560, 1440) Rot:0 Primary:False\n"
    )

    resolved = resolve_output(
        outputs,
        saved_device_idx=0,
        saved_output_idx=1,
        expected_size=(2560, 1440),
        primary=False,
    )
    assert (resolved.device_idx, resolved.output_idx) == (1, 0)


def test_resolve_preserves_saved_nonzero_device_when_valid():
    outputs = parse_output_info(
        "Device[0] Output[0]: Res:(1920, 1080) Rot:0 Primary:True\n"
        "Device[1] Output[0]: Res:(2560, 1440) Rot:0 Primary:False\n"
    )

    resolved = resolve_output(
        outputs,
        saved_device_idx=1,
        saved_output_idx=0,
        expected_size=(2560, 1440),
        primary=False,
    )
    assert resolved.device_idx == 1


def test_new_selection_does_not_trust_provisional_qt_index_when_ambiguous():
    outputs = parse_output_info(
        "Device[0] Output[0]: Res:(1920, 1080) Rot:0 Primary:False\n"
        "Device[1] Output[0]: Res:(1920, 1080) Rot:0 Primary:False\n"
    )

    # Even though the provisional Qt hint happens to point at Device[0]/0,
    # there are two equally valid physical outputs. A brand-new selection must
    # fail instead of persisting an arbitrary cross-GPU mapping.
    with pytest.raises(ValueError, match="多个"):
        resolve_output(
            outputs,
            saved_device_idx=0,
            saved_output_idx=0,
            expected_size=(1920, 1080),
            primary=False,
            prefer_saved=False,
        )


def test_resolve_rejects_ambiguous_or_missing_monitor():
    outputs = parse_output_info(
        "Device[0] Output[0]: Res:(1920, 1080) Rot:0 Primary:False\n"
        "Device[1] Output[0]: Res:(1920, 1080) Rot:0 Primary:False\n"
    )

    with pytest.raises(ValueError, match="不可用"):
        resolve_output(
            outputs,
            saved_device_idx=0,
            saved_output_idx=0,
            expected_size=(2560, 1440),
            primary=False,
        )

    with pytest.raises(ValueError, match="多个"):
        resolve_output(
            outputs,
            saved_device_idx=9,
            saved_output_idx=9,
            expected_size=(1920, 1080),
            primary=False,
        )
