import { AlertTriangle, CheckCircle2, Circle, MinusCircle, RotateCcw } from "lucide-react";

import type { AdapterReadiness, ReadinessResponse, ReadinessStage } from "../../types";

interface ReadinessDetailProps {
  readiness: ReadinessResponse;
  degraded: boolean;
  onRetry: () => void;
}

/**
 * Stages in the order a model actually becomes usable. Reporting which one is
 * unmet is the difference between an actionable message and a bare "not ready".
 */
const STAGES: { key: keyof AdapterReadiness; label: string }[] = [
  { key: "configured", label: "Configured" },
  { key: "installed", label: "Dependency installed" },
  { key: "cached", label: "Model downloaded" },
  { key: "loaded", label: "Model loaded" },
  { key: "reachable", label: "Provider reachable" },
  { key: "inference_verified", label: "Inference verified" },
];

function StageIcon({ stage }: { stage: ReadinessStage }) {
  if (stage.status === "ready") return <CheckCircle2 className="stage-icon stage-icon--ok" aria-hidden="true" />;
  if (stage.status === "blocked") return <AlertTriangle className="stage-icon stage-icon--bad" aria-hidden="true" />;
  return <MinusCircle className="stage-icon stage-icon--na" aria-hidden="true" />;
}

function stageText(stage: ReadinessStage): string {
  if (stage.status === "ready") return "ready";
  if (stage.status === "blocked") return `blocked — ${stage.detail}`;
  return `not applicable — ${stage.detail}`;
}

function AdapterCard({ name, adapter }: { name: string; adapter: AdapterReadiness }) {
  return (
    <article className={adapter.ready ? "adapter-card" : "adapter-card adapter-card--blocked"}>
      <header>
        <h3>
          {adapter.ready ? (
            <CheckCircle2 aria-hidden="true" />
          ) : (
            <Circle aria-hidden="true" />
          )}
          {name}
        </h3>
        <p className="adapter-model">
          <code>{adapter.model_name ?? "unknown"}</code>
          {adapter.adapter ? <small> via {adapter.adapter}</small> : null}
        </p>
      </header>

      <p className="adapter-detail">{adapter.detail ?? "No detail reported."}</p>

      <dl className="stage-list">
        {STAGES.map(({ key, label }) => {
          const stage = adapter[key] as ReadinessStage | undefined;
          if (stage === undefined) return null;
          return (
            <div className="stage" key={key}>
              <dt>
                <StageIcon stage={stage} />
                {label}
              </dt>
              <dd>{stageText(stage)}</dd>
            </div>
          );
        })}
      </dl>
    </article>
  );
}

export function ReadinessDetail({ readiness, degraded, onRetry }: ReadinessDetailProps) {
  return (
    <section className="readiness-detail" aria-labelledby="readiness-detail-title">
      <div className="readiness-heading">
        <h2 id="readiness-detail-title">Model readiness</h2>
        <button className="button button--secondary" type="button" onClick={onRetry}>
          <RotateCcw aria-hidden="true" /> Re-check
        </button>
      </div>

      {degraded ? (
        <div className="readiness-degraded" role="status">
          <AlertTriangle aria-hidden="true" />
          <div>
            {/* The API answered. Saying it is unreachable would send the operator
                to diagnose the wrong thing entirely. */}
            <strong>The API is running, but a model is not usable yet</strong>
            <p>
              LabJarvis answered this check and reported which prerequisite is missing
              below. Typed control and protocol review still work.
            </p>
          </div>
        </div>
      ) : null}

      {!readiness.database.ready ? (
        <p className="readiness-database" role="alert">
          The database is not reachable. Run the documented migration step, then re-check.
        </p>
      ) : null}

      <div className="adapter-grid">
        <AdapterCard name="Speech" adapter={readiness.adapters.speech} />
        <AdapterCard name="Reasoning" adapter={readiness.adapters.text} />
        <AdapterCard name="Vision / OCR" adapter={readiness.adapters.vision} />
        {readiness.adapters.document_ocr !== undefined ? (
          <AdapterCard name="Scanned document OCR" adapter={readiness.adapters.document_ocr} />
        ) : null}
      </div>
    </section>
  );
}
