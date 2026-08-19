using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Pipes;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;

namespace SageWidgetService
{
    internal static class Program
    {
        private const string PipeName = "LOCAL\\ValorantTranslator";
        private const int DefaultPort = 17382;
        private const int ConnectTimeoutMilliseconds = 2500;
        private static readonly ManualResetEventSlim StopEvent = new ManualResetEventSlim(false);

        [STAThread]
        private static void Main(string[] args)
        {
            int port = ReadPort(args, DefaultPort);
            string pipeName = ReadOption(args, "--pipe-name", PipeName);
            string mutexName = port == DefaultPort && string.Equals(pipeName, PipeName, StringComparison.Ordinal)
                ? "Local\\SageWidgetService"
                : "Local\\SageWidgetService_" + port;
            using (var mutex = new Mutex(true, mutexName, out bool createdNew))
            {
                if (!createdNew) return;

                AppDomain.CurrentDomain.ProcessExit += (sender, eventArgs) => StopEvent.Set();
                var broker = new SubtitleEventBroker();
                using (var http = new LocalSubtitleHttpServer(port, broker, Log))
                {
                    try
                    {
                        http.Start();
                        Log("SageWidgetService 已启动，监听 127.0.0.1:" + port);
                        RunPipeLoop(broker, pipeName);
                    }
                    catch (Exception ex)
                    {
                        Log("服务主循环异常: " + ex);
                    }
                }
            }
        }

        private static void RunPipeLoop(SubtitleEventBroker broker, string pipeName)
        {
            while (!StopEvent.IsSet)
            {
                try
                {
                    using (var pipe = new NamedPipeClientStream(
                        ".", pipeName, PipeDirection.In, PipeOptions.Asynchronous))
                    {
                        pipe.Connect(ConnectTimeoutMilliseconds);
                        Log("已连接 Sage 后台 Named Pipe。");
                        using (var reader = new StreamReader(pipe, new UTF8Encoding(false), false, 8192, true))
                        {
                            while (!StopEvent.IsSet && pipe.IsConnected)
                            {
                                string line = reader.ReadLine();
                                if (line == null) break;
                                line = line.Trim();
                                if (line.Length > 1 && line[0] == '{') broker.Publish(line);
                            }
                        }
                    }
                }
                catch (TimeoutException)
                {
                    // Sage GUI may not be running yet. Keep the local service alive.
                }
                catch (IOException ex)
                {
                    Log("Named Pipe 已断开: " + ex.Message);
                }
                catch (Exception ex)
                {
                    Log("Named Pipe 连接失败: " + ex.Message);
                }

                StopEvent.Wait(1000);
            }
        }

        private static int ReadPort(string[] args, int fallback)
        {
            if (args != null)
            {
                for (int i = 0; i + 1 < args.Length; i++)
                {
                    if (string.Equals(args[i], "--port", StringComparison.OrdinalIgnoreCase)
                        && int.TryParse(args[i + 1], out int port)
                        && port >= 1024 && port <= 65535)
                    {
                        return port;
                    }
                }
            }
            return fallback;
        }

        private static string ReadOption(string[] args, string name, string fallback)
        {
            if (args != null)
            {
                for (int i = 0; i + 1 < args.Length; i++)
                {
                    if (string.Equals(args[i], name, StringComparison.OrdinalIgnoreCase)
                        && !string.IsNullOrWhiteSpace(args[i + 1]))
                    {
                        return args[i + 1];
                    }
                }
            }
            return fallback;
        }

