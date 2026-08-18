# Sage Codex Handover

This document is the handoff point for continuing the repository review/fixes on a Windows machine.

## Working context

- Repository: `yuanwil1y/sage`
- Base branch: `main`
- Current handoff branch: `agent/python-pipeline-hardening`
- Draft PR: `#17` — **Harden Python streaming and worker pipelines**
- Base commit reviewed: `a4f5027781368cf98eb0e5189bdd3c989e2c3c3d`

Start by checking out `agent/python-pipeline-hardening` and reviewing PR #17 rather than redoing the Python-side fixes from `main`.

## Issues implemented on this branch

PR #17 is intended to close these issues after review/merge:

- **#3 — Decouple PCM capture, ASR and translation**
  - ASR and translation no longer run on producer/capture callbacks.
  - Added bounded queues and explicit drop-oldest behavior.
  - ASR/translation/IPC exceptions are isolated per message.
  - Shutdown drains ASR before stopping translation so already queued utterances are not lost.

- **#4 — Preserve PCM and VAD remainder across arbitrary chunk boundaries**
  - PCM byte framing now carries 0–3 leftover bytes across calls.
  - VAD carries sub-512-sample remainder across calls.
  - Randomized chunk-boundary regression coverage added.

- **#10 — Reset and flush streaming audio state across capture restart**
  - soxr stream can be flushed/reset.
  - VAD/Silero recurrent state is reset between streams.
  - Final partial-stream semantics are covered by tests.

- **#11 — Decouple OCR polling from translation / live `poll_hz`**
  - OCR producer no longer waits for Hy-MT2 translation.
  - Running worker uses updated `poll_hz` without restart.

- **#12 — Activate downloaded/replaced models without restarting Sage**
  - Added `backend/runtime_models.py`.
  - Watches Hy-MT2 / faster-whisper local model state and file fingerprints.
  - Hy-MT2 install/replacement restarts the local server and atomically swaps the translator.
  - Hy-MT2 removal degrades to passthrough.
  - Whisper file changes reset its lazy model so the next utterance loads current disk state.
  - Transient filesystem replacement races are not treated as deletion.

- **#14 — Rework OCR deduplication**
  - Removed the 30-second global semantic suppression behavior.
  - Dedup is based on visible chat-window continuity/OCR jitter instead.
  - A legitimate repeated callout can be emitted again when it appears as a new row.

## Files changed by PR #17

The branch intentionally stays on the Python/test side. Key files include:

- `backend/audio/normalize.py`
- `backend/audio/resample.py`
- `backend/audio/vad.py`
- `backend/audio/pipeline.py`
- `backend/audio/transcriber.py`
- `backend/pipeline/orchestrator.py`
- `backend/ocr/chat_worker.py`
- `backend/ocr/dedup.py`
- `backend/runtime_models.py`
- `backend/main.py`
- corresponding tests under `backend/tests/`

Do not assume the implementation is identical to the original synchronous architecture when working on downstream Windows components; inspect the current branch first.

## Validation already done

The available review environment was Linux, so Windows-native end-to-end execution was not possible. The Python/algorithm side was validated with regression/fault-injection coverage for:

- randomized PCM byte chunking
- randomized VAD sample chunking
- resampler flush/reset
- final partial VAD tail handling
- slow ASR not blocking the producer callback
- slow translation not blocking chat/OCR producer callbacks
- translator failure followed by successful later messages
- graceful ASR -> translation shutdown/drain ordering
- live OCR `poll_hz` updates
- visible-window OCR dedup / OCR jitter / scrolling / repeated valid callouts
- Hy-MT2 install/replacement/delete activation behavior
- Whisper runtime reset
- transient model-replacement watcher states

Before merging PR #17, run the relevant test suite on Windows as well, especially the orchestrator/audio/model lifecycle tests.

## Windows follow-up: highest priority

These issues were deliberately **not** changed in PR #17 because they depend on Windows APIs, Game Bar/UWP behavior, or real display/audio devices.

### Native audio / WASAPI

#### #2 — Preserve WASAPI silent packets

Current native helper drops `AUDCLNT_BUFFERFLAGS_SILENT` packets. A silent packet still represents real elapsed time and should produce the corresponding zero PCM frames; dropping it compresses the VAD timeline.

Recommended Windows validation:

1. Patch `native/audio-capture/main.cpp` so SILENT outputs zero-valued frames of the same duration.
2. Log/observe `AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY`.
3. Build the helper on Windows.
4. Test `speech -> >800 ms silence -> speech` and verify two utterances are produced with default VAD settings.

#### #9 — Harden WASAPI process-loopback activation / format negotiation

Review the helper against Microsoft's current ApplicationLoopback sample.

Important points:

- keep `ActivateAudioInterfaceAsync` call HRESULT separate from activation-result HRESULT from `GetActivateResult`
- improve activation / `IAudioClient::Initialize` diagnostics
- evaluate/use `AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM` for the canonical requested format
- validate 44.1 kHz and 48 kHz endpoints
- test at least a normal endpoint plus USB/virtual audio if available

