"""Runtime model hot-activation tests."""

from runtime_models import PassthroughTranslator, RuntimeModelController


class FakeOrchestrator:
    def __init__(self, transcriber=None):
        self.transcriber = transcriber
        self.translator = None
        self.statuses = []
        self._voice_model_error_reported = True

    def replace_translator(self, translator):
        self.translator = translator

    def _report_status(self, section, text):
        self.statuses.append((section, text))


class FakeMt2:
    def __init__(self, *, fail_ready=False, fail_ready_count=0):
        self.base_url = "http://127.0.0.1:18088"
        self.fail_ready = fail_ready
        self.fail_ready_count = int(fail_ready_count)
        self.calls = []
        self._running = False

    @property
    def running(self):
        return self._running

    def stop(self):
        self.calls.append("stop")
        self._running = False

    def start(self):
        self.calls.append("start")
        self._running = True

    def wait_ready(self, timeout):
        self.calls.append(("wait", timeout))
        if self.fail_ready or self.fail_ready_count > 0:
            if self.fail_ready_count > 0:
                self.fail_ready_count -= 1
            raise RuntimeError("not ready")


class FakeTranslator:
    def __init__(self, base_url):
        self.base_url = base_url

    def translate(self, text):
        return f"译:{text}"


class FakeTranscriber:
    def __init__(self, available=True):
        self.available = available
        self.reset_count = 0

    def reset_model(self):
        self.reset_count += 1

    @property
    def model_available(self):
        return self.available


def test_hy_mt2_install_hot_activates_translator():
    orchestrator = FakeOrchestrator()
    mt2 = FakeMt2()
    controller = RuntimeModelController(
        orchestrator,
        mt2,
        translator_factory=FakeTranslator,
        mt2_ready_timeout=0.25,
    )

    controller.handle_change("hy-mt2", "下载")

    assert isinstance(orchestrator.translator, FakeTranslator)
    assert orchestrator.translator.base_url == mt2.base_url
    assert mt2.calls == ["stop", "start", ("wait", 0.25)]
    assert orchestrator.statuses[-1] == ("Local Translation", "Hy-MT2: loaded")


def test_hy_mt2_activation_failure_degrades_to_passthrough():
    orchestrator = FakeOrchestrator()
    mt2 = FakeMt2(fail_ready=True)
    controller = RuntimeModelController(
        orchestrator,
        mt2,
        translator_factory=FakeTranslator,
    )

    controller.handle_change("hy-mt2", "导入")

    assert isinstance(orchestrator.translator, PassthroughTranslator)
    assert mt2.calls[-1] == "stop"
    assert orchestrator.statuses[-1][0] == "Local Translation"
    assert "unavailable" in orchestrator.statuses[-1][1]


def test_hy_mt2_delete_stops_server_and_degrades():
    orchestrator = FakeOrchestrator()
    mt2 = FakeMt2()
    controller = RuntimeModelController(
        orchestrator,
        mt2,
        translator_factory=FakeTranslator,
    )

    controller.handle_change("hy-mt2", "删除")

    assert isinstance(orchestrator.translator, PassthroughTranslator)
    assert mt2.calls == ["stop"]
    assert orchestrator.statuses[-1] == (
        "Local Translation",
        "Hy-MT2: unavailable",
    )


def test_whisper_change_resets_lazy_model_and_status():
    transcriber = FakeTranscriber(available=True)
    orchestrator = FakeOrchestrator(transcriber)
    controller = RuntimeModelController(
        orchestrator,
        FakeMt2(),
        translator_factory=FakeTranslator,
    )

    controller.handle_change("whisper-medium", "下载")

    assert transcriber.reset_count == 1
    assert orchestrator._voice_model_error_reported is False
    assert orchestrator.statuses[-1] == ("Voice", "ASR: ready")


