# Sage final Windows handover

This document is the handoff after the post-PR-21 deep review and follow-up implementation. Do **not** redo the earlier Python streaming architecture. Start from the open draft PRs below and validate/merge them deliberately.

## Current open draft PRs

### PR #28 — widget restart replay + SubtitleStore pruning
Branch: `agent/widget-restart-store-fixes`
Issues: #5, #22

Implemented:
- Treat SageWidgetService `session` as a hard event epoch.
- On session change, clear the old cursor/store and discard the response requested with the stale cursor; the next request uses `after=0` so retained events from the new service are replayed.
- Prune per-source subtitle retention by `CreatedAt`/entry identity rather than stale ascending indices.

Automated validation completed:
- Existing portable/Windows repository jobs: green.
- A temporary Windows 2022 PR workflow compiled `SageWidgetService.csproj` x64 Release successfully.
- The same workflow compiled the Game Bar UWP/widget project successfully.
- The temporary workflow was removed again; final PR diff is only the two business C# files.

Still validate interactively:
1. Start widget + service, accumulate a nontrivial old cursor, restart only SageWidgetService, publish events before the widget detects the new session, and verify the first retained events are shown immediately.
2. Feed interleaved voice/chat subtitles with one source overflowing by 2+ and verify the other source is never pruned incorrectly.

### PR #29 — runtime recovery + packaged startup
Branch: `agent/runtime-lifecycle-followups`
Issues: #23, #24, #25, #27

Implemented:
- Per-generation AudioCapture supervisor cancellation event; old generations cannot be revived by a later `start()`.
- Hy-MT2 installed-but-inactive runtime retries with bounded exponential backoff, including recovery from an initial startup failure without changing model files.
- Registered/tested packaged `--headless` mode.
- Voice UI distinguishes unsupported Windows process-loopback build, missing helper, and missing ASR model.

Automated validation completed:
- Portable Python regression job: green.
- Windows Python/supervision job: green.

Useful final smoke tests:
- Kill/restart helper around a target PID change and confirm only one generation remains.
- Force first llama-server readiness failure and confirm runtime recovers without restarting Sage.

### PR #30 — output-local multi-monitor capture + Windows CI
Branch: `agent/display-ci-followups`
Issues: #13, #15 (Refs; keep open until physical acceptance)

Implemented:
- New ROI contract is DXcam **output-local physical pixels**.
- Never pass Windows virtual-desktop x/y offsets to `camera.grab()`.
- Persist `(device_idx, output_idx)` plus coordinate-space marker and screen fingerprint.
- Migrate Sage 1.0.14 legacy `screen_origin + local_physical` ROI when saved screen geometry is available; otherwise require reselection.
- Resolve persisted outputs across all DXcam adapters.
- For a brand-new Qt selection, do not trust Qt screen index as DXcam identity; require a unique physical match across adapters and reject ambiguity.
- Pass `device_idx` to `dxcam.create()` and request BGR explicitly.
- Reject negative/out-of-output capture regions before DXcam.
- Resolve/migrate the ROI once when the OCR worker is constructed, avoiding repeated invalid-config errors every poll.
- Added tests for right/left secondary monitors, legacy migration, output-local scaling, cross-GPU remap, and ambiguous equal-spec monitors.
- CI runs display/ROI/screen-capture/Named-Pipe tests.
- Windows artifact job builds native helper, SageWidgetService, an unsigned Game Bar MSIX/AppX via `gamebar-widget/build.ps1`, then runs artifact-dependent local-service integration tests.

Automated validation completed on the latest code:
- Portable Python regressions: green.
- Windows Python/supervision regressions: green.
- Native process-loopback helper build: green.
- SageWidgetService x64 Release build: green.
- Unsigned Game Bar MSIX/AppX build: green.
- Artifact-dependent Named Pipe -> SageWidgetService -> HTTP integration tests: green.

Still validate physically:
1. Secondary monitor to the **right** of primary, mixed DPI.
2. Secondary monitor to the **left/above** primary (negative virtual desktop origin).
3. Hybrid/multi-GPU setup with the target monitor on non-zero DXcam `device_idx`.
4. Fullscreen/borderless VALORANT OCR from the selected monitor.
5. Signed MSIX/certificate installation, Game Bar activation, widget visual behavior, cleanup/uninstall.

