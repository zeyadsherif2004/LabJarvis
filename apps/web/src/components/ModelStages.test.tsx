import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { readyFakeMode } from "../test/fixtures";
import type { ReadinessResponse, StageProvenance } from "../types";
import { ModelStages } from "./ModelStages";

function readiness(stages: Record<string, StageProvenance>): ReadinessResponse {
  return { ...readyFakeMode, profile: "local", stages };
}

const base = { detail: "", warmup: "ready" };

describe("ModelStages", () => {
  it("labels a loopback OpenAI-compatible server as running on this machine", () => {
    render(
      <ModelStages
        readiness={readiness({
          text: {
            ...base,
            provider: "openai_compatible",
            provider_label: "LM Studio",
            locality: "local",
            model: "served-model",
          },
        })}
      />,
    );
    expect(screen.getByText("on this machine via LM Studio")).toBeInTheDocument();
  });

  it("always names a hosted provider as hosted", () => {
    render(
      <ModelStages
        readiness={readiness({
          text: {
            ...base,
            provider: "openai_compatible",
            provider_label: "DeepSeek",
            locality: "hosted",
            model: "deepseek-flash",
          },
        })}
      />,
    );
    expect(screen.getByText("hosted by DeepSeek")).toBeInTheDocument();
  });
});
