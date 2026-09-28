import { useRef, useState } from "react";
import { AlertTriangle, Camera, CheckCircle2, FileUp, Mic, ShieldCheck } from "lucide-react";

import { MODE_LABELS } from "../../components/AppShell";
import type { ActionResult } from "../../actions";
import type { ReadinessResponse } from "../../types";
import { developmentProtocolFile } from "./developmentSample";


interface SetupViewProps {
  readiness: ReadinessResponse;
  busy: boolean;
  onImport: (file: File, useOcr: boolean) => Promise<ActionResult>;
}

export function SetupView({ readiness, busy, onImport }: SetupViewProps) {
  const adapters = Object.entries(readiness.adapters);
  const fileInput = useRef<HTMLInputElement>(null);
  const [useOcr, setUseOcr] = useState(false);
  const [deviceState, setDeviceState] = useState<Record<"microphone" | "camera", string>>({
    microphone: "Not checked",
    camera: "Not checked",
  });

  async function checkDevice(kind: "microphone" | "camera"): Promise<void> {
    if (navigator.mediaDevices?.getUserMedia === undefined) {
      setDeviceState((current) => ({ ...current, [kind]: "Unavailable in this browser" }));
      return;
    }
    setDeviceState((current) => ({ ...current, [kind]: "Checking permission..." }));
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: kind === "microphone",
        video: kind === "camera",
      });
      stream.getTracks().forEach((track) => track.stop());
      setDeviceState((current) => ({ ...current, [kind]: "Available" }));
    } catch (error) {
      const denied = error instanceof DOMException && error.name === "NotAllowedError";
      setDeviceState((current) => ({
        ...current,
        [kind]: denied ? "Permission denied" : "Unavailable",
      }));
    }
  }
  return (
    <main className="setup-view" id="main-content">
      <section className="setup-intro" aria-labelledby="system-check-title">
        <div>
          <h1 id="system-check-title">System check</h1>
          <p>
            Confirm model adapters, then import a reviewed protocol document to begin a session.
          </p>
        </div>
        <div className="readiness-summary" role="status">
          <CheckCircle2 aria-hidden="true" />
          <span>{MODE_LABELS[readiness.mode]} mode</span>
          <strong>{readiness.status === "ready" ? "Ready" : "Attention required"}</strong>
        </div>
      </section>

      {readiness.privacy.external_model_processing ? (
        <section className="privacy-warning" aria-labelledby="external-processing-title">
          <AlertTriangle aria-hidden="true" />
          <div>
            <h2 id="external-processing-title">External model processing is enabled</h2>
            <p>
              Audio, transcript context, and label images are transmitted to{" "}
              {readiness.hosted_provider ?? "the configured provider"} when you start an
              action. Nothing is sent before you do.
            </p>
            <p>
              Provider-side retention, training, residency, and deletion are governed by that
              account&apos;s terms, not by LabJarvis. Institutional approval may be required
              before sending laboratory or participant data.
            </p>
          </div>
        </section>
      ) : null}

      <section className="adapter-list" aria-label="Adapter readiness">
        {adapters.map(([name, state]) => (
          <div className="adapter-row" key={name}>
            {state.ready ? (
              <CheckCircle2 aria-hidden="true" className="status-icon status-icon--ready" />
            ) : (
              <AlertTriangle aria-hidden="true" className="status-icon status-icon--warning" />
            )}
            <span>{name}</span>
            <strong>{state.ready ? "Ready" : "Unavailable"}</strong>
          </div>
        ))}
      </section>

      <section className="device-checks" aria-labelledby="device-checks-title">
        <div>
          <h2 id="device-checks-title">Browser devices</h2>
          <p>Test permissions before gloving. Each test releases the device immediately.</p>
        </div>
        <button
          className="button button--secondary"
          type="button"
          onClick={() => void checkDevice("microphone")}
        >
          <Mic aria-hidden="true" /> Test microphone
        </button>
        <span role="status">{deviceState.microphone}</span>
        <button
          className="button button--secondary"
          type="button"
          onClick={() => void checkDevice("camera")}
        >
          <Camera aria-hidden="true" /> Test camera
        </button>
        <span role="status">{deviceState.camera}</span>
      </section>

      <section className="privacy-summary" aria-labelledby="setup-privacy-title">
        <ShieldCheck aria-hidden="true" />
        <div>
          <h2 id="setup-privacy-title">Local retention settings</h2>
          <p>
            Raw audio and images: {readiness.privacy.raw_media_retention ? "retained" : "discarded"}.
            Transcripts: {readiness.privacy.transcript_retention ? "retained locally" : "not retained"}.
          </p>
          <p>
            {readiness.privacy.external_model_processing
              ? "These settings control this machine's database and media store only. They do not affect what the hosted provider does with data sent for inference."
              : "Model inference runs on this machine. Nothing is sent to an external provider."}
          </p>
        </div>
      </section>

      <section className="setup-boundaries" aria-labelledby="safety-boundaries-title">
        <ShieldCheck aria-hidden="true" />
        <div>
          <h2 id="safety-boundaries-title">Operator control stays explicit</h2>
          <p>
            LabJarvis reads visible label text only. It does not identify container contents,
            assess sterility, or replace protocol review. Consequential commands require a separate
            confirmation.
          </p>
        </div>
      </section>

      <section className="protocol-import" aria-labelledby="protocol-import-title">
        <FileUp aria-hidden="true" />
        <div>
          <h2 id="protocol-import-title">Import a protocol</h2>
          <p>
            Accepted formats: PDF, Word (DOCX), plain text, or Markdown - your protocol as it is.
            Numbered or bulleted steps are found automatically; anything unclear is flagged and
            any text not used as a step is kept for you to review. OCR can be enabled explicitly
            for scanned PDFs.
          </p>
        </div>
        <div className="import-action">
          <label className="visually-hidden" htmlFor="protocol-document">
            Protocol document
          </label>
          <input
            id="protocol-document"
            ref={fileInput}
            className="visually-hidden"
            type="file"
            accept=".pdf,.docx,.txt,.md,.markdown,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain,text/markdown"
            disabled={busy || readiness.status !== "ready"}
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file !== undefined) void onImport(file, useOcr);
            }}
          />
          <label className="dashboard-filter-check">
            <input
              type="checkbox"
              checked={useOcr}
              disabled={busy}
              onChange={(event) => setUseOcr(event.target.checked)}
            />
            Use OCR for a scanned PDF
          </label>
          <small>OCR is slower and marks extracted source text as OCR-derived for review.</small>
          <button
            className="button button--primary"
            type="button"
            disabled={busy || readiness.status !== "ready"}
            onClick={() => fileInput.current?.click()}
          >
            {busy ? "Parsing document..." : "Choose protocol document"}
          </button>
          {readiness.mode === "fake" ? (
            <button
              className="button button--secondary"
              type="button"
              disabled={busy}
              onClick={() => void onImport(developmentProtocolFile(), false)}
            >
              Load development sample
            </button>
          ) : null}
        </div>
      </section>
    </main>
  );
}
