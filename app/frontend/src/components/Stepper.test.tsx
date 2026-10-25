import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Stepper } from "./Stepper";

const STEPS = [{ id: "a", label: "입력" }, { id: "b", label: "분석" }, { id: "c", label: "결과" }];

describe("Stepper", () => {
  it("marks the current step and only lets reached steps be clicked", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(<Stepper steps={STEPS} current={1} reached={1} onSelect={onSelect} />);
    const items = screen.getAllByRole("listitem");
    expect(items[1]).toHaveAttribute("aria-current", "step");
    expect(items[0]).not.toHaveAttribute("aria-current");
    expect(screen.getAllByRole("button")).toHaveLength(1); // only step 1 (done)
    await user.click(screen.getByRole("button", { name: /입력/ }));
    expect(onSelect).toHaveBeenCalledWith(0);
  });
});
