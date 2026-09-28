import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ApiProblem } from "../../api/client";
import { parseRoute, routeToHash } from "../../routing";
import { fakeClient } from "../../test/fixtures";
import type { ConfigurationState, ModelConfiguration, StageChoice } from "../../types";
import { SettingsView } from "./SettingsView";

const local = (model: string): StageChoice => ({
  provider: "local",
  model,
  device: "cpu",
  reasoning_effort: null,
});
const openai = (model: string, effort: string | null = null): StageChoice => ({
  provider: "openai",
  model,
  device: "cpu",
  reasoning_effort: effort,
});
const LOCAL = { speech: local("small.en"), text: local("qwen2.5:3b"), vision: local("english_g2") };
const HOSTED = {
  speech: openai("gpt-4o-mini-transcribe"),
  text: openai("gpt-5.4-nano", "none"),
  vision: openai("gpt-5.4-nano"),
};

const localSaved: ModelConfiguration = {
  profile: "local",
  ...LOCAL,
  allow_hosted_processing: false,
  hands_free: false,
  high_integrity_transcription: false,
  high_integrity_model: "",
  voice_output: "kokoro",
  voice: "af_heart",
};

function configurationState(overrides: Partial<ConfigurationState> = {}): ConfigurationState {
  return {
    instance_id: "instance-1",
    active: {
      profile: "local",
      ...LOCAL,
      allow_hosted_processing: false,
      hands_free: false,
      high_integrity_transcription: false,
      high_integrity_model: "",
      voice_output: "kokoro",
      voice: "af_heart",
    },
    saved: localSaved,
    saved_ignored: false,
    next_start: {
      ...LOCAL,
      allow_hosted_processing: false,
      hands_free: false,
      high_integrity_transcription: false,
      high_integrity_model: "",
      voice_output: "kokoro",
      voice: "af_heart",
    },
    next_start_problem: null,
    restart_required: false,
    overrides: [],
    restart: { available: true, reason: null },
    active_sessions: 0,
    credentials: [
      { id: "openai", label: "OpenAI API key", configured: false, source: null, variable: null },
    ],
    warnings: [],
    options: {
      providers: {
        speech: [
          { value: "local", label: "On this computer", locality: "local", configured: true },
          { value: "openai", label: "Hosted: OpenAI", locality: "hosted", configured: true },
        ],
        text: [
          { value: "local", label: "On this computer", locality: "local", configured: true },
          { value: "openai", label: "Hosted: OpenAI", locality: "hosted", configured: true },
          { value: "deepseek", label: "Hosted: DeepSeek", locality: "hosted", configured: false },
        ],
        vision: [
          { value: "local", label: "On this computer", locality: "local", configured: true },
          { value: "openai", label: "Hosted: OpenAI", locality: "hosted", configured: true },
        ],
      },
      model_suggestions: {
        speech: { local: ["small.en", "base.en"], openai: ["gpt-4o-mini-transcribe"] },
        text: { local: ["qwen2.5:3b"], openai: ["gpt-5.4-nano"], deepseek: ["deepseek-flash"] },
        vision: { local: ["english_g2"], openai: ["gpt-5.4-nano"] },
      },
      reasoning_efforts: ["none", "low", "medium", "high"],
    },
    presets: {
      local: { label: "Local", description: "Everything runs on this computer.", ...LOCAL },
      hosted: { label: "Hosted", description: "Processed by OpenAI.", ...HOSTED },
      hybrid: {
        label: "Hybrid",
        description: "Text processed by OpenAI.",
        speech: LOCAL.speech,
        text: HOSTED.text,
        vision: LOCAL.vision,
      },
      custom: { label: "Custom", description: "Choose each stage." },
    },
    settings_file: ".labjarvis/settings.json",
    voice_output: {
      kokoro_installed: true,
      kokoro_detail: "Kokoro speaks responses on this computer.",
      voices: ["af_heart", "am_michael", "bf_emma"],
    },
    ...overrides,
  };
}

const restartRequired = (overrides: Partial<ConfigurationState> = {}) =>
  configurationState({
    active: { ...configurationState().active, profile: "development" },
    restart_required: true,
    ...overrides,
  });

