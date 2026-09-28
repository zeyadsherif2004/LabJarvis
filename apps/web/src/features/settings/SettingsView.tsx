import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { CheckCircle2, KeyRound, RotateCcw, Save, TriangleAlert } from "lucide-react";

import { ApiProblem, type ClientPort } from "../../api/client";
import { errorSentence } from "../../errors";
import {
  loadPushToTalkKey,
  PUSH_TO_TALK_KEYS,
  savePushToTalkKey,
  type PushToTalkKey,
} from "../../media/pushToTalk";
import type {
  ConfigurationProfile,
  ConfigurationProjection,
  ConfigurationState,
  ModelConfiguration,
  StageChoice,
  StageName,
  StageProviderChoice,
} from "../../types";
import "./settings.css";

interface SettingsViewProps {
  client: ClientPort;
  /** Called once LabJarvis is back after a restart, so readiness is re-read. */
  onRestarted: () => void;
  /** Injected in tests. */
  pollIntervalMs?: number;
  restartTimeoutMs?: number;
}

const STAGES: StageName[] = ["speech", "text", "vision"];
const STAGE_TITLES: Record<StageName, string> = {
  speech: "Speech",
  text: "Text",
  vision: "Vision",
};
const STAGE_PURPOSE: Record<StageName, string> = {
  speech: "Turns what you say into text.",
  text: "Understands commands and questions.",
  vision: "Reads reagent labels.",
};
const PROFILES: ConfigurationProfile[] = ["local", "hosted", "hybrid", "custom"];
const PROFILE_LABELS: Record<ConfigurationProfile | "development", string> = {
  local: "Local",
  hosted: "Hosted",
  hybrid: "Hybrid",
  custom: "Custom",
  development: "Development fixtures",
};
const FIELD_LABELS: Record<string, string> = {
  "speech.provider": "Speech provider",
  "speech.model": "Speech model",
  "speech.device": "Speech CPU/GPU",
  "text.provider": "Text provider",
  "text.model": "Text model",
  "text.reasoning_effort": "Text reasoning effort",
  "vision.provider": "Vision provider",
  "vision.model": "Vision model",
  "vision.device": "Vision CPU/GPU",
  allow_hosted_processing: "Allow hosted processing",
  hands_free: "Hands-free mode",
  high_integrity_transcription: "High-integrity transcription",
  high_integrity_model: "High-integrity model",
};

type Phase = "idle" | "confirm" | "restarting" | "restarted" | "timeout";

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function presetDraft(
  state: ConfigurationState,
  profile: ConfigurationProfile,
  base: ModelConfiguration,
): ModelConfiguration {
  const preset = state.presets[profile];
  if (profile === "custom" || preset.speech === undefined) return { ...base, profile };
  return {
    ...base,
    profile,
    speech: preset.speech,
    text: preset.text ?? base.text,
    vision: preset.vision ?? base.vision,
  };
}

/** The form starts from what was saved, else from what is running. */
function initialDraft(state: ConfigurationState): ModelConfiguration {
  if (state.saved !== null) return state.saved;
  const fallback: ModelConfiguration = {
    profile: "local",
    speech: state.presets.local.speech!,
    text: state.presets.local.text!,
    vision: state.presets.local.vision!,
    allow_hosted_processing: state.active.allow_hosted_processing,
    hands_free: state.active.hands_free,
    high_integrity_transcription: state.active.high_integrity_transcription,
    high_integrity_model: state.active.high_integrity_model,
    // Kokoro is the recommended voice; the browser voice stays the fallback.
    voice_output: "kokoro",
    voice: state.active.voice || "af_heart",
  };
  if (state.active.profile === "development") return fallback;
  const stage = (name: StageName): StageChoice => {
    const running = state.active[name];
    return running.provider === "fake"
      ? fallback[name]
      : { ...running, provider: running.provider };
  };
  return {
    ...fallback,
    profile: state.active.profile,
    speech: stage("speech"),
    text: stage("text"),
    vision: stage("vision"),
  };
}

function providerLabel(state: ConfigurationState, stage: StageName, provider: string): string {
  if (provider === "fake") return "development fixture";
  if (provider === "local") return "this computer";
  const option = state.options.providers[stage].find((item) => item.value === provider);
  if (provider === "openai") return "OpenAI";
  if (provider === "deepseek") return "DeepSeek";
  return option?.label.replace(/^(Hosted|Local server): /, "") ?? "an OpenAI-compatible provider";
}

