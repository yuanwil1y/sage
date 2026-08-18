"""下载 Hy-MT2 翻译模型。

模型来源：Hugging Face tencent/Hy-MT2-1.8B-GGUF 的 Q4_K_M 量化
大小约 1.13GB，首次使用前运行一次即可。下载到 backend/models/hy-mt2/。

用法：
    python download_model.py
    python download_model.py --verify-group sha256
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from pathlib import Path
from urllib.request import urlopen

# 模型信息
REPO = "tencent/Hy-MT2-1.8B-GGUF"
FILENAME = "Hy-MT2-1.8B-Q4_K_M.gguf"
URL = f"https://huggingface.co/{REPO}/resolve/main/{FILENAME}"

# 期望 SHA256（可选校验）
EXPECTED_SHA256 = "DC5F44FCF1FA496EE7AD725982C0C8C553A4DE00259B53AF84C4B89FB0C06699"

# 期望大小（约 1.13GB）
EXPECTED_SIZE = 1_133_080_448


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    print(f"下载 {FILENAME} ...")
    print(f"  来源: {url}")
    print(f"  保存: {dest}")

    # 断点续传：已有 .part 则续传
    mode = "ab" if tmp.exists() else "wb"
    start = tmp.stat().st_size if tmp.exists() else 0
    headers = {"User-Agent": "valorant-translator/1.0"}
    if start:
        headers["Range"] = f"bytes={start}-"

    req = __import__("urllib.request").request.Request(url, headers=headers)
    last = time.time()
    downloaded = start
    with urlopen(req) as resp, open(tmp, mode) as f:
        if start and resp.status == 200:
            # 服务器不支持续传，从头来
            f.seek(0, 0)
            f.truncate()
            downloaded = 0
        total = int(resp.headers.get("Content-Length", 0)) + downloaded
        print(f"  总大小: {total/1024/1024:.0f} MB")
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
            downloaded += len(chunk)
            now = time.time()
            if now - last > 0.5:
                pct = downloaded / total * 100 if total else 0
                mb = downloaded / 1024 / 1024
                print(f"  {mb:.0f} MB / {total/1024/1024:.0f} MB ({pct:.0f}%)", end="\r")
                last = now
    print()
    tmp.rename(dest)
    print(f"完成: {dest} ({dest.stat().st_size/1024/1024:.0f} MB)")


def _verify(dest: Path) -> None:
    size = dest.stat().st_size
    if size < EXPECTED_SIZE * 0.99:
        print(f"[警告] 文件可能不完整: {size} 字节 (< 期望 {EXPECTED_SIZE})")
    if EXPECTED_SHA256:
        print("校验 SHA256 ...")
        h = hashlib.sha256()
        with open(dest, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        digest = h.hexdigest().lower()
        expected = EXPECTED_SHA256.lower()
        if digest != expected:
            print(f"[失败] SHA256 不匹配: {digest}")
            print(f"       期望 {expected}")
            sys.exit(1)
        print("SHA256 校验通过")
    else:
        print(f"[提示] 未启用 SHA256 强校验（文件大小校验通过）")


def main() -> None:
    parser = argparse.ArgumentParser(description="下载 Hy-MT2 翻译模型")
    parser.add_argument("--url", default=URL)
    parser.add_argument(
        "--dest",
        type=Path,
        default=Path(__file__).resolve().parent / "models" / "hy-mt2" / FILENAME,
    )
    parser.add_argument("--verify-only", action="store_true", help="仅校验已下载文件")
    args = parser.parse_args()

    if args.verify_only:
        if not args.dest.exists():
            print(f"[错误] 文件不存在: {args.dest}")
            sys.exit(1)
        _verify(args.dest)
        return

    _download(args.url, args.dest)
    _verify(args.dest)


if __name__ == "__main__":
    main()