        private static void Log(string message)
        {
            try
            {
                string root = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    "Sage", "logs");
                Directory.CreateDirectory(root);
                File.AppendAllText(
                    Path.Combine(root, "gamebar-service.log"),
                    string.Format("[{0:yyyy-MM-dd HH:mm:ss.fff}] {1}{2}", DateTime.Now, message, Environment.NewLine),
                    Encoding.UTF8);
            }
            catch { }
        }
    }

    internal sealed class SubtitleEvent
    {
        public long Id { get; set; }
        public string Json { get; set; }
    }

    internal sealed class SubtitleEventBroker
    {
        private const int MaximumEvents = 256;
        private readonly object _gate = new object();
        private readonly List<SubtitleEvent> _events = new List<SubtitleEvent>();
        // The cursor is only meaningful inside one service process. A fresh
        // session lets the widget discard its old cursor after a service
        // restart instead of waiting forever for ids that can never arrive.
        public string Session { get; } = Guid.NewGuid().ToString("N");
        private long _cursor;

        public void Publish(string json)
        {
            lock (_gate)
            {
                _events.Add(new SubtitleEvent { Id = ++_cursor, Json = json });
                while (_events.Count > MaximumEvents) _events.RemoveAt(0);
                Monitor.PulseAll(_gate);
            }
        }

        public List<SubtitleEvent> Poll(long after, int waitMilliseconds, out long cursor)
        {
            lock (_gate)
            {
                List<SubtitleEvent> result = Collect(after);
                if (result.Count == 0 && waitMilliseconds > 0)
                {
                    Monitor.Wait(_gate, Math.Min(waitMilliseconds, 12000));
                    result = Collect(after);
                }
                cursor = _cursor;
                return result;
            }
        }

        private List<SubtitleEvent> Collect(long after)
        {
            return _events.FindAll(item => item.Id > after);
        }
    }

    internal sealed class LocalSubtitleHttpServer : IDisposable
    {
        private readonly int _port;
        private readonly SubtitleEventBroker _broker;
        private readonly Action<string> _log;
        private TcpListener _listener;
        private Thread _acceptThread;
        private volatile bool _stopping;

        public LocalSubtitleHttpServer(int port, SubtitleEventBroker broker, Action<string> log)
        {
            _port = port;
            _broker = broker;
            _log = log;
        }

        public void Start()
        {
            _listener = new TcpListener(IPAddress.Loopback, _port);
            _listener.Start();
            _acceptThread = new Thread(AcceptLoop) { IsBackground = true, Name = "SageWidgetHttp" };
            _acceptThread.Start();
        }

        private void AcceptLoop()
        {
            while (!_stopping)
            {
                try
                {
                    TcpClient client = _listener.AcceptTcpClient();
                    ThreadPool.QueueUserWorkItem(state => Handle((TcpClient)state), client);
                }
                catch (SocketException) when (_stopping) { }
                catch (Exception ex) { if (!_stopping) _log("HTTP 接收失败: " + ex.Message); }
            }
        }

        private void Handle(TcpClient client)
        {
            using (client)
            using (NetworkStream stream = client.GetStream())
            {
                try
                {
                    stream.ReadTimeout = 15000;
                    string request = ReadHeaders(stream);
                    string firstLine = request.Split(new[] { "\r\n" }, StringSplitOptions.None)[0];
                    string[] parts = firstLine.Split(' ');
                    if (parts.Length < 2 || parts[0] != "GET")
                    {
                        WriteResponse(stream, 405, "{\"error\":\"method_not_allowed\"}");
                        return;
                    }

                    string target = parts[1];
                    if (target.StartsWith("/health", StringComparison.Ordinal))
                    {
                        WriteResponse(stream, 200, "{\"status\":\"ok\"}");
                        return;
                    }
                    if (!target.StartsWith("/events", StringComparison.Ordinal))
                    {
                        WriteResponse(stream, 404, "{\"error\":\"not_found\"}");
                        return;
                    }

                    long after = ReadLongQuery(target, "after", 0);
                    int waitMs = (int)ReadLongQuery(target, "wait_ms", 8000);
                    List<SubtitleEvent> events = _broker.Poll(after, waitMs, out long cursor);
                    var body = new StringBuilder();
                    body.Append("{\"session\":\"").Append(_broker.Session)
                        .Append("\",\"cursor\":").Append(cursor).Append(",\"events\":[");
                    for (int i = 0; i < events.Count; i++)
                    {
                        if (i > 0) body.Append(',');
                        body.Append("{\"id\":").Append(events[i].Id)
                            .Append(",\"payload\":").Append(events[i].Json).Append('}');
                    }
                    body.Append("]}");
                    WriteResponse(stream, 200, body.ToString());
                }
                catch (Exception ex)
                {
                    _log("HTTP 请求失败: " + ex.Message);
                }
            }
        }

        private static string ReadHeaders(Stream stream)
        {
            var bytes = new List<byte>();
            while (bytes.Count < 8192)
            {
                int value = stream.ReadByte();
                if (value < 0) break;
                bytes.Add((byte)value);
                int count = bytes.Count;
                if (count >= 4 && bytes[count - 4] == 13 && bytes[count - 3] == 10
                    && bytes[count - 2] == 13 && bytes[count - 1] == 10) break;
            }
            return Encoding.ASCII.GetString(bytes.ToArray());
        }

        private static long ReadLongQuery(string target, string name, long fallback)
        {
            int marker = target.IndexOf('?');
            if (marker < 0) return fallback;
            foreach (string pair in target.Substring(marker + 1).Split('&'))
            {
                string[] parts = pair.Split(new[] { '=' }, 2);
                if (parts.Length == 2 && parts[0] == name && long.TryParse(parts[1], out long value))
                    return value;
            }
            return fallback;
        }

        private static void WriteResponse(Stream stream, int status, string body)
        {
            byte[] payload = Encoding.UTF8.GetBytes(body);
            string reason = status == 200 ? "OK" : status == 404 ? "Not Found" : "Method Not Allowed";
            byte[] header = Encoding.ASCII.GetBytes(
                "HTTP/1.1 " + status + " " + reason + "\r\n"
                + "Content-Type: application/json; charset=utf-8\r\n"
                + "Content-Length: " + payload.Length + "\r\n"
                + "Connection: close\r\n\r\n");
            stream.Write(header, 0, header.Length);
            stream.Write(payload, 0, payload.Length);
        }

        public void Dispose()
        {
            _stopping = true;
            try { _listener?.Stop(); } catch { }
            if (_acceptThread != null && _acceptThread != Thread.CurrentThread) _acceptThread.Join(1000);
        }
    }
}
