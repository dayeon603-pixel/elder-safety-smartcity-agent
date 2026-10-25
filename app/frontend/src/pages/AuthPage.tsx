import { useRef, useState } from "react";
import type { FormEvent } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import { describeError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { PageHeader } from "../components/AppShell";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { PasswordField, TextField } from "../components/Field";
import { Notice } from "../components/Notice";
import { useToast } from "../components/Toast";
import { MIN_PASSWORD_LEN } from "../config";
import type { Validator } from "../lib/validate";
import { validateConfirm, validateEmail, validateExistingPassword, validateNewPassword } from "../lib/validate";

type Mode = "login" | "register";
type Field = "email" | "password" | "confirm";
type Errors = Partial<Record<Field, string | null>>;

const COPY: Record<Mode, { title: string; lead: string; submit: string; altLead: string; altLink: string; altTo: string }> = {
  login: {
    title: "로그인",
    lead: "데이터 수집처럼 기록이 남는 작업에는 로그인이 필요합니다. 조회는 로그인 없이 가능합니다.",
    submit: "로그인",
    altLead: "계정이 없으신가요?",
    altLink: "회원가입",
    altTo: "/register",
  },
  register: {
    title: "회원가입",
    lead: `이메일과 ${MIN_PASSWORD_LEN}자 이상의 비밀번호만 필요합니다. 비밀번호는 서버에 해시로만 저장됩니다.`,
    submit: "가입하고 시작하기",
    altLead: "이미 계정이 있으신가요?",
    altLink: "로그인",
    altTo: "/login",
  },
};

interface LocationState {
  from?: string;
}

/** Shared login/register form. Inline validation on blur and on submit; server errors in a live alert. */
export function AuthPage({ mode }: { mode: Mode }) {
  const { session, login, register } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as LocationState | null)?.from ?? "/data";

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [errors, setErrors] = useState<Errors>({});
  const [serverError, setServerError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const refs = { email: useRef<HTMLInputElement>(null), password: useRef<HTMLInputElement>(null), confirm: useRef<HTMLInputElement>(null) };

  if (session !== null && !busy) return <Navigate to={from} replace />;

  const copy = COPY[mode];
  const pwValidator: Validator = mode === "register" ? validateNewPassword : validateExistingPassword;
  const checks: Record<Field, () => string | null> = {
    email: () => validateEmail(email),
    password: () => pwValidator(password),
    confirm: () => (mode === "register" ? validateConfirm(password)(confirm) : null),
  };
  const fields: Field[] = mode === "register" ? ["email", "password", "confirm"] : ["email", "password"];

  const blur = (f: Field) => (): void => setErrors((e) => ({ ...e, [f]: checks[f]() }));

  const submit = async (ev: FormEvent): Promise<void> => {
    ev.preventDefault();
    setServerError(null);
    const next: Errors = Object.fromEntries(fields.map((f) => [f, checks[f]()]));
    setErrors(next);
    const bad = fields.find((f) => next[f]);
    if (bad !== undefined) {
      refs[bad].current?.focus();
      return;
    }
    setBusy(true);
    try {
      if (mode === "login") {
        await login(email.trim(), password);
        toast.show("로그인했습니다.", "success");
      } else if ((await register(email.trim(), password)) === "signed-in") {
        toast.show("가입 요청이 처리되어 로그인되었습니다.", "success");
      } else {
        toast.show("가입 요청이 처리되었습니다. 로그인해 주세요.", "info");
        navigate("/login", { replace: true, state: { from } });
        return;
      }
      navigate(from, { replace: true });
    } catch (err) {
      setServerError(describeError(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto max-w-md">
      <PageHeader title={copy.title} lead={copy.lead} />
      <Card>
        <form onSubmit={(e) => void submit(e)} noValidate className="space-y-5">
          {serverError !== null && <Notice tone="danger">{serverError}</Notice>}
          <TextField
            ref={refs.email}
            label="이메일"
            type="email"
            name="email"
            autoComplete="email"
            inputMode="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            onBlur={blur("email")}
            error={errors.email}
          />
          <PasswordField
            ref={refs.password}
            label="비밀번호"
            name="password"
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onBlur={blur("password")}
            error={errors.password}
            hint={mode === "register" ? `${MIN_PASSWORD_LEN}자 이상. 현재 ${password.length}자` : undefined}
          />
          {mode === "register" && (
            <PasswordField
              ref={refs.confirm}
              label="비밀번호 확인"
              name="confirm"
              autoComplete="new-password"
              required
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              onBlur={blur("confirm")}
              error={errors.confirm}
            />
          )}
          {/* mousedown preventDefault: otherwise the field blur adds an error line, the button shifts, and the click is lost. */}
          <Button type="submit" variant="primary" loading={busy} className="w-full" onMouseDown={(e) => e.preventDefault()}>
            {busy ? "처리 중" : copy.submit}
          </Button>
        </form>
        <p className="mt-6 border-t border-line pt-4 text-small text-ink-muted">
          {copy.altLead} <Link to={copy.altTo} state={location.state}>{copy.altLink}</Link>
        </p>
      </Card>
    </div>
  );
}
