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
#include <wrl/implements.h>
#include <mmdeviceapi.h>
#include <audioclient.h>
#include <initguid.h>

#include <cstdio>
#include <cstdarg>
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
    : public Microsoft::WRL::RuntimeClass<
          Microsoft::WRL::RuntimeClassFlags<Microsoft::WRL::ClassicCom>,
          Microsoft::WRL::FtmBase,
          IActivateAudioInterfaceCompletionHandler> {
public:
    CActivateAudioInterfaceCompletionHandler() = default;

    // IActivateAudioInterfaceCompletionHandler
    STDMETHOD(ActivateCompleted)(IActivateAudioInterfaceAsyncOperation* operation) override {
        HRESULT activationResult = E_UNEXPECTED;
        HRESULT getResult = E_POINTER;
        IUnknown* punk = nullptr;
        if (operation != nullptr) {
            // These are two different HRESULTs: GetActivateResult reports
            // whether the result could be read, while activationResult is
            // the actual result of activating the audio interface.
            getResult = operation->GetActivateResult(&activationResult, &punk);
        }
        m_activationResult = FAILED(getResult) ? getResult : activationResult;
        if (SUCCEEDED(m_activationResult) && punk != nullptr) {
            HRESULT queryResult = punk->QueryInterface(IID_PPV_ARGS(&m_audioClient));
            if (FAILED(queryResult)) m_activationResult = queryResult;
        } else if (SUCCEEDED(m_activationResult)) {
            m_activationResult = E_NOINTERFACE;
        }
        if (punk != nullptr) {
            punk->Release();
        }
        ::SetEvent(m_completedEvent);
        // The async operation itself completed successfully; callers read the
        // actual activation HRESULT from m_activationResult. Returning a
        // failure here can make the COM completion machinery treat a handled
        // activation error as a callback failure.
        return S_OK;
    }

    void SetCompletionEvent(HANDLE ev) { m_completedEvent = ev; }
    IAudioClient* GetAudioClient() { return m_audioClient.Get(); }
    HRESULT ActivationResult() const { return m_activationResult; }

private:
    HANDLE m_completedEvent = nullptr;
    HRESULT m_activationResult = E_UNEXPECTED;
    ComPtr<IAudioClient> m_audioClient;
};

//===========================================================================
// PCM 输出
//===========================================================================

static void output_pcm16(const BYTE* data, uint32_t frames, DWORD flags) {
    const size_t bytes = static_cast<size_t>(frames) * 2 * sizeof(int16_t);  // stereo
    std::vector<uint8_t> silence;
    if (data == nullptr || (flags & AUDCLNT_BUFFERFLAGS_SILENT)) {
        // SILENT still advances the audio clock. Emit an equivalent amount of
        // zero PCM so the downstream resampler/VAD does not see a time jump.
        silence.assign(bytes, 0);
        data = silence.data();
    }
    size_t written = 0;
    while (written < bytes) {
        size_t n = fwrite(data + written, 1, bytes - written, stdout);
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

        if (flags & AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY) {
            log_msg("WASAPI 报告 DATA_DISCONTINUITY");
        }
        if (frames > 0) {
            output_pcm16(data, frames, flags);
        }

        hr = capture->ReleaseBuffer(frames);
        if (FAILED(hr)) {
            log_msg("ReleaseBuffer 失败: 0x%08x", hr);
            break;
        }
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
    if (completed == nullptr) {
        log_msg("创建激活完成事件失败: 0x%08x", GetLastError());
        return 1;
    }
    ComPtr<CActivateAudioInterfaceCompletionHandler> handler;
    hr = Microsoft::WRL::MakeAndInitialize<CActivateAudioInterfaceCompletionHandler>(
        &handler);
    if (FAILED(hr)) {
        log_msg("创建激活回调失败: 0x%08x", hr);
        CloseHandle(completed);
        return 1;
    }
    handler->SetCompletionEvent(completed);

    ComPtr<IActivateAudioInterfaceAsyncOperation> op;
    log_msg("激活进程 loopback，目标 PID=%lu", target_pid);
    hr = ActivateAudioInterfaceAsync(
        VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK,
        __uuidof(IAudioClient),
        &propvar,
        handler.Get(),
        &op);

    if (SUCCEEDED(hr)) {
        DWORD wait_result = WaitForSingleObject(completed, 10000);
        if (wait_result != WAIT_OBJECT_0) {
            log_msg("等待音频接口激活回调超时: 0x%08x", wait_result);
            CloseHandle(completed);
            return 1;
        }
    } else {
        log_msg("ActivateAudioInterfaceAsync 失败: 0x%08x", hr);
        CloseHandle(completed);
        return 1;
    }

    HRESULT activation_result = handler->ActivationResult();
    if (FAILED(activation_result)) {
        log_msg("ActivateAudioInterfaceAsync 完成但激活失败: 0x%08x", activation_result);
        CloseHandle(completed);
        return 1;
    }

    ComPtr<IAudioClient> client;
    if (handler->GetAudioClient()) {
        client = handler->GetAudioClient();
    }
    handler.Reset();
    CloseHandle(completed);

    if (!client) {
        log_msg("获取 IAudioClient 失败");
        return 1;
    }

    // 请求稳定的 16-bit PCM；AUTOCONVERTPCM 允许系统把设备混合格式
    // 转换到这个协议格式，避免把 float/声道布局差异传给 Python。
    WAVEFORMATEX mix_format{};
    mix_format.wFormatTag = WAVE_FORMAT_PCM;
    mix_format.nChannels = 2;
    mix_format.nSamplesPerSec = 44100;
    mix_format.wBitsPerSample = 16;
    mix_format.nBlockAlign = (mix_format.nChannels * mix_format.wBitsPerSample) / 8;
    mix_format.nAvgBytesPerSec = mix_format.nSamplesPerSec * mix_format.nBlockAlign;
    mix_format.cbSize = 0;

    hr = client->Initialize(
        AUDCLNT_SHAREMODE_SHARED,
        AUDCLNT_STREAMFLAGS_LOOPBACK
            | AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM
            | AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY,
        0, 0, &mix_format, nullptr);
    if (FAILED(hr)) {
        log_msg("Initialize(loopback) 失败: 0x%08x", hr);
        return 1;
    }

    hr = client->Start();
    if (FAILED(hr)) {
        log_msg("Start(loopback) 失败: 0x%08x", hr);
        return 1;
    }
    log_msg("开始捕获，输出 PCM s16le 44.1kHz stereo...");
    pump_audio(client.Get());
    client->Stop();

    op.Reset();
    CoUninitialize();
    log_msg("捕获结束");
    return 0;
}
