# Codex Windows Prep Handover

This branch is stacked on `agent/python-pipeline-hardening` / draft PR #17.
Read `CODEX_HANDOVER.md` first for the original review context, then use this
file for the additional code already prepared for Windows validation.

## Branch / scope

- Branch: `agent/windows-prep-followups`
- Base: `agent/python-pipeline-hardening`
- This branch prepares code for issues **#8, #15, #16**.
- These issues should remain open until the Windows acceptance tests below pass.
- Do not redo the Python pipeline fixes from PR #17.

## #8 — native audio helper supervision

Implemented in:

- `backend/audio/capture_reader.py`
- `backend/audio/pipeline.py`
- `backend/tests/test_capture_reader.py`

Behavior now prepared:

- stderr is drained instead of sent to `DEVNULL`
- recent stderr is retained in a bounded in-memory tail
- unexpected stdout EOF/process exit triggers supervised restart
- restart uses bounded exponential backoff
- a run that survives `restart_stable_seconds` resets the failure counter
- intentional `stop()` sets the stop event and does not restart
- exceptions from `on_pcm` are isolated so the stdout reader survives
- immediately before a replacement helper starts, the reader emits an internal
  `b""` stream-boundary marker
- `AudioPipeline.feed_pcm(b"")` resets PCM carry, soxr, VAD/pre-roll and Silero
  recurrent state without emitting a partial utterance
- custom test helpers do not enforce the real Windows capability gate

Windows acceptance still required:

1. Run VALORANT and confirm normal process loopback capture.
2. Kill `valorant_audio_capture.exe` only.
3. Confirm logs show the helper exit code and useful stderr.
4. Confirm restart is rate-limited and the helper comes back automatically.
5. Confirm voice subtitles resume without restarting VALORANT.
6. Repeat helper failures several times and confirm no runaway process/thread leak.
7. Exit VALORANT / stop Sage and confirm the supervisor does not resurrect the helper.
8. Change/disable an audio endpoint if possible and verify diagnostics/recovery.

## #15 — regression / CI groundwork

Implemented:

- `.github/workflows/tests.yml`
- `backend/pytest.ini`
- additional supervision/release tests
- `backend/tools/windows_soak.py`

CI is intentionally split:

- Ubuntu portable regression job for streaming/VAD/OCR/model/release logic
- Windows job for Python supervision/orchestrator/pywin32-safe tests
- tests that require a built native helper or MSIX use the
  `windows_integration` marker instead of being silently skipped as ordinary
  unit tests

`windows_soak.py` collects raw samples plus p50/p95/p99 summaries for:

- RSS
- private bytes
- handle count
- thread count
- CPU time delta

Example:

```powershell
cd backend
python tools/windows_soak.py --process ValorantTranslator --minutes 60 --output dist/soak.json
```

#15 is still not complete. Windows/Codex should add or run:

- real native-helper kill/recovery integration test
- SageWidgetService restart/cursor integration test after #5 is fixed
- PipeServer no-client shutdown test after #6 is fixed
- realistic game OCR screenshots
- a 30–60 minute real VALORANT soak while exercising game restart, widget
  close/reopen, alt-tab, device changes and resolution/DPI changes
- stage latency instrumentation if p50/p95/p99 ASR/translation/display latency is
  desired in addition to process-resource metrics

## #16 — release hardening prepared

Implemented in:

- `backend/version.py`
- `backend/release_validation.py`
- `backend/build_package.py`
- `backend/installer.iss`
- `backend/windows_capabilities.py`
- `backend/tests/test_release_validation.py`

Prepared behavior:

- canonical Sage version is `1.0.14` / `1.0.14.0`
- installer version now matches the Game Bar manifest
- installer project/support URLs now point at `yuanwil1y/sage`
- release metadata validator checks installer + manifest consistency
- production Full packaging now treats the Game Bar payload as required
- Game Bar payload must contain `AppxSignature.p7x` and a matching staged `.cer`
  (this checks that a signature block is present; Windows should still validate
  the actual certificate/package installation end to end)
- `--allow-missing` is explicitly a development escape hatch only
- process-loopback voice capture has an explicit minimum build constant of 20348
- the real `valorant_audio_capture.exe` path enforces the Windows capability gate
  before starting; the exception text explains the required/current build and
  that text-chat translation can remain available

Windows acceptance still required:

1. Compile a Full production package with all resources present.
2. Confirm the build fails when the signed Game Bar payload is removed.
3. Confirm `--allow-missing` still permits an intentionally incomplete dev build.
4. Compile `installer.iss` with Inno Setup and verify file version metadata.
5. Install/uninstall the resulting package and validate the signed MSIX/cert flow.
6. Test on a supported Windows build (>=20348).
7. If a <20348 VM is available, confirm voice capture fails with the explicit
   capability message rather than an opaque WASAPI/helper error.
8. Decide whether to surface that capability message more prominently in the
   main status UI; the current existing capture-start path logs the caught
   `FileNotFoundError` subclass.

## Still untouched / owned by Windows work

The following original issues are not solved by this branch:

- #2 WASAPI SILENT timeline preservation
- #5 Game Bar service session/cursor reset
- #6 cancellable `ConnectNamedPipe`
- #7 stale pipe reconnect / first subtitle loss
- #9 WASAPI activation/format negotiation
- #13 DXcam monitor/DPI mapping

Recommended Windows order remains:

1. validate this branch's CI and #8/#16 prepared code
2. #2 + #9
3. #6 + #7
4. #5
5. #13
6. full Windows soak / release validation
