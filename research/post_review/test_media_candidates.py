from labjarvis.ports.models import LabelResult, SpeechResult
from research.post_review.run_media_candidates import evaluate_speech, evaluate_vision


class SpeechAdapter:
    adapter_name = "speech-fixed"
    model_name = "speech-v1"

    def transcribe(self, audio: bytes, *, content_type: str) -> SpeechResult:
        del audio, content_type
        return SpeechResult(
            transcript="record the temperature as 37 degrees Celsius",
            confidence=None,
            adapter=self.adapter_name,
            model_name=self.model_name,
        )


class VisionAdapter:
    adapter_name = "vision-fixed"
    model_name = "vision-v1"

    def recognise(self, image: bytes, *, content_type: str) -> LabelResult:
        del image, content_type
        return LabelResult(
            visible_text="ethanol",
            confidence=0.9,
            adapter=self.adapter_name,
            model_name=self.model_name,
        )


def test_speech_summary_is_explicitly_synthetic(tmp_path) -> None:
    media = tmp_path / "clip.wav"
    media.write_bytes(b"audio")
    samples = [
        {
            "id": "s1",
            "expected_text": "record the temperature as 37 degrees Celsius",
            "content_type": "audio/wav",
            "duration_seconds": 2.0,
            "media_file": media,
        }
    ]

    result = evaluate_speech(SpeechAdapter(), samples)

    assert result["evidence_class"] == "synthetic_media_post_review"
    assert result["metrics"]["wer"] == 0
    assert result["sample_count"] == 1


def test_vision_summary_reports_false_accepts(tmp_path) -> None:
    media = tmp_path / "label.png"
    media.write_bytes(b"image")
    samples = [
        {
            "id": "v1",
            "printed_text": "methanol",
            "expected_reagent": "ethanol",
            "truth": "mismatch",
            "content_type": "image/png",
            "media_file": media,
        }
    ]

    result = evaluate_vision(VisionAdapter(), samples)

    assert result["evidence_class"] == "synthetic_media_post_review"
    assert result["metrics"]["false_accept_rate"] == 1.0
