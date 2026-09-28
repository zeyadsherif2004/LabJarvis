import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { encodeWav, type HandsFreeOptions } from "../../media/handsFreeListener";
import { HandsFreeListeningPanel } from "./HandsFreeListeningPanel";

function fakeListener() {
  const calls = { start: 0, pause: 0, destroy: 0 };
  let options: HandsFreeOptions | null = null;
  const create = vi.fn((given: HandsFreeOptions) => {
    options = given;
    return Promise.resolve({
      start: () => {
        calls.start += 1;
        return Promise.resolve();
      },
      pause: () => {
        calls.pause += 1;
        return Promise.resolve();
      },
      destroy: () => {
        calls.destroy += 1;
        return Promise.resolve();
      },
    });
  });
  return { calls, create, emit: (audio: Blob) => options?.onSegment(audio) };
}

describe("HandsFreeListeningPanel", () => {
  it("starts listening on request and forwards detected segments", async () => {
    const listener = fakeListener();
    const onSegment = vi.fn();
    render(
      <HandsFreeListeningPanel wakePhrase="Jarvis" busy={false} onSegment={onSegment} createListener={listener.create} />,
    );

    fireEvent.click(screen.getByRole("button", { name: /start hands-free listening/i }));

    expect(await screen.findByText(/Listening for "Jarvis/)).toBeInTheDocument();
    act(() => listener.emit(new Blob(["x"])));
    expect(onSegment).toHaveBeenCalledTimes(1);
  });

  it("pauses while the application is busy and switches off on request", async () => {
    const listener = fakeListener();
    const { rerender } = render(
      <HandsFreeListeningPanel wakePhrase="Jarvis" busy={false} onSegment={vi.fn()} createListener={listener.create} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /start hands-free listening/i }));
    await screen.findByText(/Listening for/);

    rerender(
      <HandsFreeListeningPanel wakePhrase="Jarvis" busy onSegment={vi.fn()} createListener={listener.create} />,
    );
    await waitFor(() => expect(screen.getByText(/paused while LabJarvis speaks/)).toBeInTheDocument());
    expect(listener.calls.pause).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: /stop hands-free listening/i }));
    await waitFor(() => expect(listener.calls.destroy).toBe(1));
  });

  it("reports a start failure and keeps push-to-talk as the fallback", async () => {
    render(
      <HandsFreeListeningPanel
        wakePhrase="Jarvis"
        busy={false}
        onSegment={vi.fn()}
        createListener={() => Promise.reject(new Error("no microphone"))}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /start hands-free listening/i }));

    expect(await screen.findByText(/push-to-talk remains available/)).toBeInTheDocument();
  });

  it("encodes detected audio as 16 kHz PCM WAV", async () => {
    const wav = encodeWav(new Float32Array([0, 0.5, -0.5, 1]));
    const bytes = new Uint8Array(await wav.arrayBuffer());

    expect(wav.type).toBe("audio/wav");
    expect(String.fromCharCode(...bytes.slice(0, 4))).toBe("RIFF");
    expect(bytes.length).toBe(44 + 8);
  });
});

function fakeListenerWithStart() {
  let options: HandsFreeOptions | null = null;
  const create = vi.fn((given: HandsFreeOptions) => {
    options = given;
    return Promise.resolve({
      start: () => Promise.resolve(),
      pause: () => Promise.resolve(),
      destroy: () => Promise.resolve(),
    });
  });
  return {
    create,
    speechStart: () => options?.onSpeechStart?.(),
    segment: (audio: Blob) => options?.onSegment(audio),
  };
}

describe("HandsFreeListeningPanel echo guard", () => {
  it("drops a segment that began while LabJarvis was speaking", async () => {
    const listener = fakeListenerWithStart();
    const onSegment = vi.fn();
    let speaking = true;
    render(
      <HandsFreeListeningPanel
        wakePhrase="Jarvis"
        busy={false}
        onSegment={onSegment}
        createListener={listener.create}
        isSpeaking={() => speaking}
        echoGuardMs={0}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /start hands-free listening/i }));
    await screen.findByText(/Listening for|paused while/);

    act(() => listener.speechStart());
    speaking = false;
    act(() => listener.segment(new Blob(["FAKE:confirm 10 millilitres"])));

    expect(onSegment).not.toHaveBeenCalled();
    expect(await screen.findByText(/Ignored 1 segment that overlapped/)).toBeInTheDocument();
  });

  it("drops a segment that starts within the guard window after speech ends", async () => {
    const listener = fakeListenerWithStart();
    const onSegment = vi.fn();
    let speaking = true;
    render(
      <HandsFreeListeningPanel
        wakePhrase="Jarvis"
        busy={false}
        onSegment={onSegment}
        createListener={listener.create}
        isSpeaking={() => speaking}
        echoGuardMs={60_000}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /start hands-free listening/i }));
    await screen.findByText(/Listening for|paused while/);
    act(() => listener.speechStart()); // records that LabJarvis is speaking
    act(() => listener.segment(new Blob(["x"])));
    speaking = false;

    act(() => listener.speechStart());
    act(() => listener.segment(new Blob(["FAKE:Jarvis, confirm 10 millilitres"])));

    expect(onSegment).not.toHaveBeenCalled();
  });

  it("forwards a segment spoken well after LabJarvis stopped", async () => {
    const listener = fakeListenerWithStart();
    const onSegment = vi.fn();
    render(
      <HandsFreeListeningPanel
        wakePhrase="Jarvis"
        busy={false}
        onSegment={onSegment}
        createListener={listener.create}
        isSpeaking={() => false}
        echoGuardMs={1_000}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /start hands-free listening/i }));
    await screen.findByText(/Listening for/);

    act(() => listener.speechStart());
    act(() => listener.segment(new Blob(["FAKE:Jarvis, confirm 10 millilitres"])));

    expect(onSegment).toHaveBeenCalledTimes(1);
  });
});
