import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { PasswordField, TextField } from "./Field";

describe("Field", () => {
  it("links label, hint and error; marks invalid", () => {
    render(<TextField label="이메일" hint="회사 메일" error="형식이 올바르지 않습니다." defaultValue="x" />);
    const input = screen.getByLabelText("이메일");
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveAccessibleDescription("회사 메일 형식이 올바르지 않습니다.");
  });

  it("is valid-by-default with no error", () => {
    render(<TextField label="이름" />);
    expect(screen.getByLabelText("이름")).not.toHaveAttribute("aria-invalid");
  });

  it("toggles password visibility", async () => {
    const user = userEvent.setup();
    render(<PasswordField label="비밀번호" />);
    const input = screen.getByLabelText("비밀번호");
    expect(input).toHaveAttribute("type", "password");
    await user.click(screen.getByRole("button", { name: "보기" }));
    expect(input).toHaveAttribute("type", "text");
    expect(screen.getByRole("button", { name: "숨기기" })).toBeInTheDocument();
  });
});
