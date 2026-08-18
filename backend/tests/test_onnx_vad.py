from __future__ import annotations

import numpy as np
import pytest

from audio.onnx_vad import OnnxVadModel


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[dict[str, np.ndarray]] = []

    def run(self, _outputs: object, inputs: dict[str, np.ndarray]) -> list[np.ndarray]:
        self.calls.append(inputs)
        next_state = np.ones_like(inputs["state"])
        return [np.asarray([[0.75]], dtype=np.float32), next_state]


def test_onnx_vad_keeps_context_and_recurrent_state() -> None:
    session = FakeSession()
    model = OnnxVadModel(session=session)

    assert model(np.zeros(512, dtype=np.float32), 16000) == pytest.approx(0.75)
    assert model(np.zeros(512, dtype=np.float32), 16000) == pytest.approx(0.75)

    assert session.calls[0]["input"].shape == (1, 576)
    assert session.calls[1]["input"].shape == (1, 576)
    assert np.all(session.calls[1]["state"] == 1.0)
    assert session.calls[0]["sr"].shape == ()


def test_onnx_vad_rejects_wrong_frame_size() -> None:
    model = OnnxVadModel(session=FakeSession())

    with pytest.raises(ValueError, match="512"):
        model(np.zeros(256, dtype=np.float32), 16000)