#2 and #9 should ideally be handled together because they touch the same native helper and test matrix.

### Windows Named Pipe / Game Bar transport

#### #6 — Make `PipeServer` connect wait cancellable

`CreateNamedPipe(... nDefaultTimeOut=...)` does not make synchronous blocking `ConnectNamedPipe(handle, None)` periodically time out. Verify and replace with a genuinely cancellable/overlapped strategy.

Acceptance test: start server with no client, call `stop()`, and assert the server thread is actually dead.

#### #7 — Detect stale pipe clients before sacrificing the next subtitle

Production GUI does not continuously send the heartbeat that the existing reconnect test uses to force stale-client detection. The first real subtitle after a service/widget disconnect can become the failed probe and be lost.

Acceptance test: connect -> disconnect -> reconnect -> send exactly one subtitle; the new client must receive that first subtitle.

#6 and #7 should ideally be designed together because both concern pipe lifecycle/reconnect semantics.

#### #5 — Reset Game Bar event cursor across `SageWidgetService` restart

The service event IDs restart from zero while the Widget keeps its old `_cursor`. Add a service session/epoch UUID (preferred) or equivalent restart identity so the Widget resets its numeric cursor when the service instance changes.

Acceptance test: consume events, restart `SageWidgetService`, publish one event, and ensure that first event is displayed immediately.

Note: changing HTTP long polling to WebSocket is **not required** to solve #5. Correct session/sequence/reconnect semantics are still necessary with WebSocket.

### Native helper supervision

#### #8 — Restart `valorant_audio_capture.exe` after unexpected exit

The Python supervisor side was not included in PR #17. On Windows, implement and verify:

- detect helper exit/stdout EOF
- capture useful stderr/exit diagnostics instead of discarding everything
- restart with bounded backoff while the same VALORANT process remains valid
- reset the audio stream state before the replacement helper starts feeding data
- do not restart after intentional shutdown

Acceptance test: kill the helper while VALORANT remains running; voice translation should recover without restarting VALORANT.

### DXcam / multi-monitor / DPI

#### #13 — Explicit capture format and robust monitor mapping

Validate on real Windows displays:

- explicitly request the OCR color format from DXcam (BGR if that remains the contract)
- do not assume Qt screen ordering equals DXcam/DXGI output ordering
- verify mixed-DPI and secondary-monitor ROI coordinate conversion
- verify negative desktop coordinates / monitor topology changes

At minimum test a secondary monitor with non-100% scaling. Multi-GPU testing is desirable when available.

## Lower-priority Windows/release follow-up

### #15 — Regression/soak/CI coverage

PR #17 adds several Python regression tests, but #15 is not fully closed. Remaining useful Windows work:

- Windows CI job for pywin32 / pipe tests where feasible
- service restart/cursor integration test
- native-helper kill/recovery test
- 30–60 minute real VALORANT JP-server soak test
- record p50/p95/p99 latency, RSS, handles, thread count
- test game restart, alt-tab, Widget close/reopen, audio-device changes, resolution/DPI changes

### #16 — Release build gates / metadata / capability checks

Still untouched in PR #17. Review on Windows:

- installer still references the old repository URL
- installer/MSIX version metadata is inconsistent
- a production Full build should fail if required Widget/native assets are missing
- install/runtime should clearly gate unsupported Windows builds for process loopback
- validate Inno Setup + MSIX signing/install/uninstall end to end

## Important architectural expectations after PR #17

The intended Python data flow is now approximately:

```text
Native helper stdout
    -> PCM byte carry / normalize
    -> resample / VAD sample carry
    -> bounded ASR queue
    -> ASR worker

OCR worker
    -> source-message queue

ASR output
    -> source-message queue

source-message queue
    -> translation worker
    -> PipeServer
```

Do **not** move Whisper/Hy-MT2 inference back onto the native stdout reader or OCR polling thread while implementing Windows fixes.

Queues are intentionally bounded to prevent unlimited latency/memory growth under overload. Preserve that principle when wiring helper restart or downstream IPC recovery.

## Suggested order for Windows Codex work

1. **#2 + #9** — native WASAPI helper correctness/compatibility
2. **#6 + #7** — Named Pipe lifecycle/reconnect
3. **#5** — Game Bar service session/cursor recovery
4. **#8** — native helper supervision/restart
5. **#13** — multi-monitor / DPI / DXcam validation
6. **#15** — Windows regression + soak tests
7. **#16** — release/installer hardening

After each block, add regression coverage before moving to the next block.

## Merge guidance

- Keep PR #17 as Draft until Windows tests have at least exercised the changed Python pipeline against the real native helper/service stack.
- Windows-specific fixes can be made in separate branches/PRs to keep review scope understandable.
- If Windows validation reveals a bug in PR #17, patch `agent/python-pipeline-hardening` (or a branch based on it) rather than reverting to the original synchronous behavior.
- Do not close #2/#5/#6/#7/#8/#9/#13/#15/#16 merely because PR #17 exists; they remain separate work.
