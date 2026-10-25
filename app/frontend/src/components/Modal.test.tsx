import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { Modal } from "./Modal";

function Harness() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button onClick={() => setOpen(true)}>열기</button>
      <Modal open={open} title="확인" onClose={() => setOpen(false)} footer={<button onClick={() => setOpen(false)}>닫기</button>}>
        <input aria-label="입력" />
      </Modal>
    </>
  );
}

describe("Modal", () => {
  it("is a labelled modal dialog, traps Tab, closes on Escape and restores focus", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const opener = screen.getByRole("button", { name: "열기" });
    await user.click(opener);
    const dlg = screen.getByRole("dialog", { name: "확인" });
    expect(dlg).toHaveAttribute("aria-modal", "true");
    expect(screen.getByLabelText("입력")).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("button", { name: "닫기" })).toHaveFocus();
    await user.tab(); // wraps
    expect(screen.getByLabelText("입력")).toHaveFocus();
    await user.tab({ shift: true }); // wraps backwards
    expect(screen.getByRole("button", { name: "닫기" })).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(opener).toHaveFocus();
  });
});
