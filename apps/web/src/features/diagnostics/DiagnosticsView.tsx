import { ModelStages } from "../../components/ModelStages";
import { Eye, FileScan, MessageSquareText, Mic2 } from "lucide-react";

import { MODE_LABELS } from "../../components/AppShell";
import { StatusIndicator } from "../../components/StatusIndicator";
import type { ReadinessResponse } from "../../types";
import { ReadinessDetail } from "./ReadinessDetail";


interface DiagnosticsViewProps {
  readiness: ReadinessResponse;
  degraded: boolean;
  onRetry: () => void;
}

const adapterIcons = {
  speech: Mic2,
  text: MessageSquareText,
  vision: Eye,
  document_ocr: FileScan,
};

export function DiagnosticsView({ readiness, degraded, onRetry }: DiagnosticsViewProps) {
  return (
    <main className="diagnostics-view" id="main-content">
      <header className="view-heading">
        <div>
          <h1>Diagnostics</h1>
          <p>Adapter readiness only. Protocol text, transcripts, media, and credentials are excluded from this view.</p>
        </div>
        <StatusIndicator
          label={readiness.status === "ready" ? "System ready" : "Attention required"}
          tone={readiness.status === "ready" ? "ready" : "warning"}
        />
      </header>
      <div className="diagnostic-list">
        {Object.entries(readiness.adapters).map(([name, state]) => {
          const Icon = adapterIcons[name as keyof typeof adapterIcons] ?? FileScan;
          return (
            <div key={name}>
              <Icon aria-hidden="true" />
              <strong>{name}</strong>
              {state.model_name !== undefined ? (
                <span className="diagnostic-model">{state.model_name}</span>
              ) : null}
              <StatusIndicator
                label={state.ready ? "Ready" : "Unavailable"}
                tone={state.ready ? "ready" : "danger"}
              />
              {state.detail !== undefined && !state.ready ? (
                <span className="diagnostic-detail">{state.detail}</span>
              ) : null}
            </div>
          );
        })}
      </div>
      <section className="diagnostic-boundaries" aria-labelledby="latency-window-title">
        <h2 id="latency-window-title">Recent model latency</h2>
        {readiness.latency_window === undefined ? (
          <p>No latency window was reported.</p>
        ) : (
          <>
            <p>
              Content-free aggregates bounded to {readiness.latency_window.max_samples} stored
              inference records; no request content is included.
            </p>
            <ul>
              {Object.entries(readiness.latency_window.modalities).map(([modality, sample]) => (
                <li key={modality}>
                  <strong>{modality}</strong>: {sample.sample_count} samples, average {sample.average_ms} ms,
                  p95 {sample.p95_ms} ms, {sample.failed_count} failed
                </li>
              ))}
            </ul>
          </>
        )}
      </section>
      {readiness.hands_free?.summary ? (
        <section className="diagnostic-boundaries" aria-labelledby="hands-free-readiness-title">
          <h2 id="hands-free-readiness-title">Hands-free</h2>
          <p className="hands-free-summary" role="status">
            {readiness.hands_free.summary}
          </p>
          {readiness.hands_free.missing !== undefined && readiness.hands_free.missing.length > 0 ? (
            <ul>
              {readiness.hands_free.missing.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          ) : null}
        </section>
      ) : null}
      <section className="diagnostic-stages">
        <ModelStages readiness={readiness} />
      </section>
      <section className="diagnostic-boundaries">
        <h2>Privacy and retention</h2>
        <dl>
          <div>
            <dt>Execution mode</dt>
            <dd>{MODE_LABELS[readiness.mode]}</dd>
          </div>
          <div>
            <dt>Model processing</dt>
            <dd>
              {readiness.privacy.external_model_processing
                ? `External: audio, transcript context, and label images are sent to ${
                    readiness.hosted_provider ?? "the configured provider"
                  } during an operator-initiated action`
                : "On this machine only; no model data leaves this device"}
            </dd>
          </div>
          {readiness.hosted_provider !== undefined ? (
            <div>
              <dt>Hosted provider</dt>
              <dd>{readiness.hosted_provider}</dd>
            </div>
          ) : null}
          <div>
            <dt>Optional wake phrase</dt>
            <dd>{readiness.interaction.wake_phrase}</dd>
          </div>
          <div>
            <dt>Local raw media retention</dt>
            <dd>
              {readiness.privacy.raw_media_retention
                ? "Enabled by explicit operator configuration"
                : "Disabled; uploads are processed in memory"}
            </dd>
          </div>
          <div>
            <dt>Local database retention</dt>
            <dd>
              {readiness.privacy.external_model_processing
                ? "Governs this machine only; provider-side retention is governed by the configured provider account terms"
                : "Governs all stored notebook, audit, and media records"}
            </dd>
          </div>
          <div>
            <dt>Normal logs</dt>
            <dd>Metadata and correlation identifiers only; never credentials</dd>
          </div>
          <div>
            <dt>Local transcript retention</dt>
            <dd>{readiness.privacy.transcript_retention ? "Enabled locally" : "Disabled"}</dd>
          </div>
        </dl>
      </section>
      <ReadinessDetail readiness={readiness} degraded={degraded} onRetry={onRetry} />
    </main>
  );
}