def test_watcher_detects_install_and_same_status_replacement():
    states = {
        "hy-mt2": ("missing", ()),
        "whisper-medium": ("missing", ()),
    }
    orchestrator = FakeOrchestrator(FakeTranscriber())
    mt2 = FakeMt2()
    controller = RuntimeModelController(
        orchestrator,
        mt2,
        translator_factory=FakeTranslator,
        watch_interval=60.0,
        state_reader=lambda key: states[key],
    )
    controller.start()
    try:
        states["hy-mt2"] = ("installed", (("model.gguf", 100, 1),))
        controller.refresh_once()
        assert isinstance(orchestrator.translator, FakeTranslator)
        assert mt2.calls.count("start") == 1

        # A replacement can remain "installed" while the fingerprint changes.
        states["hy-mt2"] = ("installed", (("model.gguf", 100, 2),))
        controller.refresh_once()
        assert mt2.calls.count("start") == 2
    finally:
        controller.stop()


def test_watcher_detects_delete_after_installed_state():
    states = {
        "hy-mt2": ("installed", (("model.gguf", 100, 1),)),
        "whisper-medium": ("missing", ()),
    }
    orchestrator = FakeOrchestrator(FakeTranscriber())
    mt2 = FakeMt2()
    controller = RuntimeModelController(
        orchestrator,
        mt2,
        translator_factory=FakeTranslator,
        watch_interval=60.0,
        state_reader=lambda key: states[key],
    )
    controller.start()
    try:
        states["hy-mt2"] = ("missing", ())
        controller.refresh_once()
        assert isinstance(orchestrator.translator, PassthroughTranslator)
        assert mt2.calls == ["stop"]
    finally:
        controller.stop()


def test_watcher_ignores_transient_state_during_atomic_replacement():
    states = {
        "hy-mt2": ("installed", (("model.gguf", 100, 1),)),
        "whisper-medium": ("missing", ()),
    }
    orchestrator = FakeOrchestrator(FakeTranscriber())
    mt2 = FakeMt2()
    controller = RuntimeModelController(
        orchestrator,
        mt2,
        translator_factory=FakeTranslator,
        watch_interval=60.0,
        state_reader=lambda key: states[key],
    )
    controller.start()
    try:
        states["hy-mt2"] = ("changing", ())
        controller.refresh_once()
        assert mt2.calls == []
        assert orchestrator.translator is None

        states["hy-mt2"] = ("installed", (("model.gguf", 100, 2),))
        controller.refresh_once()
        assert mt2.calls.count("start") == 1
        assert isinstance(orchestrator.translator, FakeTranslator)
    finally:
        controller.stop()


def test_installed_hy_mt2_retries_after_transient_runtime_failure():
    now = [100.0]
    states = {
        "hy-mt2": ("installed", (("model.gguf", 100, 1),)),
        "whisper-medium": ("missing", ()),
    }
    orchestrator = FakeOrchestrator(FakeTranscriber())
    orchestrator.translator = PassthroughTranslator()
    mt2 = FakeMt2(fail_ready_count=1)
    controller = RuntimeModelController(
        orchestrator,
        mt2,
        translator_factory=FakeTranslator,
        watch_interval=60.0,
        state_reader=lambda key: states[key],
        retry_initial_delay=0.5,
        retry_max_delay=0.5,
        clock=lambda: now[0],
    )
    controller.start()
    try:
        # No file transition is required: installed + inactive is enough to
        # trigger the first runtime retry.
        controller.refresh_once()
        assert mt2.calls.count("start") == 1
        assert isinstance(orchestrator.translator, PassthroughTranslator)

        # Backoff prevents a tight retry loop.
        controller.refresh_once()
        assert mt2.calls.count("start") == 1

        now[0] += 0.5
        controller.refresh_once()
        assert mt2.calls.count("start") == 2
        assert isinstance(orchestrator.translator, FakeTranslator)
        assert orchestrator.statuses[-1] == ("Local Translation", "Hy-MT2: loaded")
    finally:
        controller.stop()
