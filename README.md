# Sage

Sage 是面向 Windows 的 VALORANT 日服实时翻译工具：把游戏中的队友语音和左下角聊天文字转换成中文，并通过 Xbox Game Bar 小组件覆盖显示在游戏画面上。

ASR、OCR 和翻译都在本机运行。运行阶段不需要云端翻译 API，也不会读取游戏内存、拦截网络、注入代码或自动操作游戏。

## 功能

- 语音聊天：进程音频采集、Silero VAD、本地 Whisper 日语识别和中文翻译。
- 文字聊天：屏幕区域捕获、PaddleOCR 日文识别、去重、拼行和中文翻译。
- 本地翻译：使用 Hy-MT2 GGUF，通过本地 llama.cpp 运行时推理。
- 游戏内显示：透明、可固定、支持鼠标穿透的 Sage Game Bar 小组件。
- 中文控制台：实时状态、语音聊天、文字聊天、模型中心和调试日志页面。
- 完整离线运行：大模型由用户在模型中心下载；下载完成后运行时不需要网络。

## 工作方式

```text
VALORANT 音频
    ↓ Process Loopback → VAD → Whisper ASR
    ┐
    ├→ 本地翻译 → Named Pipe → MSIX 本地服务 → HTTP 长轮询 → Sage Game Bar Widget
    ┘
VALORANT 聊天区域
    ↓ DXcam → PaddleOCR → 拼行/去重
```

主要目录：

| 目录 | 内容 |
|---|---|
| `backend/` | Python 后端、中文 GUI、OCR、语音、翻译和模型管理 |
| `native/audio-capture/` | Windows Process Loopback 音频采集模块 |
| `gamebar-widget/` | C# Game Bar 小组件、同包本地服务和通信代码 |
| `runtime/` | 本地推理运行时的准备目录 |

## 普通用户

发布版应使用 Sage 的 Inno Setup 安装器。安装器会自动完成 Game Bar 小组件的证书、依赖、MSIX 和本机通信权限配置；用户不需要手动导入证书、开启开发者模式或运行 PowerShell。安装完成后按 `Win + G`，在小组件列表中打开并固定 Sage 即可。

安装器不会携带签名私钥。卸载时会同时移除 Sage 小组件和对应证书，并询问是否保留用户下载的模型与配置。

## 模型

模型二进制不提交到 Git 仓库。这样可以保持源码仓库可维护，也避免把第三方权重和私钥类发布材料混入源码。

### 安装包内置资源

- OCR：PP-OCRv6 medium det/rec ONNX 模型，构建安装器时放入：
  - `backend/resources/ocr/PP-OCRv6_medium_det_onnx/`
  - `backend/resources/ocr/PP-OCRv6_medium_rec_onnx/`
- VAD：`backend/resources/silero_vad.onnx`

