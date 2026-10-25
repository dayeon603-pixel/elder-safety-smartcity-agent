import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DRILL_TAG } from "../../lib/terminal/config";
import type { IdleData, StopInfo, TerminalMessage } from "../../lib/terminal/types";
import { AlertScreen, ConnectionDot, IdleScreen, StartScreen } from "./TerminalScreens";

const msg = (o: Partial<TerminalMessage>): TerminalMessage => ({ kind: "heat", title: "폭염 경보", body: "그늘에서 쉬세요", audio_url: null, stop_id: null, id: null, published_at: null, ...o });
const stop: StopInfo = { stop_id: "S1", name: "관악푸르지오아파트", lat: 1, lon: 2, sgg_code: "11620" };
const clock = { ampm: "오후", time: "3:07" };

describe("AlertScreen", () => {
  it.each(["heat", "fall", "rain", "clear"] as const)("renders %s with title, body and the drill tag", (kind) => {
    render(<AlertScreen message={msg({ kind })} voice="idle" flash onTap={() => undefined} />);
    expect(screen.getByTestId("terminal-alert")).toHaveAttribute("data-kind", kind);
    expect(screen.getByTestId("terminal-title")).toHaveTextContent("폭염 경보");
    expect(screen.getByText(DRILL_TAG)).toBeInTheDocument();
  });
  it("never renders a blank screen when title/body are empty", () => {
    render(<AlertScreen message={msg({ kind: "fall", title: "", body: "" })} voice="idle" flash={false} onTap={() => undefined} />);
    expect(screen.getByTestId("terminal-title").textContent).not.toBe("");
    expect(screen.getByTestId("terminal-body").textContent).not.toBe("");
  });
  it("offers a tap when audio was blocked", () => {
    const onTap = vi.fn();
    render(<AlertScreen message={msg({})} voice="blocked" flash={false} onTap={onTap} />);
    expect(screen.getByText(/화면을 누르면 음성/)).toBeInTheDocument();
    screen.getByTestId("terminal-replay").click();
    expect(onTap).toHaveBeenCalledOnce();
  });
  it("shows the flash only when asked", () => {
    const { rerender } = render(<AlertScreen message={msg({})} voice="idle" flash onTap={() => undefined} />);
    expect(screen.getByTestId("terminal-flash")).toBeInTheDocument();
    rerender(<AlertScreen message={msg({})} voice="idle" flash={false} onTap={() => undefined} />);
    expect(screen.queryByTestId("terminal-flash")).toBeNull();
  });
});

describe("IdleScreen", () => {
  const data: IdleData = {
    feelsC: 33.4, weatherLabel: "현재 체감온도",
    aed: { name: "대교타워", meters: 412, minutes: 9, basis: "line" }, aedNote: null,
    shelter: null, shelterNote: "자료 없음",
  };
  it("shows API values and honest gaps", () => {
    render(<IdleScreen stop={stop} clock={clock} data={data} wakeUnsupported={false} />);
    expect(screen.getByTestId("terminal-temp")).toHaveTextContent("33.4");
    expect(screen.getByTestId("terminal-aed")).toHaveTextContent("걸어서 9분");
    expect(screen.getByTestId("terminal-aed")).toHaveTextContent("412m (직선)");
    expect(screen.getByTestId("terminal-shelter")).toHaveTextContent("자료 없음");
    expect(screen.getByText("관악푸르지오아파트")).toBeInTheDocument();
    expect(screen.getByText("3:07")).toBeInTheDocument();
  });
  it("renders placeholders while loading and when temperature is missing", () => {
    const { rerender } = render(<IdleScreen stop={stop} clock={clock} data={null} wakeUnsupported={false} />);
    expect(screen.getByTestId("terminal-temp")).toHaveTextContent("확인 중");
    rerender(<IdleScreen stop={stop} clock={clock} data={{ ...data, feelsC: null, weatherLabel: null }} wakeUnsupported />);
    expect(screen.getByTestId("terminal-temp")).toHaveTextContent("자료 없음");
    expect(screen.getByText(/자동 잠금을 꺼/)).toBeInTheDocument();
  });
});

describe("misc", () => {
  it("start button and connection dot", () => {
    const onStart = vi.fn();
    render(<><StartScreen stopName="정류장" onStart={onStart} /><ConnectionDot state="reconnecting" /></>);
    screen.getByTestId("terminal-start").click();
    expect(onStart).toHaveBeenCalledOnce();
    expect(screen.getByTestId("terminal-conn")).toHaveAttribute("data-state", "reconnecting");
  });
});
