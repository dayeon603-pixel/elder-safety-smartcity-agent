import { describe, expect, it } from "vitest";
import { addressedTo, messageAgeMs, messageKey, parseTerminalMessage, resolveAudioUrl } from "./message";

describe("parseTerminalMessage", () => {
  it("parses all four kinds and nulls absent optionals", () => {
    for (const kind of ["heat", "fall", "rain", "clear"]) {
      const m = parseTerminalMessage(JSON.stringify({ kind, title: "t", body: "b" }));
      expect(m).toMatchObject({ kind, title: "t", body: "b", audio_url: null, stop_id: null, id: null });
    }
  });
  it("accepts an envelope and numeric ids", () => {
    expect(parseTerminalMessage(JSON.stringify({ message: { kind: "fall", title: "t", body: "b", id: 7 } }))?.id).toBe("7");
  });
  it("rejects bad kinds, non-objects and bad JSON", () => {
    expect(parseTerminalMessage('{"kind":"x"}')).toBeNull();
    expect(parseTerminalMessage("[]")).toBeNull();
    expect(parseTerminalMessage("42")).toBeNull();
    expect(parseTerminalMessage("{")).toBeNull();
    expect(parseTerminalMessage("null")).toBeNull();
  });
});

describe("helpers", () => {
  it("messageKey prefers id", () => {
    const a = parseTerminalMessage('{"kind":"heat","title":"a","id":"1"}')!;
    const b = parseTerminalMessage('{"kind":"heat","title":"b","id":"1"}')!;
    expect(messageKey(a)).toBe(messageKey(b));
  });
  it("messageAgeMs is null without a parseable timestamp", () => {
    const m = parseTerminalMessage('{"kind":"heat","published_at":"nope"}')!;
    expect(messageAgeMs(m, 0)).toBeNull();
    const t = parseTerminalMessage('{"kind":"heat","published_at":"2026-10-24T00:00:00Z"}')!;
    expect(messageAgeMs(t, Date.parse("2026-10-24T00:00:05Z"))).toBe(5000);
  });
  it("resolveAudioUrl keeps the phone on the same origin", () => {
    expect(resolveAudioUrl("/cap/alerts/x/audio.wav", "/api")).toBe("/api/cap/alerts/x/audio.wav");
    expect(resolveAudioUrl("/api/cap/x.wav", "/api")).toBe("/api/cap/x.wav");
    expect(resolveAudioUrl("http://127.0.0.1:8010/cap/x.wav?v=1", "/api")).toBe("/api/cap/x.wav?v=1");
    expect(resolveAudioUrl("cap/x.wav", "/api")).toBe("/api/cap/x.wav");
  });
  it("addressedTo: broadcast or matching stop", () => {
    const m = parseTerminalMessage('{"kind":"heat","stop_id":"S1"}')!;
    expect(addressedTo(m, "S1")).toBe(true);
    expect(addressedTo(m, "S2")).toBe(false);
    expect(addressedTo({ ...m, stop_id: null }, "S2")).toBe(true);
  });
});