function problem(code: string, detail: string, problems?: string[]): ApiProblem {
  return new ApiProblem({
    type: "about:blank",
    title: code,
    status: code === "CONFIGURATION_INVALID" ? 422 : 409,
    detail,
    code,
    correlation_id: "c-1",
    ...(problems === undefined ? {} : { problems }),
  });
}

describe("SettingsView", () => {
  it("shows the active configuration and credential status without any key", async () => {
    const client = fakeClient({ getConfiguration: () => Promise.resolve(configurationState()) });

    render(<SettingsView client={client} onRestarted={vi.fn()} />);

    expect(await screen.findByRole("heading", { name: "Configuration active" })).toBeInTheDocument();
    expect(screen.getByText(/OpenAI API key/)).toHaveTextContent("Not configured");
    expect(screen.queryByRole("button", { name: /Restart LabJarvis/ })).toBeNull();
    for (const name of ["Local", "Hosted", "Hybrid", "Custom"]) {
      expect(screen.getByRole("radio", { name: new RegExp(`^${name}`) })).toBeInTheDocument();
    }
  });

  it("saves a preset and explains every problem when it is refused", async () => {
    const user = userEvent.setup();
    const saveConfiguration = vi
      .fn()
      .mockRejectedValue(
        problem("CONFIGURATION_INVALID", "refused", [
          "Hosted processing is not allowed, but Speech (OpenAI) would run on a hosted provider.",
          "Speech (OpenAI) needs an API key, but none is configured.",
        ]),
      );
    const client = fakeClient({
      getConfiguration: () => Promise.resolve(configurationState()),
      saveConfiguration,
    });
    render(<SettingsView client={client} onRestarted={vi.fn()} />);

    await user.click(await screen.findByRole("radio", { name: /^Hosted/ }));
    await user.click(screen.getByRole("button", { name: /Save configuration/ }));

    expect(saveConfiguration).toHaveBeenCalledWith(
      expect.objectContaining({ profile: "hosted", ...HOSTED, allow_hosted_processing: false }),
    );
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("This configuration was not saved");
    expect(within(alert).getAllByRole("listitem")).toHaveLength(2);
    expect(screen.queryByText(/Configuration saved/)).toBeNull();
  });

  it("edits individual stages in Custom mode and then offers a restart", async () => {
    const user = userEvent.setup();
    const saveConfiguration = vi.fn().mockResolvedValue(
      restartRequired({
        saved: { ...localSaved, profile: "custom", speech: { ...LOCAL.speech, model: "base.en", device: "gpu" } },
      }),
    );
    const client = fakeClient({
      getConfiguration: () => Promise.resolve(configurationState()),
      saveConfiguration,
    });
    render(<SettingsView client={client} onRestarted={vi.fn()} />);

    await user.click(await screen.findByRole("radio", { name: /^Custom/ }));
    const speech = screen.getByRole("group", { name: "Speech" });
    const model = within(speech).getByRole("combobox", { name: "Model" });
    await user.clear(model);
    await user.type(model, "base.en");
    await user.click(within(speech).getByRole("radio", { name: "GPU" }));
    expect(within(screen.getByRole("group", { name: "Text" })).queryByRole("radio", { name: "GPU" })).toBeNull();
    await user.click(screen.getByRole("button", { name: /Save configuration/ }));

    expect(saveConfiguration).toHaveBeenCalledWith(
      expect.objectContaining({
        profile: "custom",
        speech: { provider: "local", model: "base.en", device: "gpu", reasoning_effort: null },
      }),
    );
    expect(
      await screen.findByRole("heading", { name: "Configuration saved — restart required" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Restart LabJarvis/ })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Restart later" }));
    expect(screen.getByText(/next time it starts/)).toBeInTheDocument();
  });

  it("switches text between Local, OpenAI and DeepSeek, each keeping its own model", async () => {
    const user = userEvent.setup();
    const saveConfiguration = vi.fn().mockResolvedValue(restartRequired());
    const client = fakeClient({
      getConfiguration: () => Promise.resolve(configurationState()),
      saveConfiguration,
    });
    render(<SettingsView client={client} onRestarted={vi.fn()} />);

    await user.click(await screen.findByRole("radio", { name: /^Custom/ }));
    const text = screen.getByRole("group", { name: "Text" });
    const runsOn = within(text).getByRole("combobox", { name: "Runs on" });
    expect(
      within(runsOn).getByRole("option", { name: "Hosted: DeepSeek (API key not configured)" }),
    ).toBeInTheDocument();

    await user.selectOptions(runsOn, "openai");
    const model = () => within(text).getByRole("combobox", { name: "Model" });
    expect(model()).toHaveValue("gpt-5.4-nano");
    await user.clear(model());
    await user.type(model(), "gpt-4.1-nano");
    expect(within(text).getByRole("combobox", { name: "Reasoning effort" })).toBeInTheDocument();

    await user.selectOptions(runsOn, "deepseek");
    expect(model()).toHaveValue("deepseek-flash");
    expect(within(text).queryByRole("combobox", { name: "Reasoning effort" })).toBeNull();

    await user.selectOptions(runsOn, "openai");
    expect(model()).toHaveValue("gpt-4.1-nano");
    await user.selectOptions(runsOn, "local");
    expect(model()).toHaveValue("qwen2.5:3b");

    await user.selectOptions(runsOn, "deepseek");
    await user.click(screen.getByRole("button", { name: /Save configuration/ }));
    expect(saveConfiguration).toHaveBeenCalledWith(
      expect.objectContaining({
        text: { provider: "deepseek", model: "deepseek-flash", device: "cpu", reasoning_effort: null },
      }),
    );
    expect(JSON.stringify(saveConfiguration.mock.calls)).not.toMatch(/key|url/i);
  });

  it("says plainly when LabJarvis cannot restart itself", async () => {
    const reason =
      "LabJarvis was started without restart supervision, so restart it manually to apply the saved configuration.";
    const client = fakeClient({
      getConfiguration: () =>
        Promise.resolve(restartRequired({ restart: { available: false, reason } })),
    });

    render(<SettingsView client={client} onRestarted={vi.fn()} />);

    expect(await screen.findByText(reason)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Restart LabJarvis/ })).toBeNull();
  });

  it("asks for confirmation during an active session, then waits for the new instance", async () => {
    const user = userEvent.setup();
    const onRestarted = vi.fn();
    const restartLabJarvis = vi.fn().mockResolvedValue({ restarting: true, instance_id: "instance-1" });
    const getConfiguration = vi
      .fn()
      .mockResolvedValueOnce(restartRequired({ active_sessions: 1 }))
      .mockRejectedValueOnce(new Error("offline while restarting"))
      .mockResolvedValue(configurationState({ instance_id: "instance-2" }));
    const client = fakeClient({ getConfiguration, restartLabJarvis });
    render(
      <SettingsView client={client} onRestarted={onRestarted} pollIntervalMs={1} />,
    );

    await user.click(await screen.findByRole("button", { name: /Restart LabJarvis/ }));
    const dialog = screen.getByRole("alertdialog");
    expect(dialog).toHaveTextContent("A laboratory session is currently active");
    expect(dialog).toHaveTextContent("will be preserved");
    expect(restartLabJarvis).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(restartLabJarvis).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: /Restart LabJarvis/ }));
    await user.click(screen.getByRole("button", { name: "Restart anyway" }));

    expect(restartLabJarvis).toHaveBeenCalledWith(true);
    expect(await screen.findByText(/restarted and is using the saved configuration/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Configuration active" })).toBeInTheDocument();
    await waitFor(() => expect(onRestarted).toHaveBeenCalledTimes(1));
  });

  it("falls back to the confirmation when the server reports an active session", async () => {
    const user = userEvent.setup();
    const restartLabJarvis = vi
      .fn()
      .mockRejectedValue(problem("RESTART_CONFIRMATION_REQUIRED", "A laboratory session is currently active."));
    const client = fakeClient({
      getConfiguration: () => Promise.resolve(restartRequired()),
      restartLabJarvis,
    });
    render(<SettingsView client={client} onRestarted={vi.fn()} />);

    await user.click(await screen.findByRole("button", { name: /Restart LabJarvis/ }));

    expect(await screen.findByRole("alertdialog")).toBeInTheDocument();
    expect(restartLabJarvis).toHaveBeenCalledWith(false);
  });

  it("does not present an environment-overridden choice as active", async () => {
    const client = fakeClient({
      getConfiguration: () =>
        Promise.resolve(
          configurationState({
            overrides: [
              {
                field: "text.model",
                saved: "qwen2.5:3b",
                effective: "qwen2.5:1.5b",
                variables: ["LABJARVIS_TEXT_MODEL_NAME"],
              },
            ],
          }),
        ),
    });

    render(<SettingsView client={client} onRestarted={vi.fn()} />);

    const section = await screen.findByRole("region", { name: "Set by the environment" });
    expect(section).toHaveTextContent(
      "Text model: saved qwen2.5:3b, used qwen2.5:1.5b (LABJARVIS_TEXT_MODEL_NAME)",
    );
  });

  it("shows where each key comes from and warns when the environment shadows .env", async () => {
    const warning =
      "The OpenAI API key comes from the LABJARVIS_OPENAI_API_KEY environment variable of the process that started LabJarvis, which takes precedence over .env.";
    const client = fakeClient({
      getConfiguration: () =>
        Promise.resolve(
          configurationState({
            credentials: [
              { id: "openai", label: "OpenAI API key", configured: true, source: "environment", variable: "LABJARVIS_OPENAI_API_KEY" },
              { id: "deepseek", label: "DeepSeek API key", configured: true, source: ".env", variable: "LABJARVIS_DEEPSEEK_API_KEY" },
            ],
            warnings: [warning],
          }),
        ),
    });

    render(<SettingsView client={client} onRestarted={vi.fn()} />);

    const credentials = within(
      await screen.findByRole("region", { name: "Credentials" }),
    ).getAllByRole("listitem");
    expect(credentials[0]).toHaveTextContent("OpenAI API key: Configured (from an environment variable, overrides .env)");
    expect(credentials[1]).toHaveTextContent("DeepSeek API key: Configured (from .env)");
    const section = screen.getByRole("region", { name: "Configuration warnings" });
    expect(section).toHaveTextContent(warning);
  });

  it("offers Kokoro as the recommended voice with a voice picker and a sample", async () => {
    const user = userEvent.setup();
    const synthesizeSpeech = vi.fn().mockRejectedValue(new Error("no audio in tests"));
    const saveConfiguration = vi.fn().mockResolvedValue(restartRequired());
    const client = fakeClient({
      getConfiguration: () => Promise.resolve(configurationState()),
      saveConfiguration,
      synthesizeSpeech,
    });
    render(<SettingsView client={client} onRestarted={vi.fn()} />);

    const section = await screen.findByRole("region", { name: "Voice output" });
    expect(within(section).getByRole("radio", { name: /Kokoro natural voice \(recommended\)/ })).toBeChecked();
    const voice = within(section).getByRole("combobox", { name: "Voice" });
    expect(within(voice).getByRole("option", { name: "Heart (American, female)" })).toBeInTheDocument();
    await user.selectOptions(voice, "bf_emma");
    await user.click(within(section).getByRole("button", { name: "Play sample" }));
    expect(synthesizeSpeech).toHaveBeenCalledWith(expect.any(String), { voice: "bf_emma" });
    expect(await within(section).findByText("The sample could not be played.")).toBeInTheDocument();

    await user.click(within(section).getByRole("radio", { name: /Browser voice/ }));
    expect(within(section).queryByRole("combobox", { name: "Voice" })).toBeNull();
    await user.click(screen.getByRole("button", { name: /Save configuration/ }));
    expect(saveConfiguration).toHaveBeenCalledWith(
      expect.objectContaining({ voice_output: "browser", voice: "bf_emma" }),
    );
  });

  it("says how to install Kokoro and that the browser voice is used until then", async () => {
    const detail =
      "Kokoro model files are missing; the browser voice is used. Run `python scripts/setup_models.py kokoro` to download them.";
    const client = fakeClient({
      getConfiguration: () =>
        Promise.resolve(
          configurationState({
            voice_output: { kokoro_installed: false, kokoro_detail: detail, voices: [] },
          }),
        ),
    });
    render(<SettingsView client={client} onRestarted={vi.fn()} />);

    const section = await screen.findByRole("region", { name: "Voice output" });
    expect(section).toHaveTextContent(detail);
    expect(within(section).queryByRole("button", { name: "Play sample" })).toBeNull();
  });

  it("is reachable at #/settings", () => {
    expect(parseRoute("#/settings")).toEqual({ name: "settings" });
    expect(routeToHash({ name: "settings" })).toBe("#/settings");
  });
});
