import { FileCheck2, Play, ShieldCheck } from "lucide-react";

import type { ActionResult } from "../../actions";
import type { ProtocolVersion, SessionResponse } from "../../types";


interface ProtocolApprovedViewProps {
  protocol: ProtocolVersion;
  session: SessionResponse;
  busy: boolean;
  onStart: () => Promise<ActionResult>;
}

export function ProtocolApprovedView({
  protocol,
  session,
  busy,
  onStart,
}: ProtocolApprovedViewProps) {
  return (
    <main className="approved-view" id="main-content">
      <FileCheck2 aria-hidden="true" className="approved-mark" />
      <p className="eyebrow">Reviewed protocol version {protocol.version_number}</p>
      <h1>Protocol approved and ready</h1>
      <p>
        {protocol.steps.length} reviewed {protocol.steps.length === 1 ? "step" : "steps"} are
        frozen for this session. Starting moves the first step into progress.
      </p>
      <div className="approved-boundary">
        <ShieldCheck aria-hidden="true" />
        <span>Commands remain proposals until you confirm consequential changes.</span>
      </div>
      <button
        className="button button--primary"
        type="button"
        disabled={busy || session.state !== "READY"}
        onClick={() => void onStart()}
      >
        <Play aria-hidden="true" /> {busy ? "Starting..." : "Start sterile session"}
      </button>
    </main>
  );
}
