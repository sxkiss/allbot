"""从视频字节抽取首帧 JPEG 作为封面。

对齐 MuseBridge._extract_video_cover 的做法：不依赖外部封面 URL（常有防盗链/签名过期），
直接从视频本身抽真实首帧，避免微信侧显示默认占位图/无封面。
"""
from __future__ import annotations

import asyncio
import os
import shutil
import tempfile

FFMPEG = "ffmpeg"


async def extract_video_cover(video_bytes: bytes, seek_seconds: float = 1.0) -> bytes:
    """返回 JPEG 封面字节；失败返回 b""。"""
    if not video_bytes or len(video_bytes) < 1024:
        return b""
    workdir = tempfile.mkdtemp(prefix="videocover_")
    try:
        src = os.path.join(workdir, "in.mp4")
        dst = os.path.join(workdir, "cover.jpg")
        with open(src, "wb") as file:
            file.write(video_bytes)
        for seek in (seek_seconds, 0.0):
            args = [FFMPEG, "-loglevel", "error", "-y"]
            if seek and seek > 0:
                args += ["-ss", f"{seek}"]
            args += ["-i", src, "-frames:v", "1", "-q:v", "2", dst]
            try:
                proc = await asyncio.create_subprocess_exec(
                    *args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(proc.communicate(), timeout=25)
            except Exception:
                continue
            if proc.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 0:
                with open(dst, "rb") as file:
                    data = file.read()
                if data.startswith(b"\xff\xd8"):
                    return data
        return b""
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
