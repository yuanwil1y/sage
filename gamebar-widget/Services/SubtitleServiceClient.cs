using System;
using System.Threading;
using System.Threading.Tasks;
using Windows.Data.Json;
using Windows.Web.Http;

namespace ValorantTranslator.Services
{
    public sealed class SubtitleServiceClient : IDisposable
    {
        private const int PollWaitMilliseconds = 8000;
        private const int PollTimeoutMilliseconds = 12000;
        private const int ReconnectDelayMilliseconds = 1000;

        private readonly SubtitleStore _store;
        private readonly Action<string> _onStatus;
        private CancellationTokenSource _cts;
        private Task _loop;
        private ulong _cursor;
        private string _session;
        private string _lastStatus;

        public SubtitleServiceClient(SubtitleStore store, Action<string> onStatus)
        {
            _store = store;
            _onStatus = onStatus;
        }

        public void Start()
        {
            if (_cts != null) return;
            _cts = new CancellationTokenSource();
            _loop = Task.Run(() => RunAsync(_cts.Token));
        }

        private async Task RunAsync(CancellationToken cancellationToken)
        {
            ReportStatus("connecting");
            while (!cancellationToken.IsCancellationRequested)
            {
                try
                {
                    using (var client = new HttpClient())
                    {
                        client.DefaultRequestHeaders.TryAppendWithoutValidation("Connection", "close");
                        var uri = LocalServiceEndpoints.Build(
                            "/events?after=" + _cursor
                            + "&wait_ms=" + PollWaitMilliseconds);
                        using (var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken))
                        {
                            timeout.CancelAfter(PollTimeoutMilliseconds);
                            HttpResponseMessage response = await client.GetAsync(uri).AsTask(timeout.Token);
                            if (!response.IsSuccessStatusCode)
                                throw new InvalidOperationException("HTTP " + (int)response.StatusCode);

                            string payload = await response.Content.ReadAsStringAsync();
                            DispatchBatch(payload);
                            ReportStatus("connected");
                        }
                    }
                }
                catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
                {
                    break;
                }
                catch (Exception ex)
                {
                    App.Log("Subtitle service poll failed: " + ex.Message);
                    ReportStatus("reconnecting");
                    try { await Task.Delay(ReconnectDelayMilliseconds, cancellationToken); }
                    catch (OperationCanceledException) { break; }
                }
            }
        }

        private void DispatchBatch(string json)
        {
            JsonObject batch = JsonObject.Parse(json);
            string session = batch.ContainsKey("session")
                ? batch.GetNamedString("session", "")
                : "";

            bool sessionChanged = !string.IsNullOrEmpty(session)
                && !string.IsNullOrEmpty(_session)
                && !string.Equals(session, _session, StringComparison.Ordinal);
            if (sessionChanged)
            {
                // The numeric cursor only has meaning inside one service
                // process. The request that discovered this new session was
                // still sent with the old session's cursor, so its batch
                // cursor/events cannot be used safely. Reset and force the
                // next poll to start at zero, which replays the new service's
                // retained events instead of skipping them.
                _session = session;
                _cursor = 0;
                _store.Clear();
                return;
            }
            if (!string.IsNullOrEmpty(session)) _session = session;

            ulong batchCursor = ToUInt64(batch.GetNamedNumber("cursor", 0));
            JsonArray events = batch.GetNamedArray("events", new JsonArray());
            foreach (IJsonValue value in events)
            {
                if (value.ValueType != JsonValueType.Object) continue;
                JsonObject envelope = value.GetObject();
                ulong id = ToUInt64(envelope.GetNamedNumber("id", 0));
                if (id == 0 || id <= _cursor) continue;
                JsonObject payload = envelope.GetNamedObject("payload", null);
                if (payload != null) Dispatch(payload);
                _cursor = id;
            }
            if (events.Count == 0 && batchCursor > _cursor) _cursor = batchCursor;
        }

        private void Dispatch(JsonObject obj)
        {
            string type = GetString(obj, "type");
            switch (type)
            {
                case "subtitle":
                    _store.Add(new Models.SubtitleMessage
                    {
                        Version = GetInt(obj, "v"),
                        Type = type,
                        Source = GetString(obj, "source"),
                        Id = GetString(obj, "id"),
                        Original = GetString(obj, "original"),
                        Translated = GetString(obj, "translated"),
                        Timestamp = GetDouble(obj, "ts"),
                    });
                    break;
                case "clear":
                    _store.Clear();
                    break;
            }
        }

        private void ReportStatus(string status)
        {
            if (string.Equals(status, _lastStatus, StringComparison.Ordinal)) return;
            _lastStatus = status;
            _onStatus?.Invoke(status);
        }

        private static ulong ToUInt64(double value)
            => value > 0 ? (ulong)Math.Floor(value) : 0;

        private static string GetString(JsonObject obj, string key)
            => obj.ContainsKey(key) ? obj[key].GetString() : "";

        private static int GetInt(JsonObject obj, string key)
            => obj.ContainsKey(key) ? (int)obj[key].GetNumber() : 0;

        private static double GetDouble(JsonObject obj, string key)
            => obj.ContainsKey(key) ? obj[key].GetNumber() : 0.0;

        public void Dispose()
        {
            _cts?.Cancel();
            _cts = null;
        }
    }
}
