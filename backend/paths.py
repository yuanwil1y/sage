"""运行时路径解析（供源码和打包 exe 场景使用）。

内置资源（OCR、VAD、术语表）和用户模型（Hy-MT2、Whisper）分开管理。
用户模型默认放在用户数据目录，升级应用时不会被安装器覆盖；源码运行时
仍然允许使用仓库内的 ``backend/models`` 目录方便开发和测试。

优先级：
  1. 环境变量覆盖（VT_RUNTIME_DIR / VT_MODEL_DIR / VT_NATIVE_EXE / VT_RESOURCES）
  2. 冻结运行时：sys.executable 所在目录下的固定子目录
  3. 源码运行时：按项目仓库布局

源码布局：
  D:/Translator/
    backend/                    ← 代码 + models + resources
      models/hy-mt2/*.gguf
      models/asr/faster-whisper-medium/*
      resources/ocr/*
      resources/valorant_ja_zh.json
    runtime/llama.cpp/llama-server.exe
    native/audio-capture/build/Release/valorant_audio_capture.exe

打包布局（安装目录只放程序和内置资源）：
  <install>/                     ← exe 所在
    ValorantTranslator.exe
    runtime/llama.cpp/llama-server.exe
    native/valorant_audio_capture.exe
    resources/ocr/*
    resources/valorant_ja_zh.json

用户数据布局（打包运行时）：
  %LOCALAPPDATA%/ValorantTranslator/models/
    hy-mt2/*.gguf
    asr/faster-whisper-medium/*

安装目录与用户数据目录分开，卸载或升级程序不会删除已下载模型。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包的 exe 中。"""
    return getattr(sys, "frozen", False)


def _exe_dir() -> Path:
    """exe 所在目录（冻结时）；源码时返回 backend 目录。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    # 源码：本文件在 backend/ 下
    return Path(__file__).resolve().parent


def _repo_root() -> Path:
    """源码运行时的项目根（backend 的上一级）。"""
    return Path(__file__).resolve().parent.parent


def user_data_dir() -> Path:
    """用户级持久化目录，允许用 ``VT_DATA_DIR`` 覆盖。"""
    env = os.environ.get("VT_DATA_DIR")
    if env:
        return Path(env)
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "ValorantTranslator"
    return Path.home() / "AppData" / "Local" / "ValorantTranslator"


def runtime_dir() -> Path:
    """llama-server.exe 所在目录。"""
    env = os.environ.get("VT_RUNTIME_DIR")
    if env:
        return Path(env)
    if is_frozen():
        return _exe_dir() / "runtime" / "llama.cpp"
    return _repo_root() / "runtime" / "llama.cpp"


def config_dir() -> Path:
    """User configuration directory."""
    env = os.environ.get("VT_CONFIG_DIR")
    if env:
        return Path(env)
    if is_frozen():
        return user_data_dir() / "config"
    return _exe_dir() / "config"


def models_root() -> Path:
    """所有可下载模型的根目录。"""
    env = os.environ.get("VT_MODELS_DIR")
    if env:
        return Path(env)
    if is_frozen():
        return user_data_dir() / "models"
    return _exe_dir() / "models"


def model_dir() -> Path:
    """Hy-MT2 模型目录。"""
    env = os.environ.get("VT_MODEL_DIR")
    if env:
        return Path(env)
    return models_root() / "hy-mt2"


def asr_model_dir(model_size: str = "medium") -> Path:
    """faster-whisper 模型目录，默认不使用库的全局隐式缓存。"""
    env = os.environ.get("VT_ASR_MODEL_DIR")
    if env:
        return Path(env)
    return models_root() / "asr" / f"faster-whisper-{model_size}"


def native_exe_path() -> Path:
    """valorant_audio_capture.exe 路径。"""
    env = os.environ.get("VT_NATIVE_EXE")
    if env:
        return Path(env)
    if is_frozen():
        return _exe_dir() / "native" / "valorant_audio_capture.exe"
    return _repo_root() / "native" / "audio-capture" / "build" / "Release" / "valorant_audio_capture.exe"


def resources_dir() -> Path:
    """术语表等资源目录。"""
    env = os.environ.get("VT_RESOURCES")
    if env:
        return Path(env)
    return _exe_dir() / "resources"


def ocr_model_dir(model_name: str) -> Path:
    """内置 PaddleOCR 模型目录。"""
    return resources_dir() / "ocr" / model_name


def vad_model_path() -> Path:
    """Silero VAD ONNX 模型路径。"""
    env = os.environ.get("VT_VAD_MODEL")
    if env:
        return Path(env)
    return resources_dir() / "silero_vad.onnx"