function describeStage(
  state: ConfigurationState,
  stage: StageName,
  choice: ConfigurationProjection[StageName] | StageChoice,
): string {
  if (choice.provider === "fake") return "Deterministic development fixture";
  const where = providerLabel(state, stage, choice.provider);
  const device =
    choice.provider === "local" && stage !== "text" ? ` (${choice.device.toUpperCase()})` : "";
  return `${choice.model} on ${where}${device}`;
}

const VOICE_ACCENTS: Record<string, string> = {
  af: "American, female",
  am: "American, male",
  bf: "British, female",
  bm: "British, male",
};

/** "af_heart" -> "Heart (American, female)". */
export function voiceLabel(voice: string): string {
  const [prefix = "", name = voice] = voice.split("_");
  const title = name.charAt(0).toUpperCase() + name.slice(1);
  const accent = VOICE_ACCENTS[prefix];
  return accent === undefined ? voice : `${title} (${accent})`;
}

function describeVoice(output: "kokoro" | "browser", voice: string): string {
  return output === "kokoro" ? `Kokoro, ${voiceLabel(voice)}` : "Browser voice";
}

const VOICE_SAMPLE = "This is how LabJarvis will sound. Step 3 is complete.";

function sameConfiguration(a: ModelConfiguration | null, b: ModelConfiguration | null): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

function isHosted(provider: StageProviderChoice): boolean {
  return provider !== "local";
}

/** Providers whose requests take a reasoning-effort setting (DeepSeek's are its own). */
function takesReasoningEffort(provider: StageProviderChoice): boolean {
  return provider === "openai" || provider === "openai_compatible";
}