If two non-primary monitors have the same resolution/DPI and DXcam's available enumeration cannot uniquely map a new Qt selection, current code intentionally rejects the selection instead of guessing. If real Windows testing exposes a better stable identity (Win32 monitor handle / DXGI output identity), improve the mapping rather than restoring index assumptions.

### PR #31 — cancellable Named Pipe writes
Branch: `agent/pipe-write-cancellation`
Issue: #26

Implemented:
- Overlapped Named Pipe `WriteFile` with bounded timeout.
- Keep payload alive for the pending OVERLAPPED lifetime.
- Poll write completion and server stop signal.
- Cancel/reap pending writes using `CancelIo` / `GetOverlappedResult`.
- Disconnect a connected-but-not-reading client instead of hanging forever.
- Do not synchronously drain ephemeral subtitle queue during shutdown.
- Fault-injection test connects a client that never reads and sends a payload much larger than the pipe buffer.

Automated validation completed:
- Portable Python job: green.
- Windows Python/supervision job including the stalled-client `test_pipe_server.py` fault injection: green.

Optional final smoke:
- Suspend/hang SageWidgetService while its pipe handle remains open, then verify PipeServer drops/recreates the client and Sage shuts down promptly.

## Important merge/rebase guidance

The follow-up PRs were intentionally developed independently from `main`. PR #29, #30, and #31 all touch `.github/workflows/tests.yml`.

**Do not resolve that workflow conflict by taking one side wholesale. Preserve the union of coverage.** Final `tests.yml` should include at least:
- existing audio/VAD/OCR/runtime/release tests;
- `tests/test_startup_options.py` from PR #29;
- `tests/test_roi.py`, `tests/test_display_mapping.py`, `tests/test_screen_capture.py` from PR #30;
- `tests/test_pipe_server.py` from #30/#31;
- the Windows artifact integration job from PR #30.

Recommended sequence:
1. Validate the real service-restart behavior in PR #28, then merge it.
2. Merge PR #29 after its already-green Python checks are still current.
3. Rebase/update PR #31 onto the new `main`, preserving `test_startup_options.py`; rerun the Windows pipe fault injection; merge #31.
4. Rebase/update PR #30 **last**, preserving the union of all tests/workflow changes, then rerun all three jobs and complete physical monitor/Game Bar acceptance before merging.

PR #30 can also be merged before #31 if desired, but whichever workflow-touching PR goes last must be rebased and preserve the union. Do not blindly choose `ours`/`theirs` for `.github/workflows/tests.yml`.

## Remaining #15 acceptance beyond automated CI

Even after CI is green, complete:
- 30–60 minute Windows soak with thread/handle/RSS monitoring.
- Record practical stage latency while VALORANT voice/chat is active.
- Repeated native-helper crash/restart and local-service restart cycles.
- Signed release build/install/uninstall smoke.

`backend/tools/windows_soak.py` already exists from the earlier work and can be used for the resource soak.

## Architecture invariants not to regress

- Native capture callback stays lightweight: normalize/resample/VAD/enqueue only.
- Whisper and translation remain on bounded workers; do not put blocking model work back on stdout/OCR producer threads.
- PCM byte remainder, soxr state, VAD remainder/recurrent state are reset across helper/process discontinuities.
- WASAPI SILENT packets preserve timeline duration.
- Named Pipe messages remain ephemeral/realtime; do not build an unbounded replay backlog in Python.
- Game Bar service session ID, not numeric cursor alone, defines the event epoch.

## Suggested Codex prompt

```text
Checkout the Sage repository and read CODEX_FINAL_HANDOVER.md from branch agent/codex-final-handover.
Review draft PRs #28, #29, #30, #31 and their linked issues before changing code.
Do the remaining Windows-only validation and fix any failures you can reproduce. Preserve the existing bounded async Python architecture.
Pay special attention to the .github/workflows/tests.yml union when rebasing/merging #29/#30/#31; do not drop startup_options, display/ROI, pipe, or artifact-integration coverage.
Do not close #13 or #15 until real multi-monitor/Game Bar/VALORANT acceptance is complete.
Do not merge a PR just because it compiles; run the relevant interactive Windows acceptance described in this handover.
```
