"""PaddleOCR 识别封装（规格文档第 28 节）。

图像链路：DXcam BGR ndarray → OpenCV 2x upscale → PaddleOCR（lang="japan"）。
返回 rec_texts / rec_scores / rec_boxes。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, List, Sequence, Tuple

import numpy as np

from paths import ocr_model_dir

log = logging.getLogger(__name__)


class OcrInitializationError(RuntimeError):
    """A permanent OCR startup failure that should stop the polling worker."""


class OcrEngine:
    """PaddleOCR 封装，惰性初始化。"""

    def __init__(
        self,
        lang: str = "japan",
        use_onnx: bool = True,
        detector_model_dir: Path | str | None = None,
        recognizer_model_dir: Path | str | None = None,
    ) -> None:
        self._lang = lang
        self._use_onnx = use_onnx
        self._detector_model_dir = Path(detector_model_dir) if detector_model_dir else ocr_model_dir(
            "PP-OCRv6_medium_det_onnx"
        )
        self._recognizer_model_dir = Path(recognizer_model_dir) if recognizer_model_dir else ocr_model_dir(
            "PP-OCRv6_medium_rec_onnx"
        )
        self._ocr: Any = None
        self._initialization_error: OcrInitializationError | None = None

    def _ensure(self) -> Any:
        if self._ocr is not None:
            return self._ocr
        if self._initialization_error is not None:
            raise self._initialization_error

        try:
            from paddleocr import PaddleOCR

            if not self._use_onnx:
                raise ValueError("当前内置 OCR 模型仅支持 ONNX Runtime")
            required = (
                self._detector_model_dir / "inference.onnx",
                self._recognizer_model_dir / "inference.onnx",
            )
            missing = [str(path) for path in required if not path.exists()]
            if missing:
                raise FileNotFoundError(
                    "内置 PaddleOCR 模型缺失，请重新安装应用: " + ", ".join(missing)
                )

            # VALORANT 聊天 ROI 是固定、正向的屏幕区域，不需要文档方向、
            # 去畸变和文字行方向模型。显式传本地目录，禁止 PaddleOCR 访问网络。
            kwargs = {
                "device": "cpu",
                "engine": "onnxruntime",
                "text_detection_model_name": "PP-OCRv6_medium_det",
                "text_detection_model_dir": str(self._detector_model_dir),
                "text_recognition_model_name": "PP-OCRv6_medium_rec",
                "text_recognition_model_dir": str(self._recognizer_model_dir),
                "use_doc_orientation_classify": False,
                "use_doc_unwarping": False,
                "use_textline_orientation": False,
            }
            log.info(
                "初始化 PaddleOCR (lang=%s, engine=onnxruntime, local_models=%s)",
                self._lang,
                self._detector_model_dir.parent,
            )
            self._ocr = PaddleOCR(**kwargs)
        except Exception as exc:
            error = OcrInitializationError(
                "文字识别组件初始化失败，请重新安装 Sage；若问题仍然存在，请查看调试日志。"
            )
            self._initialization_error = error
            raise error from exc
        return self._ocr

    def recognize(self, image_bgr: np.ndarray) -> Tuple[List[str], List[float], List[Sequence[Sequence[float]]]]:
        """识别一张 BGR 图像，返回 (texts, scores, boxes)。"""
        import cv2

        # 2x upscale（规格第 28 节）
        upscaled = cv2.resize(
            image_bgr,
            None,
            fx=2.0,
            fy=2.0,
            interpolation=cv2.INTER_CUBIC,
        )

        ocr = self._ensure()
        # PaddleOCR 3.x：ocr() 返回 list[dict]，每个 dict 含 rec_texts/rec_scores/rec_boxes
        result = ocr.predict(upscaled)

        texts: List[str] = []
        scores: List[float] = []
        boxes: List[Sequence[Sequence[float]]] = []

        # 兼容多种返回结构
        if isinstance(result, list):
            for page in result:
                # 新版返回 OCRResult 对象（支持 dict 访问）
                r_texts = page["rec_texts"] if "rec_texts" in page else page.get("rec_texts", [])
                r_scores = page["rec_scores"] if "rec_scores" in page else page.get("rec_scores", [])
                r_boxes = page["rec_boxes"] if "rec_boxes" in page else []
                r_polys = page["rec_polys"] if "rec_polys" in page else []

                for i, t in enumerate(r_texts):
                    texts.append(str(t))
                    scores.append(float(r_scores[i]) if i < len(r_scores) else 0.0)
                    # box：优先用 rec_polys（完整四边形），否则用 rec_boxes（4值矩形）
                    if i < len(r_polys):
                        poly = [list(map(float, pt)) for pt in r_polys[i]]
                        box_rescaled = [[p[0] / 2.0, p[1] / 2.0] for p in poly]
                    elif i < len(r_boxes):
                        box = r_boxes[i]
                        # rec_boxes 是 4 值矩形 [x1,y1,x2,y2] → 转四边形
                        x1, y1, x2, y2 = [float(v) for v in box]
                        box_rescaled = [
                            [x1 / 2.0, y1 / 2.0],
                            [x2 / 2.0, y1 / 2.0],
                            [x2 / 2.0, y2 / 2.0],
                            [x1 / 2.0, y2 / 2.0],
                        ]
                    else:
                        box_rescaled = [[0.0, 0.0], [0.0, 0.0], [0.0, 0.0], [0.0, 0.0]]
                    boxes.append(box_rescaled)

        return texts, scores, boxes
