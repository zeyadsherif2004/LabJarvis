"""Strict evaluation configuration and dataset contracts."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Modality = Literal["speech", "text", "vision"]
AdapterKind = Literal["fake", "faster-whisper", "ollama", "easyocr", "openai"]
ExecutionLocation = Literal["local", "remote"]


class StrictEvaluationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class EvaluationCandidate(StrictEvaluationModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,79}$")
    modality: Modality
    adapter: AdapterKind
    model_name: str = Field(min_length=1, max_length=200)
    enabled: bool = True
    device: str = Field(default="cpu", min_length=1, max_length=40)
    compute_type: str = Field(default="int8", min_length=1, max_length=40)
    provider: str | None = Field(default=None, max_length=40)
    execution_location: ExecutionLocation = "local"

    @model_validator(mode="after")
    def adapter_matches_modality(self) -> Self:
        # "openai" is deliberately absent: the hosted provider serves all three
        # modalities, so its modality is determined by the configured model.
        expected_modality = {
            "faster-whisper": "speech",
            "ollama": "text",
            "easyocr": "vision",
        }.get(self.adapter)
        if expected_modality is not None and self.modality != expected_modality:
            raise ValueError(f"{self.adapter} is not a {self.modality} adapter")
        if self.adapter == "openai":
            if self.execution_location != "remote":
                raise ValueError("hosted candidates must record execution_location 'remote'")
            if not self.provider:
                raise ValueError("hosted candidates must record the provider")
        elif self.execution_location != "local":
            raise ValueError(f"{self.adapter} runs locally and cannot be marked remote")
        return self


class EvaluationConfig(StrictEvaluationModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,79}$")
    evidence_class: Literal["synthetic_development_only", "student_collected"]
    eligible_for_final_claims: bool = False
    include_predictions: bool = False
    allow_hosted_candidates: bool = False
    datasets: dict[Modality, str]
    candidates: list[EvaluationCandidate] = Field(min_length=1)

    @model_validator(mode="after")
    def synthetic_evidence_cannot_be_claim_eligible(self) -> Self:
        if self.evidence_class == "synthetic_development_only" and self.eligible_for_final_claims:
            raise ValueError("synthetic development evidence cannot support final claims")
        remote = [
            candidate
            for candidate in self.candidates
            if candidate.enabled and candidate.execution_location == "remote"
        ]
        if remote and not self.allow_hosted_candidates:
            raise ValueError(
                "hosted candidates require explicit opt-in via allow_hosted_candidates; "
                "evaluating them sends data to an external provider and may incur cost"
            )
        enabled_modalities = {
            candidate.modality for candidate in self.candidates if candidate.enabled
        }
        if not enabled_modalities.issubset(self.datasets):
            raise ValueError("every enabled candidate requires a dataset")
        return self


class EvaluationSample(StrictEvaluationModel):
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
    modality: Modality
    synthetic: bool
    transcript: str | None = None
    expected_text: str | None = None
    expected_intent: str | None = None
    expected_parameters: dict[str, str] = Field(default_factory=dict)
    media_path: str | None = None
    content_type: str | None = None
    duration_seconds: float | None = Field(default=None, gt=0)
    active_instruction: str = "Follow the reviewed protocol step."
    expected_reagent: str | None = None
    expected_verification: Literal["MATCH", "MISMATCH", "UNCERTAIN"] | None = None

    @model_validator(mode="after")
    def require_modality_fields(self) -> Self:
        if self.modality == "speech" and self.expected_text is None:
            raise ValueError("speech samples require expected_text")
        if self.modality == "text" and (
            self.transcript is None or self.expected_intent is None
        ):
            raise ValueError("text samples require transcript and expected_intent")
        if self.modality == "vision" and (
            self.expected_text is None
            or self.expected_reagent is None
            or self.expected_verification is None
        ):
            raise ValueError("vision samples require text, reagent, and verification labels")
        return self
