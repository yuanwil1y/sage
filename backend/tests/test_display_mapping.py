from screen.display_mapping import (
    parse_output_info,
    physical_size,
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


def test_resolve_rejects_ambiguous_or_missing_monitor():
    outputs = parse_output_info(
        "Device[0] Output[0]: Res:(1920, 1080) Rot:0 Primary:False\n"
        "Device[0] Output[1]: Res:(1920, 1080) Rot:0 Primary:False\n"
    )

    try:
        resolve_output_index(
            outputs,
            saved_output_idx=0,
            expected_size=(2560, 1440),
            primary=False,
        )
    except ValueError as exc:
        assert "不可用" in str(exc)
    else:
        raise AssertionError("missing monitor should be rejected")

    try:
        resolve_output_index(
            outputs,
            saved_output_idx=2,
            expected_size=(1920, 1080),
            primary=False,
        )
    except ValueError as exc:
        assert "多个" in str(exc)
    else:
        raise AssertionError("ambiguous monitor should be rejected")
