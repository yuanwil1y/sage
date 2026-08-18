"""轻量 Silero VAD ONNX 运行时。

项目只使用 Silero VAD 的逐帧概率接口。直接通过 ONNX Runtime 调用模型，
避免为了这个接口加载 ``torch``、``torchaudio`` 以及 silero-vad 的整套工具。
"""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import Any

import numpy as np

from paths import vad_model_path

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
FRAME_SIZE = 512
CONTEXT_SIZE = 64


def resolve_model_path(model_path: Path | str | None = None) -> Path:
    """Resolve the VAD model from an override, bundled resources, or dev install."""

    if model_path is not None:
        path = Path(model_path)
    else:
        path = vad_model_path()
    if path.exists():
        return path

    # Source checkouts created before the model became a first-class resource can
    # still run when silero-vad is installed.  ``find_spec`` locates its data
    # without importing the package (and therefore without importing torch).
    try:
        package_spec = importlib.util.find_spec("silero_vad")
    except (ImportError, ModuleNotFoundError, ValueError):
        package_spec = None
    if package_spec and package_spec.submodule_search_locations:
        package_model = (
            Path(next(iter(package_spec.submodule_search_locations)))
            / "data"
            / "silero_vad.onnx"
        )
        if package_model.exists():
            return package_model

    raise FileNotFoundError(
        "Silero VAD ONNX 模型不存在。请运行模型资源准备步骤，或设置 "
        f"VT_VAD_MODEL；已检查: {path}"
    )


class OnnxVadModel:
    """Stateful single-frame Silero VAD wrapper without a torch dependency."""

    def __init__(
        self,
        model_path: Path | str | None = None,
        *,
        session: Any | None = None,
    ) -> None:
        if session is None:
            try:
                import onnxruntime
            except ImportError as exc:  # pragma: no cover - environment error
                raise RuntimeError("VAD 需要安装 onnxruntime") from exc

            path = resolve_model_path(model_path)
            options = onnxruntime.SessionOptions()
            options.inter_op_num_threads = 1
            options.intra_op_num_threads = 1
            providers = ["CPUExecutionProvider"]
            available = onnxruntime.get_available_providers()
            if "CPUExecutionProvider" not in available:
                providers = available
            self._session = onnxruntime.InferenceSession(
                str(path),
                providers=providers,
                sess_options=options,
            )
            log.info("Silero VAD ONNX 模型已加载: %s", path)
        else:
            self._session = session

        self.reset_states()

    def reset_states(self, batch_size: int = 1) -> None:
        """Reset recurrent state and the 64-sample input context."""

        self._state = np.zeros((2, batch_size, 128), dtype=np.float32)
        self._context: np.ndarray | None = None
        self._last_sr = 0
        self._last_batch_size = 0

    def __call__(self, frame: np.ndarray, sampling_rate: int) -> float:
        audio = np.asarray(frame, dtype=np.float32)
        if audio.ndim == 1:
            audio = audio[None, :]
        if audio.ndim != 2:
            raise ValueError(f"VAD 输入必须是 1D/2D 音频，实际维度: {audio.ndim}")
        if audio.shape[1] != FRAME_SIZE:
            raise ValueError(
                f"VAD 每帧必须是 {FRAME_SIZE} samples，实际: {audio.shape[1]}"
            )
        if sampling_rate != SAMPLE_RATE:
            raise ValueError(f"VAD 只支持 {SAMPLE_RATE} Hz，实际: {sampling_rate}")

        batch_size = audio.shape[0]
        if self._last_batch_size != batch_size or self._last_sr not in (0, sampling_rate):
            self.reset_states(batch_size)
        if self._context is None:
            self._context = np.zeros((batch_size, CONTEXT_SIZE), dtype=np.float32)

        model_input = np.concatenate([self._context, audio], axis=1)
        outputs = self._session.run(
            None,
            {
                "input": model_input,
                "state": self._state,
                "sr": np.asarray(sampling_rate, dtype=np.int64),
            },
        )
        probability, self._state = outputs[0], np.asarray(outputs[1], dtype=np.float32)
        self._context = model_input[:, -CONTEXT_SIZE:]
        self._last_sr = sampling_rate
        self._last_batch_size = batch_size
        return float(np.asarray(probability).reshape(-1)[0])
