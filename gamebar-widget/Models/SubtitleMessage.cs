using System;

namespace ValorantTranslator.Models
{
    // 对应 backend 的 NDJSON subtitle 消息（规格第 15 节）
    public class SubtitleMessage
    {
        public int Version { get; set; }
        public string Type { get; set; }
        public string Source { get; set; }
        public string Id { get; set; }
        public string Original { get; set; }
        public string Translated { get; set; }
        public double Timestamp { get; set; }
    }
}
