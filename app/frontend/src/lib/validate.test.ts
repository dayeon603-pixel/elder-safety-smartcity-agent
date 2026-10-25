import { describe, expect, it } from "vitest";
import {
  suggestDatasetName, validateConfirm, validateDatasetName, validateEmail, validateLicense, validateNewPassword, validateSourceUrl,
} from "./validate";

describe("validate", () => {
  it("email", () => {
    expect(validateEmail("")).toMatch(/입력/);
    expect(validateEmail("a@b")).toMatch(/형식/);
    expect(validateEmail(" user@example.com ")).toBeNull();
  });
  it("password length is counted and reported", () => {
    expect(validateNewPassword("short")).toContain("현재 5자");
    expect(validateNewPassword("x".repeat(12))).toBeNull();
    expect(validateNewPassword("x".repeat(129))).toMatch(/128자 이하/);
  });
  it("confirm", () => {
    expect(validateConfirm("abc")("abd")).toMatch(/일치/);
    expect(validateConfirm("abc")("abc")).toBeNull();
  });
  it("dataset name mirrors the backend pattern", () => {
    expect(validateDatasetName("wdbc-v1.0")).toBeNull();
    expect(validateDatasetName("-bad")).not.toBeNull();
    expect(validateDatasetName("한글")).not.toBeNull();
  });
  it("source url and license are mandatory", () => {
    expect(validateSourceUrl("")).toMatch(/필수/);
    expect(validateSourceUrl("ftp://x")).not.toBeNull();
    expect(validateSourceUrl("https://a.kr/x")).toBeNull();
    expect(validateLicense(" ")).not.toBeNull();
    expect(validateLicense("CC BY 4.0")).toBeNull();
  });
  it("suggests a valid name from a file name", () => {
    const n = suggestDatasetName("_서울 data (1).csv");
    expect(validateDatasetName(n)).toBeNull();
  });
});
