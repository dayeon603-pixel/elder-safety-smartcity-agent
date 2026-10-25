import { MAX_PASSWORD_LEN, MIN_PASSWORD_LEN } from "../config";

/** Mirrors backend/app/schemas.py patterns so the user sees the problem before the round trip. */
const EMAIL_RE = /^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,189}\.[A-Za-z]{2,24}$/;
const DATASET_NAME_RE = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/;
const URL_RE = /^https?:\/\/\S{1,2040}$/;
export const MAX_LICENSE_LEN = 64;
export const MAX_DESCRIPTION_LEN = 500;
export const MAX_COLUMN_LEN = 128;

export type Validator = (value: string) => string | null;

export const validateEmail: Validator = (v) => {
  const s = v.trim();
  if (s === "") return "이메일을 입력해 주세요.";
  if (!EMAIL_RE.test(s)) return "이메일 형식이 올바르지 않습니다. 예: name@example.com";
  return null;
};

export const validateNewPassword: Validator = (v) => {
  if (v === "") return "비밀번호를 입력해 주세요.";
  if (v.length < MIN_PASSWORD_LEN) return `비밀번호는 ${MIN_PASSWORD_LEN}자 이상이어야 합니다. 현재 ${v.length}자입니다.`;
  if (v.length > MAX_PASSWORD_LEN) return `비밀번호는 ${MAX_PASSWORD_LEN}자 이하여야 합니다.`;
  return null;
};

export const validateExistingPassword: Validator = (v) => (v === "" ? "비밀번호를 입력해 주세요." : null);

export function validateConfirm(password: string): Validator {
  return (v) => (v === "" ? "비밀번호를 한 번 더 입력해 주세요." : v !== password ? "비밀번호가 일치하지 않습니다." : null);
}

export const validateDatasetName: Validator = (v) => {
  const s = v.trim();
  if (s === "") return "데이터셋 이름을 입력해 주세요.";
  if (!DATASET_NAME_RE.test(s)) return "영문·숫자로 시작하고 영문, 숫자, _ . - 만 64자 이내로 쓸 수 있습니다.";
  return null;
};

export const validateSourceUrl: Validator = (v) => {
  const s = v.trim();
  if (s === "") return "출처 URL을 입력해 주세요. 출처 표기는 필수입니다.";
  if (!URL_RE.test(s)) return "http:// 또는 https:// 로 시작하는 주소를 입력해 주세요.";
  return null;
};

export const validateLicense: Validator = (v) => {
  const s = v.trim();
  if (s === "") return "라이선스를 입력해 주세요. 예: CC BY 4.0";
  if (s.length > MAX_LICENSE_LEN) return `라이선스는 ${MAX_LICENSE_LEN}자 이하로 입력해 주세요.`;
  return null;
};

export const validateDescription: Validator = (v) =>
  v.length > MAX_DESCRIPTION_LEN ? `설명은 ${MAX_DESCRIPTION_LEN}자 이하로 입력해 주세요. 현재 ${v.length}자입니다.` : null;

export const validateColumn: Validator = (v) =>
  v.length > MAX_COLUMN_LEN ? `열 이름은 ${MAX_COLUMN_LEN}자 이하여야 합니다.` : null;

/** Suggest a dataset name from a CSV file name that satisfies DATASET_NAME_RE. */
export function suggestDatasetName(fileName: string): string {
  const base = fileName.replace(/\.csv$/i, "").replace(/[^A-Za-z0-9_.-]+/g, "_").replace(/^[^A-Za-z0-9]+/, "");
  return base.slice(0, 64);
}
