import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TOAST_MS } from "../config";
import { ToastProvider, useToast } from "./Toast";

function Trigger() {
  const t = useToast();
  return <button onClick={() => t.show("저장했습니다", "success")}>알림</button>;
}

afterEach(() => vi.useRealTimers());

describe("Toast", () => {
  it("announces in a polite live region and auto-dismisses", () => {
    vi.useFakeTimers();
    render(<ToastProvider><Trigger /></ToastProvider>);
    act(() => screen.getByRole("button", { name: "알림" }).click());
    expect(screen.getByRole("status")).toHaveTextContent("저장했습니다");
    expect(screen.getByRole("status")).toHaveAttribute("aria-live", "polite");
    act(() => void vi.advanceTimersByTime(TOAST_MS + 10));
    expect(screen.queryByText("저장했습니다")).toBeNull();
  });

  it("can be dismissed manually", async () => {
    const user = userEvent.setup();
    render(<ToastProvider><Trigger /></ToastProvider>);
    await user.click(screen.getByRole("button", { name: "알림" }));
    await user.click(screen.getByRole("button", { name: "알림 닫기" }));
    expect(screen.queryByText("저장했습니다")).toBeNull();
  });
});
