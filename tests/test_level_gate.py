"""
Level gate tests — a synthetic episode whose second half is 30 dB quieter.

The fixture is pink noise with a 3 Hz amplitude wobble (a crude stand-in for
speech; a pure tone behaves unlike speech under dynaudnorm) that drops by
30 dB at the two-minute mark, rendered by ffmpeg in setUpClass. No network,
no Gemini: the TTS call is replaced by a stub that hands back the fixture's
bytes the way the endpoint would.

Run with either:
    python -m unittest discover -s tests
    pytest tests
Needs ffmpeg/ffprobe on PATH, as the pipeline does.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Same effective sys.path as `python src/main.py`: repo root (config) + src/.
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

from audio_levels import analyze, probe_duration  # noqa: E402
from gemini_audio import GeminiAudioGenerator  # noqa: E402

DURATION = 240      # seconds
DROP_AT = 120       # the quiet half starts here
DROP_DB = 30
SAMPLE_RATE = 24000  # what Gemini's PCM comes back as
TRUNCATED_DURATION = 90  # seconds


def script_of(words: int) -> str:
    """A two-speaker script with exactly `words` spoken words."""
    lines, left, speaker = [], words, "Host"
    while left:
        n = min(50, left)
        lines.append(f"{speaker}: " + " ".join(["word"] * n))
        left -= n
        speaker = "Cohost" if speaker == "Host" else "Host"
    return "\n".join(lines)


# 700 words: 175 wpm over the full 4-minute fixture (a normal read), 467 wpm
# over the 90-second one (most of the script missing).
FULL_SCRIPT = script_of(700)


def _ffmpeg(*args):
    subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *args],
        check=True,
    )


def _source_filters(with_drop: bool) -> str:
    speechlike = "volume='0.55+0.45*sin(2*PI*3*t)':eval=frame"
    if not with_drop:
        return speechlike
    return f"{speechlike},volume=enable='gte(t,{DROP_AT})':volume=-{DROP_DB}dB"


def make_synthetic(path: str, with_drop: bool, pcm: bool = False,
                   duration: int = DURATION) -> None:
    """Write the fixture as MP3, or as headerless 16-bit PCM when pcm=True."""
    codec = (["-f", "s16le", "-acodec", "pcm_s16le"] if pcm
             else ["-codec:a", "libmp3lame", "-q:a", "2"])
    _ffmpeg(
        "-f", "lavfi",
        "-i", f"anoisesrc=color=pink:seed=42:sample_rate={SAMPLE_RATE}"
              f":duration={duration}:amplitude=0.5",
        "-af", _source_filters(with_drop),
        *codec, path,
    )


class _Inline:
    def __init__(self, data: bytes, mime_type: str):
        self.data = data
        self.mime_type = mime_type


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def fake_response(data: bytes, mime_type: str):
    """Shape of google.genai's GenerateContentResponse, as far as we read it."""
    part = _Obj(inline_data=_Inline(data, mime_type))
    return _Obj(candidates=[_Obj(content=_Obj(parts=[part]))])


def bare_generator() -> GeminiAudioGenerator:
    """A generator with no API clients; TTS is stubbed in every test."""
    return GeminiAudioGenerator.__new__(GeminiAudioGenerator)


def copy_master(raw_path: str, output_path: str) -> None:
    """A mastering stand-in that repairs nothing, to exercise the gate's retry."""
    shutil.copyfile(raw_path, output_path)


class LevelGateTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            raise unittest.SkipTest("ffmpeg/ffprobe not on PATH")
        cls.fixtures = tempfile.mkdtemp(prefix="level-gate-fixtures-")
        cls.bad_mp3 = os.path.join(cls.fixtures, "bad.mp3")
        cls.good_mp3 = os.path.join(cls.fixtures, "good.mp3")
        cls.good_pcm = os.path.join(cls.fixtures, "good.pcm")
        make_synthetic(cls.bad_mp3, with_drop=True)
        make_synthetic(cls.good_mp3, with_drop=False)
        make_synthetic(cls.good_pcm, with_drop=False, pcm=True)
        # A render that stopped part-way: level throughout, just too short.
        cls.short_mp3 = os.path.join(cls.fixtures, "short.mp3")
        make_synthetic(cls.short_mp3, with_drop=False, duration=TRUNCATED_DURATION)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.fixtures, ignore_errors=True)

    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="level-gate-")
        self.out = os.path.join(self.work, "episode.mp3")

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)

    @staticmethod
    def _read(path: str) -> bytes:
        with open(path, "rb") as fh:
            return fh.read()

    def _leftovers(self):
        return sorted(os.listdir(self.work))

    # --- the check itself -------------------------------------------------

    def test_synthetic_drop_is_flagged(self):
        profile = analyze(self.bad_mp3)
        self.assertTrue(profile.flagged)
        drop = profile.worst
        # 20 s windows: the onset lands on the window containing DROP_AT.
        self.assertGreaterEqual(drop.onset, DROP_AT - 20)
        self.assertLessEqual(drop.onset, DROP_AT)
        self.assertAlmostEqual(drop.drop_db, DROP_DB, delta=5)
        self.assertGreaterEqual(drop.duration, DURATION - DROP_AT - 20)

    def test_level_half_is_not_flagged(self):
        self.assertFalse(analyze(self.good_mp3).flagged)

    # --- mastering ----------------------------------------------------------

    def test_mastering_lifts_the_quiet_half(self):
        gen = bare_generator()
        gen._master_audio(self.bad_mp3, self.out)
        profile = analyze(self.out)
        self.assertFalse(
            profile.flagged,
            f"still flagged after mastering: {profile.drops}",
        )
        self.assertAlmostEqual(probe_duration(self.out), DURATION, delta=1.0)

    # --- the gate in generate_audio ----------------------------------------

    def test_gate_retries_tts_once_then_fails_the_episode(self):
        gen = bare_generator()
        bad = self._read(self.bad_mp3)
        with mock.patch.object(gen, "_master_audio", side_effect=copy_master), \
             mock.patch.object(
                 gen, "_generate_with_retry",
                 side_effect=[fake_response(bad, "audio/mp3")] * 2,
             ) as tts:
            ok = gen.generate_audio("Host: hi\nCohost: hello", self.out)

        self.assertFalse(ok)
        self.assertEqual(tts.call_count, 2, "exactly one retry")
        self.assertEqual(self._leftovers(), [], "no file left for the uploader")

    def test_gate_accepts_a_clean_second_render(self):
        gen = bare_generator()
        bad, good = self._read(self.bad_mp3), self._read(self.good_mp3)
        with mock.patch.object(gen, "_master_audio", side_effect=copy_master), \
             mock.patch.object(
                 gen, "_generate_with_retry",
                 side_effect=[fake_response(bad, "audio/mp3"),
                              fake_response(good, "audio/mp3")],
             ) as tts:
            ok = gen.generate_audio("Host: hi\nCohost: hello", self.out)

        self.assertTrue(ok)
        self.assertEqual(tts.call_count, 2)
        self.assertEqual(self._leftovers(), ["episode.mp3"], "raw render cleaned up")

    def test_gate_retry_is_charged_to_the_tts_budget(self):
        gen = bare_generator()
        gen.TTS_TOTAL_BUDGET_SECONDS = 0  # the first render spends it all
        bad = self._read(self.bad_mp3)
        with mock.patch.object(gen, "_master_audio", side_effect=copy_master), \
             mock.patch.object(
                 gen, "_generate_with_retry",
                 side_effect=[fake_response(bad, "audio/mp3")] * 2,
             ) as tts:
            ok = gen.generate_audio("Host: hi\nCohost: hello", self.out)

        self.assertFalse(ok)
        self.assertEqual(tts.call_count, 1, "no budget left for a second render")
        self.assertEqual(self._leftovers(), [])

    def test_shared_deadline_reaches_the_tts_call(self):
        gen = bare_generator()
        good = self._read(self.good_mp3)
        with mock.patch.object(gen, "_master_audio", side_effect=copy_master), \
             mock.patch.object(
                 gen, "_generate_with_retry",
                 return_value=fake_response(good, "audio/mp3"),
             ) as tts:
            self.assertTrue(gen.generate_audio("Host: hi", self.out))
        self.assertIn("deadline", tts.call_args.kwargs)
        self.assertIsInstance(tts.call_args.kwargs["deadline"], float)

    def test_pcm_render_is_mastered_too(self):
        """The WAV fallback goes through the same _master_audio as direct MP3."""
        gen = bare_generator()
        pcm = self._read(self.good_pcm)
        with mock.patch.object(
            gen, "_generate_with_retry",
            return_value=fake_response(pcm, f"audio/L16;codec=pcm;rate={SAMPLE_RATE}"),
        ):
            ok = gen.generate_audio("Host: hi\nCohost: hello", self.out)

        self.assertTrue(ok)
        self.assertEqual(self._leftovers(), ["episode.mp3"], "raw WAV cleaned up")
        rate = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=sample_rate",
             "-of", "default=noprint_wrappers=1:nokey=1", self.out],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        self.assertEqual(rate, str(gen.MASTER_SAMPLE_RATE),
                         "output carries the mastering sample rate, not the PCM's")
        self.assertFalse(analyze(self.out).flagged)


    # --- the completeness gate -------------------------------------------

    def test_word_count_ignores_labels_and_stage_directions(self):
        self.assertEqual(
            GeminiAudioGenerator.script_word_count(
                "Host: Hello there [laughs] friend\nCohost: Hi.\n  Host:  ok"),
            5,
        )
        self.assertEqual(GeminiAudioGenerator.script_word_count(FULL_SCRIPT), 700)

    def test_truncated_render_is_retried_then_fails(self):
        gen = bare_generator()
        short = self._read(self.short_mp3)
        with mock.patch.object(gen, "_master_audio", side_effect=copy_master), \
             mock.patch.object(
                 gen, "_generate_with_retry",
                 side_effect=[fake_response(short, "audio/mp3")] * 2,
             ) as tts:
            ok = gen.generate_audio(FULL_SCRIPT, self.out)

        self.assertFalse(ok)
        self.assertEqual(tts.call_count, 2, "exactly one retry")
        self.assertEqual(self._leftovers(), [], "no file left for the uploader")

    def test_complete_second_render_is_accepted(self):
        gen = bare_generator()
        short, good = self._read(self.short_mp3), self._read(self.good_mp3)
        with mock.patch.object(gen, "_master_audio", side_effect=copy_master), \
             mock.patch.object(
                 gen, "_generate_with_retry",
                 side_effect=[fake_response(short, "audio/mp3"),
                              fake_response(good, "audio/mp3")],
             ) as tts:
            ok = gen.generate_audio(FULL_SCRIPT, self.out)

        self.assertTrue(ok)
        self.assertEqual(tts.call_count, 2)
        self.assertEqual(self._leftovers(), ["episode.mp3"])

    def test_short_script_is_not_judged_on_rate(self):
        """A few lines over a long file is not evidence of anything."""
        gen = bare_generator()
        self.assertIsNone(gen._check_completeness(script_of(50), 10.0))


class TransientErrorTests(unittest.TestCase):
    """_generate_with_retry: which failures earn another TTS attempt."""

    def _gen(self, side_effect):
        gen = bare_generator()
        gen.client = mock.Mock()
        gen.client.models.generate_content.side_effect = side_effect
        return gen

    def test_dropped_connection_is_retried(self):
        import httpx
        dropped = httpx.RemoteProtocolError(
            "peer closed connection without sending complete message body "
            "(incomplete chunked read)")
        gen = self._gen([dropped, "audio"])
        with mock.patch("gemini_audio.time.sleep"):
            self.assertEqual(gen._generate_with_retry(model="m"), "audio")
        self.assertEqual(gen.client.models.generate_content.call_count, 2)

    def test_non_transient_error_is_not_retried(self):
        gen = self._gen([ValueError("400 INVALID_ARGUMENT")])
        with mock.patch("gemini_audio.time.sleep"):
            with self.assertRaises(ValueError):
                gen._generate_with_retry(model="m")
        self.assertEqual(gen.client.models.generate_content.call_count, 1)


if __name__ == "__main__":
    unittest.main()
