using System;

namespace ValorantTranslator.Services
{
    internal static class LocalServiceEndpoints
    {
        public const string Scheme = "http";
        public const string Host = "127.0.0.1";
        public const int Port = 17382;

        public static Uri Build(string path)
        {
            string suffix = path.StartsWith("/") ? path : "/" + path;
            return new Uri(Scheme + "://" + Host + ":" + Port + suffix);
        }
    }
}
