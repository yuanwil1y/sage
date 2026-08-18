// valorant_audio_capture — Windows Process Loopback Helper
//
// 捕获指定进程（及其子进程树）的音频渲染输出，以固定 PCM 格式写到 stdout：
//   PCM signed 16-bit LE / 44100 Hz / stereo / interleaved
// stderr 用于日志。
//
// 用法：
//   valorant_audio_capture.exe --mode process --pid <PID>
//
// 依据规格文档第 20~21 节，参考官方 ApplicationLoopback 示例。
// 需要 Windows 10 Build 20348+（推荐 Windows 11）。

#include <windows.h>
#include <wrl/client.h>
#include <mmdeviceapi.h>
#include <audioclient.h>
#include <initguid.h>

#include <cstdio>
#include <cstdint>
#include <string>
#include <vector>
#include <atomic>
#include <fcntl.h>
#include <io.h>

using Microsoft::WRL::ComPtr;

//===========================================================================
// Process Loopback 激活参数（手动声明，避免 WINAPI_FAMILY 分区限制）
// 与官方 audioclientactivationparams.h 一致。
//===========================================================================

typedef enum PROCESS_LOOPBACK_MODE {
    PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE = 0,
    PROCESS_LOOPBACK_MODE_EXCLUDE_TARGET_PROCESS_TREE = 1,
} PROCESS_LOOPBACK_MODE;

typedef struct AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS {
    DWORD TargetProcessId;
    PROCESS_LOOPBACK_MODE ProcessLoopbackMode;
} AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS;

typedef enum AUDIOCLIENT_ACTIVATION_TYPE {
    AUDIOCLIENT_ACTIVATION_TYPE_DEFAULT = 0,
    AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK = 1,
} AUDIOCLIENT_ACTIVATION_TYPE;

typedef struct AUDIOCLIENT_ACTIVATION_PARAMS {
    AUDIOCLIENT_ACTIVATION_TYPE ActivationType;
    union {
        AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS ProcessLoopbackParams;
    } DUMMYUNIONNAME;
} AUDIOCLIENT_ACTIVATION_PARAMS;

#ifndef VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK
#define VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK L"VAD\\Process_Loopback"
#endif

static std::atomic<bool> g_running{true};

static void log_msg(const char* fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    fputc('\n', stderr);
    fflush(stderr);
}

//===========================================================================
// IActivateAudioInterfaceCompletionHandler 实现（含 IAgileObject）
//===========================================================================

class CActivateAudioInterfaceCompletionHandler final
    : public IActivateAudioInterfaceCompletionHandler,
      public IAgileObject {
public:
    CActivateAudioInterfaceCompletionHandler() = default;

    // IActivateAudioInterfaceCompletionHandler
    STDMETHOD(ActivateCompleted)(IActivateAudioInterfaceAsyncOperation* operation) override {
        HRESULT hr = S_OK;
        IUnknown* punk = nullptr;
        hr = operation->GetActivateResult(&hr, &punk);
        if (SUCCEEDED(hr) && punk != nullptr) {
            hr = punk->QueryInterface(IID_PPV_ARGS(&m_audioClient));
            punk->Release();
        }
        SetEvent(m_completedEvent);
        return hr;
    }

    // IUnknown
    STDMETHOD_(ULONG, AddRef)() override {
        return InterlockedIncrement(&m_refCount);
    }
    STDMETHOD_(ULONG, Release)() override {
        ULONG v = InterlockedDecrement(&m_refCount);
        if (v == 0) {
            delete this;
        }
        return v;
    }
    STDMETHOD(QueryInterface)(REFIID riid, void** ppv) override {
        if (ppv == nullptr) return E_POINTER;
        if (riid == IID_IUnknown ||
            riid == __uuidof(IActivateAudioInterfaceCompletionHandler) ||
            riid == __uuidof(IAgileObject)) {
            AddRef();
            *ppv = static_cast<IActivateAudioInterfaceCompletionHandler*>(this);
            return S_OK;
        }
        *ppv = nullptr;
        return E_NOINTERFACE;
    }

    void SetEvent(HANDLE ev) { m_completedEvent = ev; }
    IAudioClient* GetAudioClient() { return m_audioClient.Get(); }

private:
    ULONG m_refCount = 1;
    HANDLE m_completedEvent = nullptr;
    ComPtr<IAudioClient> m_audioClient;
};

//===========================================================================
// PCM 输出
//===========================================================================

static void output_float32_to_pcm16(const float* data, uint32_t frames) {
    const size_t sample_count = static_cast<size_t>(frames) * 2;  // stereo
    std::vector<int16_t> pcm(sample_count);
    for (size_t i = 0; i < sample_count; ++i) {
        float v = data[i];
        if (v > 1.0f) v = 1.0f;
        if (v < -1.0f) v = -1.0f;
        pcm[i] = static_cast<int16_t>(v * 32767.0f);
    }
    const size_t bytes = pcm.size() * sizeof(int16_t);
    const uint8_t* out = reinterpret_cast<const uint8_t*>(pcm.data());
    size_t written = 0;
    while (written < bytes) {
        size_t n = fwrite(out + written, 1, bytes - written, stdout);
        if (n == 0) break;
        written += n;
    }
    fflush(stdout);
}

