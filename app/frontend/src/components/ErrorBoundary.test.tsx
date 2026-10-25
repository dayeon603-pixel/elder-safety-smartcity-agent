import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ErrorBoundary } from "./ErrorBoundary";

let shouldThrow = true;
function Bomb() {
  if (shouldThrow) throw new Error("boom");
  return <p>복구됨</p>;
}

describe("ErrorBoundary", () => {
  it("shows a friendly alert and recovers on retry", async () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => undefined); // React logs caught render errors
    const user = userEvent.setup();
    shouldThrow = true;
    render(
      <ErrorBoundary>
        <Bomb />
      </ErrorBoundary>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("문제가 생겼습니다");
    shouldThrow = false;
    await user.click(screen.getByRole("button", { name: "다시 시도" }));
    expect(screen.getByText("복구됨")).toBeInTheDocument();
    spy.mockRestore();
  });

  it("clears the error when resetKey changes", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => undefined);
    shouldThrow = true;
    const { rerender } = render(<ErrorBoundary resetKey="a"><Bomb /></ErrorBoundary>);
    expect(screen.getByRole("alert")).toBeInTheDocument();
    shouldThrow = false;
    rerender(<ErrorBoundary resetKey="b"><Bomb /></ErrorBoundary>);
    expect(screen.getByText("복구됨")).toBeInTheDocument();
    spy.mockRestore();
  });
});
