import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from imageio_ffmpeg import get_ffmpeg_exe

from utils.video_compression import DIDA_VIDEO_MAX_BYTES, VideoCompressionError, compress_video


ROOT = Path(__file__).resolve().parents[1]
MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"


class VideoCompressionTest(unittest.TestCase):
    def setUp(self):
        cache = ROOT / ".test-tmp"
        cache.mkdir(exist_ok=True)
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory(dir=cache)))
        self.enterContext(patch("utils.video_compression._PROJECT_CACHE", self.directory))
        self.read_metadata = self.enterContext(patch(
            "utils.video_compression._read_metadata",
            return_value={"source_size": (1080, 1920), "duration": 77.88},
        ))
        self.source = MP4 + bytes(DIDA_VIDEO_MAX_BYTES - len(MP4))

    def encoder(self, output_sizes):
        sizes = iter(output_sizes)
        def encode(arguments, stage):
            source_path = Path(arguments[arguments.index("-i") + 1])
            self.assertEqual(source_path.read_bytes(), self.source)
            if "-pass" in arguments and arguments[arguments.index("-pass") + 1] == "1":
                return
            with Path(arguments[-1]).open("wb") as output:
                output.write(MP4)
                output.truncate(next(sizes))
        return encode

    def test_below_limit_is_unchanged_and_starts_no_encoder(self):
        source = self.source[:-1]
        with patch("utils.video_compression._run_ffmpeg") as encode:
            self.assertIs(compress_video(source), source)
        self.read_metadata.assert_not_called()
        encode.assert_not_called()

    def test_exact_limit_uses_crf23_and_keeps_audio_settings(self):
        with patch("utils.video_compression._run_ffmpeg", side_effect=self.encoder([len(MP4)])) as encode:
            self.assertEqual(compress_video(self.source), MP4)
        encode.assert_called_once()
        arguments = encode.call_args.args[0]
        for option, value in (("-crf", "23"), ("-c:v", "libx264"), ("-c:a", "aac"),
                              ("-b:a", "64000"), ("-preset", "medium")):
            self.assertEqual(arguments[arguments.index(option) + 1], value)
        self.assertNotIn("-b:v", arguments)
        self.assertNotIn("-r", arguments)
        self.assertEqual(arguments[arguments.index("-vf") + 1], "scale=720:1280:flags=lanczos,setsar=1")
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_crf_over_limit_continues_past_two_two_pass_rounds(self):
        sizes = [DIDA_VIDEO_MAX_BYTES + 1000, DIDA_VIDEO_MAX_BYTES + 400,
                 DIDA_VIDEO_MAX_BYTES + 1, DIDA_VIDEO_MAX_BYTES, len(MP4)]
        with patch("utils.video_compression._run_ffmpeg", side_effect=self.encoder(sizes)) as encode:
            self.assertEqual(compress_video(self.source), MP4)
        second_passes = [call.args[0] for call in encode.call_args_list
                         if "-pass" in call.args[0] and call.args[0][call.args[0].index("-pass") + 1] == "2"]
        self.assertEqual(len(second_passes), 4)
        rates = [int(arguments[arguments.index("-b:v") + 1]) for arguments in second_passes]
        self.assertTrue(all(0 < next_rate < rate for rate, next_rate in zip(rates, rates[1:])))
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_crf_failure_falls_back_and_two_pass_failure_propagates(self):
        succeed = self.encoder([len(MP4)])
        def crf_fails(arguments, stage):
            if "-crf" in arguments:
                raise VideoCompressionError("CRF failed")
            succeed(arguments, stage)
        with patch("utils.video_compression._run_ffmpeg", side_effect=crf_fails):
            self.assertEqual(compress_video(self.source), MP4)
        with patch("utils.video_compression._run_ffmpeg", side_effect=VideoCompressionError("encoder failed")) as encode:
            with self.assertRaisesRegex(VideoCompressionError, "encoder failed"):
                compress_video(self.source)
        self.assertEqual(encode.call_count, 2)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_invalid_crf_output_falls_back(self):
        succeed = self.encoder([len(MP4)])
        def invalid_crf(arguments, stage):
            if "-crf" in arguments:
                Path(arguments[-1]).write_bytes(b"invalid MP4")
            else:
                succeed(arguments, stage)
        with patch("utils.video_compression._run_ffmpeg", side_effect=invalid_crf) as encode:
            self.assertEqual(compress_video(self.source), MP4)
        self.assertEqual(encode.call_count, 3)

    def test_unavailable_duration_does_not_prevent_successful_crf(self):
        self.read_metadata.return_value["duration"] = 0
        with patch("utils.video_compression._run_ffmpeg", side_effect=self.encoder([len(MP4)])):
            self.assertEqual(compress_video(self.source), MP4)

    def test_invalid_two_pass_duration_is_an_error(self):
        for duration in (None, 0, float("inf"), float("nan")):
            with self.subTest(duration=duration):
                self.read_metadata.return_value["duration"] = duration
                with patch("utils.video_compression._run_ffmpeg", side_effect=self.encoder([DIDA_VIDEO_MAX_BYTES])):
                    with self.assertRaisesRegex(VideoCompressionError, "有效的视频时长"):
                        compress_video(self.source)


class BundledFfmpegTest(unittest.TestCase):
    def test_real_crf_output_keeps_complete_decodable_video_and_audio(self):
        cache = ROOT / ".test-tmp"
        cache.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=cache) as temp_dir:
            directory = Path(temp_dir)
            source = directory / "source.mp4"
            output = directory / "output.mp4"
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            generated = subprocess.run(
                [get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
                 "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=10:duration=1",
                 "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=44100:duration=1",
                 "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                 "-c:a", "aac", "-shortest", str(source)],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=flags, check=False,
            )
            self.assertEqual(generated.returncode, 0, generated.stderr.decode(errors="replace"))
            with source.open("ab") as stream:
                stream.truncate(DIDA_VIDEO_MAX_BYTES)
            compressed = compress_video(source.read_bytes())
            self.assertLess(len(compressed), DIDA_VIDEO_MAX_BYTES)
            output.write_bytes(compressed)
            decoded = subprocess.run(
                [get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-xerror", "-i", str(output),
                 "-map", "0:v:0", "-map", "0:a:0", "-f", "null", os.devnull],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=flags, check=False,
            )
            self.assertEqual(decoded.returncode, 0, decoded.stderr.decode(errors="replace"))


if __name__ == "__main__":
    unittest.main()
