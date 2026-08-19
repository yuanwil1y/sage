using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;

namespace ValorantTranslator.Services
{
    // 一条字幕（含原文与译文），供 UI 绑定
    public class SubtitleEntry
    {
        public string Id { get; set; }
        public string Source { get; set; }
        public string Original { get; set; }
        public string Translated { get; set; }

        public bool IsExpired(DateTime now)
        {
            double ttlSeconds = Source == "chat" ? 10.0 : 7.0;
            return (now - CreatedAt).TotalSeconds > ttlSeconds;
        }

        public DateTime CreatedAt { get; set; }
    }

    // Widget 内存只维护最近 5 条字幕（规格第 17 节）：
    // Voice 最多 3 条 / Chat 最多 3 条 / 总 5 条；voice 7 秒、chat 10 秒过期。
    public class SubtitleStore
    {
        private const int MaxTotal = 5;
        private const int MaxPerSource = 3;

        private readonly List<SubtitleEntry> _entries = new List<SubtitleEntry>();
        private readonly object _lock = new object();

        public event Action Changed;

        public ObservableCollection<SubtitleEntry> Entries { get; } =
            new ObservableCollection<SubtitleEntry>();

        // Keep the two sources separate in the view while retaining the
        // combined collection for compatibility with the original widget.
        public ObservableCollection<SubtitleEntry> VoiceEntries { get; } =
            new ObservableCollection<SubtitleEntry>();

        public ObservableCollection<SubtitleEntry> ChatEntries { get; } =
            new ObservableCollection<SubtitleEntry>();

        public void Add(Models.SubtitleMessage msg)
        {
            if (msg == null || string.IsNullOrEmpty(msg.Original)) return;

            DateTime createdAt = DateTime.UtcNow;
            if (msg.Timestamp > 0)
            {
                try
                {
                    createdAt = DateTimeOffset.FromUnixTimeMilliseconds(
                        (long)(msg.Timestamp * 1000.0)).UtcDateTime;
                }
                catch (ArgumentOutOfRangeException)
                {
                    createdAt = DateTime.UtcNow;
                }
            }

            lock (_lock)
            {
                if (!string.IsNullOrEmpty(msg.Id))
                {
                    _entries.RemoveAll(entry => entry.Id == msg.Id);
                }
                _entries.Add(new SubtitleEntry
                {
                    Id = msg.Id,
                    Source = msg.Source ?? "voice",
                    Original = msg.Original,
                    Translated = msg.Translated,
                    CreatedAt = createdAt,
                });
            }
            Prune();
            Changed?.Invoke();
        }

        public void Clear()
        {
            lock (_lock) _entries.Clear();
            Changed?.Invoke();
        }

        // Only the widget UI thread may update the ObservableCollection. The
        // pipe reader runs on a background task, so it updates the backing list
        // first and lets SubtitleWidget.RefreshForUi marshal the snapshot.
        public void RefreshForUi()
        {
            List<SubtitleEntry> snapshot;
            lock (_lock)
            {
                snapshot = new List<SubtitleEntry>(_entries);
            }

            Entries.Clear();
            VoiceEntries.Clear();
            ChatEntries.Clear();
            foreach (var entry in snapshot)
            {
                Entries.Add(entry);
                if (string.Equals(entry.Source, "chat", StringComparison.OrdinalIgnoreCase))
                {
                    ChatEntries.Add(entry);
                }
                else
                {
                    VoiceEntries.Add(entry);
                }
            }
        }

        private void Prune()
        {
            lock (_lock)
            {
                PruneLocked(DateTime.UtcNow);
            }
        }

        public void PruneExpired()
        {
            bool changed;
            lock (_lock)
            {
                int before = _entries.Count;
                PruneLocked(DateTime.UtcNow);
                changed = before != _entries.Count;
            }
            if (changed) Changed?.Invoke();
        }

        private void PruneLocked(DateTime now)
        {
            // 1. 过期清理
            _entries.RemoveAll(e => e.IsExpired(now));

            // Keep the backing list oldest -> newest before enforcing limits.
            // Pruning by source can then safely remove the oldest matching
            // entries without relying on indices captured before the list was
            // mutated.
            _entries.Sort((a, b) => a.CreatedAt.CompareTo(b.CreatedAt));

            // 2. 每类最多 3 条（保留最新）
            PruneSource("voice");
            PruneSource("chat");

            // 3. 总数最多 5 条（保留最新，移除最旧）
            while (_entries.Count > MaxTotal)
            {
                _entries.RemoveAt(0);
            }
        }

        private void PruneSource(string source)
        {
            int count = 0;
            for (int i = 0; i < _entries.Count; ++i)
            {
                if (string.Equals(_entries[i].Source, source, StringComparison.OrdinalIgnoreCase))
                {
                    count += 1;
                }
            }

            int overflow = count - MaxPerSource;
            if (overflow <= 0) return;

            // The list is sorted oldest -> newest. Remove the oldest matching
            // entries in-place. Do not cache indices: every RemoveAt shifts the
            // remaining list and stale ascending indices can delete another
            // source entirely.
            for (int i = 0; i < _entries.Count && overflow > 0;)
            {
                if (string.Equals(_entries[i].Source, source, StringComparison.OrdinalIgnoreCase))
                {
                    _entries.RemoveAt(i);
                    overflow -= 1;
                    continue;
                }
                i += 1;
            }
        }

    }
}