OCR 模型来源：[检测模型](https://huggingface.co/PaddlePaddle/PP-OCRv6_medium_det_onnx/tree/61323801669c338b7891481ec7bac61ce31b576a2)、[识别模型](https://huggingface.co/PaddlePaddle/PP-OCRv6_medium_rec_onnx/tree/50c7eacafc52fa7bcf4194e8cd08e46f8558504b)。每个目录需要准备上游的 `inference.json`、`inference.onnx` 和 `inference.yml`。

VAD 模型来自 [snakers4/silero-vad](https://github.com/snakers4/silero-vad/tree/master/src/silero_vad/data)。

### 安装后由模型中心下载

- 翻译模型：[`tencent/Hy-MT2-1.8B-GGUF`](https://huggingface.co/tencent/Hy-MT2-1.8B-GGUF)，使用 `Q4_K_M` 量化文件，约 1.13GB。
- 语音识别模型：[`Systran/faster-whisper-medium`](https://huggingface.co/Systran/faster-whisper-medium)，约 1.53GB。

模型中心支持下载、校验、删除，以及从离线目录导入。未下载模型时，Sage 不会偷偷联网下载。

## 从源码运行

开发和运行环境：

- Windows 10/11 x64
- Python 3.11 x64
- 语音功能需要已构建的 `native/audio-capture` 模块
- 重新构建 Game Bar 小组件需要 Visual Studio 2022 的 UWP 工作负载和 Windows SDK

安装 Python 依赖并启动中文 GUI：

```powershell
python -m pip install -r backend\requirements.txt
cd backend
python main.py --ui
```

首次使用时，在“文字聊天”页面选择 VALORANT 左下角聊天区域。配置会保存在用户配置目录，不会写入源码目录。

## 构建

### 1. 构建 Game Bar 小组件

使用 Visual Studio / Windows SDK 构建 x64 小组件。用于最终安装器的构建必须使用与 MSIX 清单 Publisher 匹配的代码签名证书：

```powershell
.\gamebar-widget\build.ps1 -Sign `
  -PfxPath C:\path\to\SageWidget.pfx `
  -PfxPassword '<PFX 密码>' `
  -CertificatePath C:\path\to\SageWidget.cer
```

`.pfx` 只在本机用于签名，不能提交到 Git。构建脚本会把公开 `.cer` 放入待发布的小组件目录，供安装器自动导入。

### 2. 构建 Full 程序目录

Sage 目前只发布 Full 完整版，不再维护 text / voicechat 分拆安装包：

```powershell
cd backend
python build_package.py
```

### 3. 构建 Inno Setup 安装器

安装 Inno Setup 6 或更高版本后执行：

```powershell
cd backend
python build_installer.py
```

安装器输出到 `backend/dist/installer/Sage_Setup.exe`。打包前请确认 OCR/VAD 资源和已签名的 x64 Game Bar MSIX 已准备好；这些生成物不会进入 Git 仓库。

## 测试

```powershell
cd backend
pytest -q
```

OCR、语音采集和 Game Bar 的真实运行效果仍需要在 Windows 游戏环境中回归验证；单元测试覆盖配置、进程间通信、OCR 处理、GUI 控制和小组件服务协议。

## 第三方项目与资源

Sage 的 Python、C++、C#、安装器脚本和 UI 代码由本项目编写。以下项目用于运行、构建或实现相关能力：

- [llama.cpp](https://github.com/ggml-org/llama.cpp)：本地 GGUF 推理运行时。
- [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR)：聊天 OCR。
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) 和 [CTranslate2](https://github.com/OpenNMT/CTranslate2)：日语语音识别。
- [DXcam](https://github.com/ra1nty/DXcam)、[OpenCV](https://github.com/opencv/opencv) 和 [ONNX Runtime](https://github.com/microsoft/onnxruntime)：屏幕捕获、图像处理和 ONNX 推理。
- [PySide6](https://doc.qt.io/qtforpython-6/)：桌面 GUI。
- [PyInstaller](https://github.com/pyinstaller/pyinstaller) 和 [Inno Setup](https://github.com/jrsoftware/issrc)：Windows 程序目录与安装器构建。
- [Xbox Game Bar SDK](https://www.nuget.org/packages/Microsoft.Gaming.XboxGameBar/) 和 [.NET UWP](https://www.nuget.org/packages/Microsoft.NETCore.UniversalWindowsPlatform/)：Game Bar 小组件。
- [CS2KillConfirmOverlay](https://github.com/eachkinji/CS2KillConfirmOverlay)：Game Bar 小组件激活、全权限本地服务、Loopback Exempt、HTTP 长轮询和管理员安装流程的实现参考。该项目使用 AGPL-3.0，Sage 的字幕业务协议和代码需独立维护，分发前应核对各依赖的许可证义务。

模型和第三方运行时二进制请以各上游项目的许可证和发布页面为准。

## 合规边界

Sage 只处理玩家本来已经能听到或看到的语音和聊天文字，不读取游戏内存、不拦截网络、不注入代码、不自动操作游戏。使用者应自行确认当地法律、游戏规则和 Riot/Vanguard 政策要求。
