import logging
import math
import os
from pathlib import Path
import subprocess
import tempfile
import time

from imageio_ffmpeg import get_ffmpeg_exe, read_frames

from utils.logging_util import log_event


DIDA_VIDEO_MAX_BYTES = 18_000_000
_VIDEO_ENCODE_BUDGET_BYTES = 17_500_000
_CONTAINER_RESERVE_BYTES = 256 * 1024
_AUDIO_BITRATE = 64_000
_CRF = 23
_PROJECT_CACHE = Path(__file__).resolve().parents[1] / ".cache" / "video-compression"


class VideoCompressionError(RuntimeError):
    pass


def _read_metadata(path: Path) -> dict:
    reader = read_frames(str(path))
    try:
        return next(reader)
    finally:
        reader.close()


def _output_size(metadata: dict) -> tuple[int, int]:
    size = metadata.get("source_size") or metadata.get("size")
    if not isinstance(size, (tuple, list)) or len(size) != 2:
        raise VideoCompressionError("无法读取视频分辨率")
    width, height = size
    if not all(isinstance(value, (int, float)) and math.isfinite(value) and value > 0 for value in size):
        raise VideoCompressionError("无法读取视频分辨率")
    scale = min(1.0, 1280 / max(width, height), 720 / min(width, height))
    return max(2, int(width * scale) // 2 * 2), max(2, int(height * scale) // 2 * 2)


def _run_ffmpeg(arguments: list[str], stage: str) -> None:
    try:
        result = subprocess.run(
            [get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            check=False,
        )
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        raise VideoCompressionError(f"{stage} 无法启动: {error}") from error
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace")[-4000:].strip()
        raise VideoCompressionError(f"{stage} 转码失败: {detail}")


def _read_output(path: Path) -> bytes:
    try:
        data = path.read_bytes()
    except OSError as error:
        raise VideoCompressionError(f"无法读取转码结果: {error}") from error
    if len(data) < 12 or data[4:8] != b"ftyp":
        raise VideoCompressionError("转码结果为空或不是有效 MP4 文件")
    return data


def compress_video(data: bytes) -> bytes:
    """优先 CRF 23，超限后循环 two-pass；仅返回严格小于 18 MB 的视频。"""
    if len(data) < DIDA_VIDEO_MAX_BYTES:
        return data

    _PROJECT_CACHE.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="attachment-", dir=_PROJECT_CACHE) as temp_dir:
        directory = Path(temp_dir)
        source_path = directory / "source.mp4"
        output_path = directory / "compressed.mp4"
        source_path.write_bytes(data)
        try:
            metadata = _read_metadata(source_path)
        except Exception as error:
            raise VideoCompressionError(f"无法读取视频元数据: {error}") from error
        width, height = _output_size(metadata)
        video_arguments = [
            "-i", str(source_path),
            "-map", "0:v:0",
            "-vf", f"scale={width}:{height}:flags=lanczos,setsar=1",
            "-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p",
        ]
        audio_arguments = ["-map", "0:a:0?", "-c:a", "aac", "-b:a", str(_AUDIO_BITRATE)]
        log_event("media", "video_compression_started", source_bytes=len(data),
                  output_size=f"{width}x{height}", crf=_CRF, audio_bitrate=_AUDIO_BITRATE)
        try:
            _run_ffmpeg(
                [*video_arguments, "-crf", str(_CRF), *audio_arguments,
                 "-movflags", "+faststart", str(output_path)],
                "CRF",
            )
            compressed = _read_output(output_path)
        except VideoCompressionError as error:
            log_event("media", "video_crf_failed", level=logging.WARNING, error=error)
        else:
            if len(compressed) < DIDA_VIDEO_MAX_BYTES:
                log_event("media", "video_compression_completed", mode="crf", source_bytes=len(data),
                          output_bytes=len(compressed), elapsed_seconds=round(time.monotonic() - started, 3))
                return compressed
            log_event("media", "video_crf_exceeded_limit", output_bytes=len(compressed),
                      max_bytes=DIDA_VIDEO_MAX_BYTES)

        duration = metadata.get("duration")
        if not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
            raise VideoCompressionError("two-pass 无法读取有效的视频时长")
        video_bitrate = int((_VIDEO_ENCODE_BUDGET_BYTES - _CONTAINER_RESERVE_BYTES) * 8 / duration - _AUDIO_BITRATE)
        attempt = 0
        while video_bitrate > 0:
            attempt += 1
            log_event("media", "video_two_pass_started", attempt=attempt, duration_seconds=duration,
                      video_bitrate=video_bitrate, budget_bytes=_VIDEO_ENCODE_BUDGET_BYTES)
            rate_arguments = ["-b:v", str(video_bitrate), "-passlogfile", str(directory / "ffmpeg-pass")]
            _run_ffmpeg(
                [*video_arguments, *rate_arguments, "-pass", "1", "-an", "-f", "null", os.devnull],
                f"two-pass 第 {attempt} 轮第一遍",
            )
            _run_ffmpeg(
                [*video_arguments, *rate_arguments, "-pass", "2", *audio_arguments,
                 "-movflags", "+faststart", str(output_path)],
                f"two-pass 第 {attempt} 轮第二遍",
            )
            compressed = _read_output(output_path)
            if len(compressed) < DIDA_VIDEO_MAX_BYTES:
                log_event("media", "video_compression_completed", mode="two_pass", attempt=attempt,
                          source_bytes=len(data), output_bytes=len(compressed),
                          elapsed_seconds=round(time.monotonic() - started, 3))
                return compressed
            log_event("media", "video_two_pass_exceeded_limit", attempt=attempt, output_bytes=len(compressed),
                      max_bytes=DIDA_VIDEO_MAX_BYTES)
            video_bitrate = int(video_bitrate * _VIDEO_ENCODE_BUDGET_BYTES / len(compressed) * 0.95)

        raise VideoCompressionError("音频与封装预算不足，无法继续使用有效的视频码率编码")
