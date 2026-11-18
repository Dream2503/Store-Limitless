"""Validate FFmpeg CUDA/NVENC support for dynamically generated video segments."""

from __future__ import annotations

import asyncio

from core.data_center import Database
from core.utils import sanitize_sensitive_urls, write_log


_GPU_PIPELINE_READY: set[str] = set()
_GPU_PIPELINE_LOCK = asyncio.Lock()


async def ensure_gpu_video_pipeline(ffmpeg_path: str) -> None:
    """Verify FFmpeg can decode, filter, and encode video through NVIDIA CUDA/NVENC."""

    if ffmpeg_path in _GPU_PIPELINE_READY:
        return

    async with _GPU_PIPELINE_LOCK:
        if ffmpeg_path in _GPU_PIPELINE_READY:
            return

        try:
            encoder_process = await asyncio.create_subprocess_exec(
                ffmpeg_path,
                "-hide_banner", "-encoders",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            encoder_stdout, encoder_stderr = await asyncio.wait_for(encoder_process.communicate(), timeout=15)
            encoder_output = (
                encoder_stdout.decode("utf-8", errors="replace")
                + encoder_stderr.decode("utf-8", errors="replace")
            )
            if encoder_process.returncode != 0 or "h264_nvenc" not in encoder_output:
                raise RuntimeError("This FFmpeg build does not expose the h264_nvenc hardware encoder.")

            hwaccel_process = await asyncio.create_subprocess_exec(
                ffmpeg_path,
                "-hide_banner", "-hwaccels",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            hwaccel_stdout, hwaccel_stderr = await asyncio.wait_for(hwaccel_process.communicate(), timeout=15)
            hwaccel_output = (
                hwaccel_stdout.decode("utf-8", errors="replace")
                + hwaccel_stderr.decode("utf-8", errors="replace")
            )
            if hwaccel_process.returncode != 0 or not any(
                line.strip().lower() == "cuda" for line in hwaccel_output.splitlines()
            ):
                raise RuntimeError("This FFmpeg build does not expose CUDA hardware acceleration.")

            # Exercise CUDA upload, CUDA scaling/pixel-format conversion, and NVENC itself.
            # Checking encoder names alone is insufficient when the NVIDIA driver/device is
            # unavailable in the runtime (common in CPU-only cloud instances).
            test_process = await asyncio.create_subprocess_exec(
                ffmpeg_path,
                "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                "-f", "lavfi", "-i", "color=c=black:s=640x360:r=1",
                "-vf", "format=nv12,hwupload_cuda,scale_cuda=format=nv12",
                "-frames:v", "1",
                "-an",
                "-c:v", "h264_nvenc",
                "-preset", "p5",
                "-tune", "hq",
                "-rc", "vbr",
                "-cq", "22",
                "-b:v", "0",
                "-f", "null", "-",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, test_stderr = await asyncio.wait_for(test_process.communicate(), timeout=30)
            except asyncio.TimeoutError:
                if test_process.returncode is None:
                    test_process.kill()
                await test_process.communicate()
                raise RuntimeError("The CUDA/NVENC startup test timed out after 30 seconds.")

            if test_process.returncode != 0:
                detail = sanitize_sensitive_urls(
                    test_stderr.decode("utf-8", errors="replace").strip()
                )[-1200:]
                raise RuntimeError(
                    "NVIDIA GPU video processing is required, but the CUDA/NVENC startup test failed. "
                    "Use a runtime with an available NVIDIA GPU, compatible drivers, and FFmpeg built with "
                    "CUDA, scale_cuda, hwupload_cuda, and h264_nvenc."
                    + (f" FFmpeg detail: {detail}" if detail else "")
                )

        except asyncio.CancelledError:
            raise
        except RuntimeError:
            raise
        except Exception as error:
            raise RuntimeError(
                "Could not verify the NVIDIA GPU video-processing pipeline. Ensure an NVIDIA GPU and its "
                "drivers are available and that the installed FFmpeg supports CUDA/NVENC."
            ) from error

        _GPU_PIPELINE_READY.add(ffmpeg_path)
        write_log(
            "INFO", Database, "VIDEO PROCESSING", "system",
            f"GPU pipeline verified for FFmpeg={ffmpeg_path!r}: CUDA decode/filter path and h264_nvenc encoding are available.",
        )
