"""音频管线识别准确率测试。

用 edge_tts 合成已知日语句子 → 转 16kHz mono → 跑 AudioPipeline(VAD) + whisper → 对比预期文本。

用法：
    python test_asr_accuracy.py
"""

import asyncio
import io
import time

import edge_tts
import numpy as np

from audio.pipeline import AudioPipeline
from audio.transcriber import Transcriber

# 测试句子（日语原文 → 预期）
TEST_SENTENCES = [
    "ジェットロー",
    "ミッド二人いる",
    "裏来てる",
    "Aサイトに行こう",
    "オペレーター持ってる",
]

VOICE = "ja-JP-NanamiNeural"  # 日语女声


async def synthesize(text: str) -> bytes:
    """用 edge_tts 合成 mp3 字节。"""
    communicate = edge_tts.Communicate(text, VOICE)
    buf = io.BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buf.write(chunk["data"])
    return buf.getvalue()


def mp3_to_16k_mono(mp3_bytes: bytes) -> np.ndarray:
    """mp3 → 16kHz mono float32。"""
    import subprocess
    import tempfile
    import os

    # 用 ffmpeg？没有。用 av (PyAV，faster-whisper 依赖已带)
    import av

    container = av.open(io.BytesIO(mp3_bytes))
    resampler = av.AudioResampler(format="flt", layout="mono", rate=16000)
    chunks = []
    for frame in container.decode(audio=0):
        for r in resampler.resample(frame):
            nd = r.to_ndarray()
            chunks.append(nd.reshape(-1))  # 展平成一维，避免帧长不一致
    # flush 剩余
    for r in resampler.resample(None):
        chunks.append(r.to_ndarray().reshape(-1))
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    audio = np.concatenate(chunks).astype(np.float32)
    return audio


async def main():
    transcriber = Transcriber(model_size="medium")  # 规格要求 medium
    pipeline = AudioPipeline()

    print("=" * 60)
    print("音频管线识别准确率测试（whisper medium）")
    print("=" * 60)

    total_correct = 0
    total = 0
    results = []

    for i, sentence in enumerate(TEST_SENTENCES):
        print(f"\n[{i+1}/{len(TEST_SENTENCES)}] 预期: {sentence}")
        mp3 = await synthesize(sentence)
        audio_16k = mp3_to_16k_mono(mp3)
        print(f"  音频时长: {len(audio_16k)/16000:.2f}s")

        # 跑音频管线（合成音频已是干净单句，直接用 transcriber 转写）
        t0 = time.time()
        recognized = transcriber.transcribe(audio_16k)
        elapsed = time.time() - t0

        # 归一化对比（去空格、去句读）
        def norm(s):
            return "".join(s.replace("　", "").replace(" ", "").replace("、", "").replace("。", ""))

        rec = norm(recognized)
        exp = norm(sentence)
        exact = rec == exp
        # 用 RapidFuzz 算相似度（更公平反映语义正确性）
        from rapidfuzz import fuzz
        similarity = fuzz.ratio(rec, exp)

        total += 1
        if exact:
            total_correct += 1

        results.append((sentence, recognized, exact, similarity, elapsed))
        marker = "OK" if exact else f"~{similarity}%"
        print(f"  识别: {recognized}  [{marker}]")
        print(f"  耗时: {elapsed:.2f}s")

    print("\n" + "=" * 60)
    print(f"严格匹配: {total_correct}/{total} = {total_correct/total*100:.0f}%")
    avg_sim = sum(r[3] for r in results) / len(results)
    print(f"平均相似度: {avg_sim:.0f}%")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