static void pump_audio(IAudioClient* audio_client) {
    ComPtr<IAudioCaptureClient> capture;
    HRESULT hr = audio_client->GetService(IID_PPV_ARGS(&capture));
    if (FAILED(hr)) {
        log_msg("GetService(IAudioCaptureClient) 失败: 0x%08x", hr);
        return;
    }

    while (g_running) {
        UINT32 packet_length = 0;
        hr = capture->GetNextPacketSize(&packet_length);
        if (FAILED(hr)) {
            log_msg("GetNextPacketSize 失败: 0x%08x", hr);
            break;
        }
        if (packet_length == 0) {
            Sleep(10);
            continue;
        }

        BYTE* data = nullptr;
        UINT32 frames = 0;
        DWORD flags = 0;
        hr = capture->GetBuffer(&data, &frames, &flags, nullptr, nullptr);
        if (FAILED(hr)) {
            log_msg("GetBuffer 失败: 0x%08x", hr);
            break;
        }

        if (frames > 0 && data != nullptr && !(flags & AUDCLNT_BUFFERFLAGS_SILENT)) {
            output_float32_to_pcm16(reinterpret_cast<const float*>(data), frames);
        }

        capture->ReleaseBuffer(frames);
    }
}

//===========================================================================
// 入口
//===========================================================================

int wmain(int argc, wchar_t** argv) {
    DWORD target_pid = 0;
    for (int i = 1; i < argc; ++i) {
        std::wstring a = argv[i];
        if (a == L"--pid" && i + 1 < argc) {
            target_pid = static_cast<DWORD>(_wtoi(argv[++i]));
        }
    }
    if (target_pid == 0) {
        log_msg("用法: valorant_audio_capture.exe --mode process --pid <PID>");
        return 1;
    }

    _setmode(_fileno(stdout), _O_BINARY);

    HRESULT hr = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    if (FAILED(hr)) {
        log_msg("CoInitializeEx 失败: 0x%08x", hr);
        return 1;
    }

    // 构造 process loopback 激活参数
    AUDIOCLIENT_ACTIVATION_PARAMS params{};
    params.ActivationType = AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK;
    params.ProcessLoopbackParams.TargetProcessId = target_pid;
    params.ProcessLoopbackParams.ProcessLoopbackMode =
        PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE;

    PROPVARIANT propvar{};
    PropVariantInit(&propvar);
    propvar.vt = VT_BLOB;
    propvar.blob.cbSize = sizeof(params);
    propvar.blob.pBlobData = reinterpret_cast<BYTE*>(&params);

    HANDLE completed = CreateEvent(nullptr, FALSE, FALSE, nullptr);
    auto* handler = new (std::nothrow) CActivateAudioInterfaceCompletionHandler();
    if (handler == nullptr) {
        log_msg("内存分配失败");
        return 1;
    }
    handler->SetEvent(completed);

    ComPtr<IActivateAudioInterfaceAsyncOperation> op;
    log_msg("激活进程 loopback，目标 PID=%lu", target_pid);
    hr = ActivateAudioInterfaceAsync(
        VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK,
        __uuidof(IAudioClient),
        &propvar,
        handler,
        &op);

    if (SUCCEEDED(hr)) {
        WaitForSingleObject(completed, INFINITE);
    } else {
        log_msg("ActivateAudioInterfaceAsync 失败: 0x%08x", hr);
        handler->Release();
        return 1;
    }

    ComPtr<IAudioClient> client;
    if (handler->GetAudioClient()) {
        client = handler->GetAudioClient();
    }
    handler->Release();

    if (!client) {
        log_msg("获取 IAudioClient 失败");
        return 1;
    }

    // 混合格式：44.1kHz float32 stereo
    WAVEFORMATEX mix_format{};
    mix_format.wFormatTag = WAVE_FORMAT_IEEE_FLOAT;
    mix_format.nChannels = 2;
    mix_format.nSamplesPerSec = 44100;
    mix_format.wBitsPerSample = 32;
    mix_format.nBlockAlign = (mix_format.nChannels * mix_format.wBitsPerSample) / 8;
    mix_format.nAvgBytesPerSec = mix_format.nSamplesPerSec * mix_format.nBlockAlign;
    mix_format.cbSize = 0;

    hr = client->Initialize(
        AUDCLNT_SHAREMODE_SHARED,
        AUDCLNT_STREAMFLAGS_LOOPBACK,
        0, 0, &mix_format, nullptr);
    if (FAILED(hr)) {
        log_msg("Initialize(loopback) 失败: 0x%08x", hr);
        return 1;
    }

    client->Start();
    log_msg("开始捕获，输出 PCM s16le 44.1kHz stereo...");
    pump_audio(client.Get());
    client->Stop();

    op.Reset();
    CoUninitialize();
    log_msg("捕获结束");
    return 0;
}
