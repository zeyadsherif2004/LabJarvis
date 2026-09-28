import type { ReadinessResponse, StageProvenance } from "../types";

const STAGE_NAMES: Record<string, string> = {
  speech: "Speech",
  text: "Text",
  vision: "Vision",
};

export function profileLabel(readiness: ReadinessResponse): string {
  switch (readiness.profile) {
    case "hybrid":
      return "Hybrid (local + hosted)";
    case "hosted":
      return "Hosted API";
    case "local":
      return "Local models";
    case "fake":
      return "Development fake";
    default:
      return readiness.mode;
  }
}

function where(stage: StageProvenance): string {
  if (stage.locality === "hosted") return `hosted by ${stage.provider_label}`;
  if (stage.locality === "fake") return "deterministic fixture";
  // A loopback OpenAI-compatible server (vLLM, LM Studio, ...) is local.
  if (stage.provider !== "local") return `on this machine via ${stage.provider_label}`;
  return "on this machine";
}

/**
 * Which model runs each stage, and where. A hosted stage is always named as
 * hosted, with its provider, so a hybrid profile can never look fully local.
 */
export function ModelStages({ readiness, compact = false }: { readiness: ReadinessResponse; compact?: boolean }) {
  const stages = Object.entries(readiness.stages ?? {});
  if (stages.length === 0) return null;
  if (compact) {
    return (
      <span className="model-stages-compact">
        {stages.map(([name, stage]) => (
          <span
            key={name}
            className={`stage-chip stage-chip--${stage.locality}`}
            title={`${STAGE_NAMES[name] ?? name}: ${stage.model} (${where(stage)})`}
          >
            {STAGE_NAMES[name] ?? name}: {stage.locality === "hosted" ? stage.provider_label : stage.locality}
          </span>
        ))}
      </span>
    );
  }
  return (
    <table className="model-stages">
      <caption>Model stages ({profileLabel(readiness)})</caption>
      <thead>
        <tr>
          <th scope="col">Stage</th>
          <th scope="col">Model</th>
          <th scope="col">Runs</th>
          <th scope="col">Details</th>
          <th scope="col">Warm-up</th>
        </tr>
      </thead>
      <tbody>
        {stages.map(([name, stage]) => (
          <tr key={name}>
            <th scope="row">{STAGE_NAMES[name] ?? name}</th>
            <td>{stage.model}</td>
            <td>{where(stage)}</td>
            <td>{stage.detail || "-"}</td>
            <td>{stage.warmup}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