export function SettingsView({
  client,
  onRestarted,
  pollIntervalMs = 1000,
  restartTimeoutMs = 120_000,
}: SettingsViewProps) {
  const [state, setState] = useState<ConfigurationState | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [draft, setDraft] = useState<ModelConfiguration | null>(null);
  const [problems, setProblems] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [later, setLater] = useState(false);
  const [pedalKey, setPedalKey] = useState<PushToTalkKey>(loadPushToTalkKey);
  const [preview, setPreview] = useState<"idle" | "playing" | "failed">("idle");
  const mounted = useRef(true);
  /** The model last chosen for each stage and provider, so switching back restores it. */
  const remembered = useRef<Partial<Record<string, StageChoice>>>({});
  const headingId = useId();

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const load = useCallback(async () => {
    try {
      const loaded = await client.getConfiguration();
      if (!mounted.current) return;
      setState(loaded);
      setDraft(initialDraft(loaded));
      setLoadError(null);
    } catch (caught) {
      if (mounted.current) setLoadError(errorSentence(caught));
    }
  }, [client]);

  useEffect(() => {
    void load();
  }, [load]);

  const dirty = useMemo(
    () => state !== null && draft !== null && !sameConfiguration(draft, initialDraft(state)),
    [draft, state],
  );

  if (loadError !== null) {
    return (
      <main className="settings-view" id="main-content">
        <h1>Settings</h1>
        <p role="alert">{loadError}</p>
        <button className="button button--primary" type="button" onClick={() => void load()}>
          Retry
        </button>
      </main>
    );
  }
  if (state === null || draft === null) {
    return (
      <main className="settings-view loading-state" id="main-content" aria-busy="true">
        <p>Loading settings...</p>
      </main>
    );
  }
  const current = state;
  const custom = draft.profile === "custom";

  function update(next: Partial<ModelConfiguration>): void {
    setDraft((previous) => (previous === null ? previous : { ...previous, ...next }));
    setProblems([]);
    setNotice(null);
  }

  function updateStage(stage: StageName, next: Partial<StageChoice>): void {
    setDraft((previous) =>
      previous === null ? previous : { ...previous, [stage]: { ...previous[stage], ...next } },
    );
    setProblems([]);
    setNotice(null);
  }

  function changeProvider(stage: StageName, provider: StageProviderChoice): void {
    if (draft === null) return;
    const previous = draft[stage];
    remembered.current[`${stage}:${previous.provider}`] = previous;
    const saved = current.saved?.[stage];
    const restored =
      remembered.current[`${stage}:${provider}`] ??
      (saved?.provider === provider ? saved : undefined);
    const suggestions = current.options.model_suggestions[stage][provider] ?? [];
    updateStage(
      stage,
      restored ?? {
        provider,
        model: suggestions[0] ?? "",
        device: "cpu",
        // The evaluated OpenAI text configuration runs with reasoning "none".
        reasoning_effort: stage === "text" && provider === "openai" ? "none" : null,
      },
    );
  }

  async function save(): Promise<void> {
    if (draft === null) return;
    setSaving(true);
    setProblems([]);
    setError(null);
    setNotice(null);
    try {
      const saved = await client.saveConfiguration(draft);
      setState(saved);
      setDraft(initialDraft(saved));
      setLater(false);
      setPhase("idle");
      setNotice(
        saved.restart_required
          ? "Configuration saved. It will be used after LabJarvis restarts."
          : "Configuration saved.",
      );
    } catch (caught) {
      if (caught instanceof ApiProblem && caught.problem.problems !== undefined) {
        setProblems(caught.problem.problems);
      } else {
        setError(errorSentence(caught));
      }
    } finally {
      setSaving(false);
    }
  }

  async function restart(acknowledge: boolean): Promise<void> {
    setError(null);
    if (!acknowledge && current.active_sessions > 0) {
      setPhase("confirm");
      return;
    }
    const previous = current.instance_id;
    setPhase("restarting");
    try {
      await client.restartLabJarvis(acknowledge);
    } catch (caught) {
      if (
        caught instanceof ApiProblem &&
        caught.problem.code === "RESTART_CONFIRMATION_REQUIRED"
      ) {
        setPhase("confirm");
        return;
      }
      setPhase("idle");
      setError(errorSentence(caught));
      return;
    }
    const deadline = Date.now() + restartTimeoutMs;
    while (Date.now() < deadline) {
      await sleep(pollIntervalMs);
      if (!mounted.current) return;
      try {
        const next = await client.getConfiguration();
        if (next.instance_id !== previous) {
          if (!mounted.current) return;
          setState(next);
          setDraft(initialDraft(next));
          setNotice(null);
          setPhase("restarted");
          setLater(false);
          onRestarted();
          return;
        }
      } catch {
        // Expected while LabJarvis is stopped; keep waiting.
      }
    }
    if (mounted.current) setPhase("timeout");
  }

  async function playSample(): Promise<void> {
    if (draft === null) return;
    setPreview("playing");
    try {
      const wav = await client.synthesizeSpeech(VOICE_SAMPLE, { voice: draft.voice });
      const url = URL.createObjectURL(new Blob([wav], { type: "audio/wav" }));
      const audio = new Audio(url);
      audio.onended = () => {
        URL.revokeObjectURL(url);
        setPreview("idle");
      };
      await audio.play();
    } catch {
      setPreview("failed");
    }
  }

  const overrides = current.overrides;
  const hostedChosen = STAGES.some((stage) => isHosted(draft[stage].provider));

  return (
    <main className="settings-view" id="main-content" aria-labelledby={headingId}>
      <header className="view-heading">
        <div>
          <h1 id={headingId}>Settings</h1>
          <p>
            Choose how LabJarvis runs its speech, text and vision models. Model changes are saved
            now and used the next time LabJarvis starts; the models running now keep working
            until then.
          </p>
        </div>
      </header>

      <StatusPanel
        state={current}
        phase={phase}
        later={later}
        onRestart={() => void restart(false)}
        onConfirm={() => void restart(true)}
        onCancel={() => setPhase("idle")}
        onLater={() => setLater(true)}
      />

      {notice !== null ? (
        <p className="settings-notice" role="status">
          {notice}
        </p>
      ) : null}
      {error !== null ? (
        <p className="settings-error" role="alert">
          {error}
        </p>
      ) : null}

      <section className="settings-card" aria-labelledby="settings-profile-title">
        <h2 id="settings-profile-title">Profile</h2>
        <div className="settings-profiles" role="radiogroup" aria-labelledby="settings-profile-title">
          {PROFILES.map((profile) => (
            <label
              key={profile}
              className={`settings-profile${draft.profile === profile ? " settings-profile--selected" : ""}`}
            >
              <input
                type="radio"
                name="settings-profile"
                value={profile}
                checked={draft.profile === profile}
                onChange={() => {
                  setDraft(presetDraft(current, profile, draft));
                  setProblems([]);
                  setNotice(null);
                }}
              />
              <strong>{current.presets[profile].label}</strong>
              <span>{current.presets[profile].description}</span>
            </label>
          ))}
        </div>
      </section>

      <section className="settings-card" aria-labelledby="settings-models-title">
        <h2 id="settings-models-title">Models</h2>
        {!custom ? (
          <p className="settings-hint">
            The {PROFILE_LABELS[draft.profile]} profile uses fixed models. Choose Custom to change
            an individual stage.
          </p>
        ) : null}
        <div className="settings-stages">
          {STAGES.map((stage) => (
            <StageEditor
              key={stage}
              stage={stage}
              state={current}
              choice={draft[stage]}
              editable={custom}
              onProvider={(provider) => changeProvider(stage, provider)}
              onChange={(next) => updateStage(stage, next)}
            />
          ))}
        </div>
      </section>

      <section className="settings-card" aria-labelledby="settings-interaction-title">
        <h2 id="settings-interaction-title">Interaction</h2>
        <label className="settings-check">
          <input
            type="checkbox"
            checked={draft.hands_free}
            onChange={(event) => update({ hands_free: event.target.checked })}
          />
          <span>
            <strong>Hands-free mode</strong>
            Wake-phrase listening, voice-only confirmation and timer alarms. Vision on this
            computer offers live scanning (the default) or a timed single-frame capture; hosted
            Vision sends one cropped image after a spoken countdown when you ask to check a reagent.
          </span>
        </label>
        <label className="settings-check">
          <input
            type="checkbox"
            checked={draft.hands_free || draft.high_integrity_transcription}
            disabled={draft.hands_free}
            onChange={(event) => update({ high_integrity_transcription: event.target.checked })}
          />
          <span>
            <strong>High-integrity transcription</strong>
            {draft.hands_free
              ? "Included in hands-free mode: every consequential utterance is checked by a second transcription."
              : "Check utterances that could change the record with a second transcription."}
          </span>
        </label>
        {draft.hands_free || draft.high_integrity_transcription ? (
          <label className="settings-field">
            <span>Second transcription model</span>
            <input
              type="text"
              value={draft.high_integrity_model}
              placeholder={draft.hands_free ? "base.en (default)" : "for example base.en"}
              maxLength={200}
              onChange={(event) => update({ high_integrity_model: event.target.value.trim() })}
            />
          </label>
        ) : null}
      </section>

      <section className="settings-card" aria-labelledby="settings-voice-title">
        <h2 id="settings-voice-title">Voice output</h2>
        <div role="radiogroup" aria-labelledby="settings-voice-title">
          <label className="settings-check">
            <input
              type="radio"
              name="settings-voice-output"
              checked={draft.voice_output === "kokoro"}
              onChange={() => update({ voice_output: "kokoro" })}
            />
            <span>
              <strong>Kokoro natural voice (recommended)</strong>
              A neural voice that runs on this computer. Nothing is sent anywhere.
            </span>
          </label>
          <label className="settings-check">
            <input
              type="radio"
              name="settings-voice-output"
              checked={draft.voice_output === "browser"}
              onChange={() => update({ voice_output: "browser" })}
            />
            <span>
              <strong>Browser voice</strong>
              Your system's built-in speech. Always available; also used automatically whenever
              Kokoro cannot speak.
            </span>
          </label>
        </div>
        {draft.voice_output === "kokoro" ? (
          current.voice_output.kokoro_installed ? (
            <div className="settings-actions">
              <label className="settings-field">
                <span>Voice</span>
                <select
                  value={draft.voice}
                  onChange={(event) => update({ voice: event.target.value })}
                >
                  {current.voice_output.voices.map((voice) => (
                    <option key={voice} value={voice}>
                      {voiceLabel(voice)}
                    </option>
                  ))}
                </select>
              </label>
              <button
                className="button button--secondary"
                type="button"
                disabled={preview === "playing"}
                onClick={() => void playSample()}
              >
                {preview === "playing" ? "Playing..." : "Play sample"}
              </button>
              {preview === "failed" ? (
                <span className="settings-hint" role="status">
                  The sample could not be played.
                </span>
              ) : null}
            </div>
          ) : (
            <p className="settings-hint" role="note">
              {current.voice_output.kokoro_detail} Until then the browser voice is used.
            </p>
          )
        ) : null}
      </section>

      <section className="settings-card" aria-labelledby="settings-privacy-title">
        <h2 id="settings-privacy-title">Privacy</h2>
        <label className="settings-check">
          <input
            type="checkbox"
            checked={draft.allow_hosted_processing}
            onChange={(event) => update({ allow_hosted_processing: event.target.checked })}
          />
          <span>
            <strong>Allow hosted processing</strong>
            Required before any stage may run on a hosted provider. Hosted stages send their input
            (audio, what you say, or label photographs) off this computer. LabJarvis never
            switches to a hosted provider by itself.
          </span>
        </label>
        {hostedChosen && !draft.allow_hosted_processing ? (
          <p className="settings-hint" role="note">
            A stage is set to a hosted provider, so saving will be refused until hosted processing
            is allowed.
          </p>
        ) : null}
      </section>

      <section className="settings-card" aria-labelledby="settings-credentials-title">
        <h2 id="settings-credentials-title">Credentials</h2>
        <ul className="settings-credentials">
          {current.credentials.map((item) => (
            <li key={item.id}>
              <KeyRound aria-hidden="true" />
              {item.label}: <strong>{item.configured ? "Configured" : "Not configured"}</strong>
              {item.source === ".env" ? <span className="settings-hint"> (from .env)</span> : null}
              {item.source === "environment" ? (
                <span className="settings-hint"> (from an environment variable, overrides .env)</span>
              ) : null}
            </li>
          ))}
        </ul>
        <p className="settings-hint">
          Each hosted provider has its own key, set in the LabJarvis environment (the{" "}
          <code>.env</code> file or the shell that starts it): <code>LABJARVIS_OPENAI_API_KEY</code>{" "}
          (or <code>LABJARVIS_HOSTED_API_KEY</code>) for OpenAI and{" "}
          <code>LABJARVIS_DEEPSEEK_API_KEY</code> for DeepSeek. Keys are never entered in or shown
          by the browser.
        </p>
      </section>

      <section className="settings-card" aria-labelledby="settings-browser-title">
        <h2 id="settings-browser-title">This browser</h2>
        <label className="settings-field">
          <span>Push-to-talk key or foot pedal</span>
          <select
            value={pedalKey}
            onChange={(event) => {
              const key = event.target.value as PushToTalkKey;
              setPedalKey(key);
              savePushToTalkKey(key);
            }}
          >
            {PUSH_TO_TALK_KEYS.map((key) => (
              <option key={key} value={key}>
                {key}
              </option>
            ))}
          </select>
        </label>
        <p className="settings-hint">
          Applies immediately in this browser; no save or restart needed. Set your USB pedal to
          send the same key.
        </p>
      </section>

      {problems.length > 0 ? (
        <div className="settings-problems" role="alert">
          <strong>This configuration was not saved:</strong>
          <ul>
            {problems.map((problem) => (
              <li key={problem}>{problem}</li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="settings-actions">
        <button
          className="button button--primary"
          type="button"
          disabled={saving || phase === "restarting"}
          onClick={() => void save()}
        >
          <Save aria-hidden="true" /> {saving ? "Saving..." : "Save configuration"}
        </button>
        {dirty ? <span className="settings-hint">Unsaved changes</span> : null}
      </div>

      {current.warnings.length > 0 ? (
        <section className="settings-card settings-card--warning" aria-labelledby="settings-warnings-title">
          <h2 id="settings-warnings-title">Configuration warnings</h2>
          <ul>
            {current.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </section>
      ) : null}

      {overrides.length > 0 || current.saved_ignored ? (
        <section className="settings-card settings-card--warning" aria-labelledby="settings-env-title">
          <h2 id="settings-env-title">Set by the environment</h2>
          {current.saved_ignored ? (
            <p>
              This launch ignores the saved configuration (demo or development launch), so the
              environment decides every choice.
            </p>
          ) : (
            <>
              <p>
                These saved choices will not take effect, because the environment LabJarvis was
                started from sets them. Remove the variable to use the saved value.
              </p>
              <ul>
                {overrides.map((item) => (
                  <li key={item.field}>
                    {FIELD_LABELS[item.field] ?? item.field}: saved {String(item.saved)}, used{" "}
                    {String(item.effective)}
                    {item.variables.length > 0 ? ` (${item.variables.join(", ")})` : null}
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>
      ) : null}

      <details className="settings-help">
        <summary>How configuration works</summary>
        <p>
          Saved choices are stored in <code>{current.settings_file}</code> and read when LabJarvis
          starts. Environment variables set in the shell that starts LabJarvis take precedence
          over saved choices, which take precedence over the <code>.env</code> file and the
          built-in defaults.
        </p>
        <p>
          Models are never swapped while LabJarvis runs. Restarting reloads the saved
          configuration; protocols, sessions and the notebook are stored in the database and are
          not affected.
        </p>
      </details>
    </main>
  );
}

interface StatusPanelProps {
  state: ConfigurationState;
  phase: Phase;
  later: boolean;
  onRestart: () => void;
  onConfirm: () => void;
  onCancel: () => void;
  onLater: () => void;
}

function StatusPanel({ state, phase, later, onRestart, onConfirm, onCancel, onLater }: StatusPanelProps) {
  const confirmRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (phase === "confirm") confirmRef.current?.focus();
  }, [phase]);

  const summary = (
    <dl className="settings-compare">
      <div>
        <dt>Running now</dt>
        <dd>
          <strong>{PROFILE_LABELS[state.active.profile]}</strong>
          <ul>
            {STAGES.map((stage) => (
              <li key={stage}>
                {STAGE_TITLES[stage]}: {describeStage(state, stage, state.active[stage])}
              </li>
            ))}
            <li>Voice: {describeVoice(state.active.voice_output, state.active.voice)}</li>
          </ul>
        </dd>
      </div>
      {state.saved !== null && (state.restart_required || state.next_start_problem !== null) ? (
        <div>
          <dt>Saved for the next start</dt>
          <dd>
            <strong>{PROFILE_LABELS[state.saved.profile]}</strong>
            <ul>
              {STAGES.map((stage) => (
                <li key={stage}>
                  {STAGE_TITLES[stage]}:{" "}
                  {describeStage(state, stage, state.next_start?.[stage] ?? state.saved![stage])}
                </li>
              ))}
              <li>
                Voice:{" "}
                {describeVoice(
                  (state.next_start ?? state.saved).voice_output,
                  (state.next_start ?? state.saved).voice,
                )}
              </li>
            </ul>
          </dd>
        </div>
      ) : null}
    </dl>
  );

  if (phase === "restarting") {
    return (
      <section className="settings-status settings-status--busy" role="status" aria-live="polite">
        <RotateCcw aria-hidden="true" />
        <div>
          <h2>Restarting LabJarvis...</h2>
          <p>The interface will reconnect by itself when LabJarvis is back.</p>
        </div>
      </section>
    );
  }
  if (phase === "timeout") {
    return (
      <section className="settings-status settings-status--warning" role="alert">
        <TriangleAlert aria-hidden="true" />
        <div>
          <h2>LabJarvis has not come back</h2>
          <p>
            It was asked to restart but is not answering yet. Check the window where LabJarvis
            was started for the reason, then reload this page.
          </p>
        </div>
      </section>
    );
  }
  if (state.next_start_problem !== null) {
    return (
      <section className="settings-status settings-status--warning" role="alert">
        <TriangleAlert aria-hidden="true" />
        <div>
          <h2>The saved configuration cannot be used</h2>
          <p>{state.next_start_problem}</p>
          <p>Change the configuration below and save again.</p>
          {summary}
        </div>
      </section>
    );
  }
  if (!state.restart_required) {
    return (
      <section className="settings-status settings-status--ready" role="status">
        <CheckCircle2 aria-hidden="true" />
        <div>
          <h2>Configuration active</h2>
          <p>
            {phase === "restarted"
              ? "LabJarvis restarted and is using the saved configuration."
              : state.saved === null
                ? "Nothing has been saved here yet; LabJarvis is using its environment configuration."
                : "The saved configuration is the one running now."}
          </p>
          {summary}
        </div>
      </section>
    );
  }
  return (
    <section className="settings-status settings-status--warning" aria-labelledby="settings-restart-title">
      <TriangleAlert aria-hidden="true" />
      <div>
        <h2 id="settings-restart-title">Configuration saved — restart required</h2>
        <p>The models running now keep working until LabJarvis restarts.</p>
        {summary}
        {phase === "confirm" ? (
          <div className="settings-confirm" role="alertdialog" aria-labelledby="settings-confirm-text">
            <p id="settings-confirm-text">
              A laboratory session is currently active. Restarting LabJarvis will temporarily
              interrupt the interface. All already-persisted protocol, session and notebook state
              will be preserved; anything typed but not yet submitted on screen will be lost.
            </p>
            <div className="settings-actions">
              <button className="button button--secondary" type="button" onClick={onCancel}>
                Cancel
              </button>
              <button ref={confirmRef} className="button button--danger" type="button" onClick={onConfirm}>
                Restart anyway
              </button>
            </div>
          </div>
        ) : !state.restart.available ? (
          <p className="settings-manual" role="note">
            {state.restart.reason ??
              "Restart LabJarvis manually to apply the saved configuration."}
          </p>
        ) : later ? (
          <p className="settings-hint">
            LabJarvis will use the saved configuration the next time it starts.{" "}
            <button className="button-link" type="button" onClick={onRestart}>
              Restart now
            </button>
          </p>
        ) : (
          <div className="settings-actions">
            <button className="button button--primary" type="button" onClick={onRestart}>
              <RotateCcw aria-hidden="true" /> Restart LabJarvis
            </button>
            <button className="button button--secondary" type="button" onClick={onLater}>
              Restart later
            </button>
          </div>
        )}
      </div>
    </section>
  );
}

interface StageEditorProps {
  stage: StageName;
  state: ConfigurationState;
  choice: StageChoice;
  editable: boolean;
  onProvider: (provider: StageProviderChoice) => void;
  onChange: (next: Partial<StageChoice>) => void;
}

function StageEditor({ stage, state, choice, editable, onProvider, onChange }: StageEditorProps) {
  const listId = useId();
  const title = STAGE_TITLES[stage];
  if (!editable) {
    return (
      <div className="settings-stage">
        <h3>{title}</h3>
        <p className="settings-hint">{STAGE_PURPOSE[stage]}</p>
        <p>{describeStage(state, stage, choice)}</p>
      </div>
    );
  }
  const suggestions = state.options.model_suggestions[stage][choice.provider] ?? [];
  const hosted = isHosted(choice.provider);
  return (
    <fieldset className="settings-stage">
      <legend>{title}</legend>
      <p className="settings-hint">{STAGE_PURPOSE[stage]}</p>
      <label className="settings-field">
        <span>Runs on</span>
        <select
          value={choice.provider}
          onChange={(event) => onProvider(event.target.value as StageProviderChoice)}
        >
          {state.options.providers[stage].map((option) => (
            <option key={option.value} value={option.value}>
              {option.configured ? option.label : `${option.label} (API key not configured)`}
            </option>
          ))}
        </select>
      </label>
      <label className="settings-field">
        <span>Model</span>
        <input
          type="text"
          list={listId}
          value={choice.model}
          maxLength={200}
          onChange={(event) => onChange({ model: event.target.value.trim() })}
        />
        <datalist id={listId}>
          {suggestions.map((model) => (
            <option key={model} value={model} />
          ))}
        </datalist>
      </label>
      {!hosted && stage !== "text" ? (
        <div className="settings-field" role="radiogroup" aria-label={`${title} hardware`}>
          <span>Hardware</span>
          <div className="settings-inline">
            {(["cpu", "gpu"] as const).map((device) => (
              <label key={device}>
                <input
                  type="radio"
                  name={`${stage}-device`}
                  checked={choice.device === device}
                  onChange={() => onChange({ device })}
                />{" "}
                {device.toUpperCase()}
              </label>
            ))}
          </div>
        </div>
      ) : null}
      {stage === "text" && takesReasoningEffort(choice.provider) ? (
        <label className="settings-field">
          <span>Reasoning effort</span>
          <select
            value={choice.reasoning_effort ?? ""}
            onChange={(event) => onChange({ reasoning_effort: event.target.value || null })}
          >
            <option value="">Provider default</option>
            {state.options.reasoning_efforts.map((effort) => (
              <option key={effort} value={effort}>
                {effort}
              </option>
            ))}
          </select>
        </label>
      ) : null}
    </fieldset>
  );
}
