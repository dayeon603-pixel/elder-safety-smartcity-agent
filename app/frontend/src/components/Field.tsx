import { forwardRef, useId, useState } from "react";
import type { InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from "react";

interface FieldShellProps {
  label: string;
  hint?: ReactNode;
  error?: string | null;
  required?: boolean;
  optionalText?: string;
}

const CONTROL =
  "block w-full border bg-surface px-3 py-2 text-body text-ink placeholder:text-ink-faint " +
  "min-h-11 sm:min-h-10 disabled:bg-surface-sunken disabled:text-ink-faint";
const CONTROL_OK = "border-line-strong";
const CONTROL_ERR = "border-danger";

/** Label + control + hint + error. Error is linked via aria-describedby and announced politely. */
function Shell({
  id, label, hint, error, required, optionalText, children,
}: FieldShellProps & { id: string; children: (a: { "aria-describedby": string | undefined; "aria-invalid": boolean | undefined }) => ReactNode }) {
  const hintId = `${id}-hint`;
  const errId = `${id}-err`;
  const describedBy = [hint !== undefined ? hintId : null, error ? errId : null].filter(Boolean).join(" ") || undefined;
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="block text-small font-medium text-ink">
        {label}
        {required === true && <span aria-hidden="true" className="ml-1 text-danger">*</span>}
        {required === false && optionalText !== undefined && <span className="ml-2 font-normal text-ink-faint">{optionalText}</span>}
      </label>
      {children({ "aria-describedby": describedBy, "aria-invalid": error ? true : undefined })}
      {hint !== undefined && (
        <p id={hintId} className="text-caption text-ink-faint">
          {hint}
        </p>
      )}
      <p id={errId} aria-live="polite" className={error ? "text-small text-danger" : "sr-only"}>
        {error ? error : ""}
      </p>
    </div>
  );
}

export interface TextFieldProps extends FieldShellProps, Omit<InputHTMLAttributes<HTMLInputElement>, "id"> {
  id?: string;
  /** Element placed inside the right edge of the input (e.g. show/hide toggle). */
  adornment?: ReactNode;
}

export const TextField = forwardRef<HTMLInputElement, TextFieldProps>(function TextField(
  { id, label, hint, error, required, optionalText, adornment, className = "", ...rest },
  ref,
) {
  const auto = useId();
  const fid = id ?? auto;
  return (
    <Shell id={fid} label={label} hint={hint} error={error} required={required} optionalText={optionalText}>
      {(a) => (
        <div className="relative">
          <input
            ref={ref}
            id={fid}
            required={required}
            className={`${CONTROL} ${error ? CONTROL_ERR : CONTROL_OK} ${adornment !== undefined ? "pr-16" : ""} ${className}`}
            {...a}
            {...rest}
          />
          {adornment !== undefined && <div className="absolute inset-y-0 right-1 flex items-center">{adornment}</div>}
        </div>
      )}
    </Shell>
  );
});

export const PasswordField = forwardRef<HTMLInputElement, Omit<TextFieldProps, "type" | "adornment">>(function PasswordField(props, ref) {
  const [shown, setShown] = useState(false);
  return (
    <TextField
      ref={ref}
      {...props}
      type={shown ? "text" : "password"}
      adornment={
        <button
          type="button"
          onClick={() => setShown((s) => !s)}
          className="flex h-9 items-center px-2 text-small font-medium text-accent hover:text-accent-strong"
        >
          {shown ? "숨기기" : "보기"}
        </button>
      }
    />
  );
});

export interface TextAreaFieldProps extends FieldShellProps, Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "id"> {
  id?: string;
}

export function TextAreaField({ id, label, hint, error, required, optionalText, className = "", ...rest }: TextAreaFieldProps) {
  const auto = useId();
  const fid = id ?? auto;
  return (
    <Shell id={fid} label={label} hint={hint} error={error} required={required} optionalText={optionalText}>
      {(a) => <textarea id={fid} rows={3} className={`${CONTROL} ${error ? CONTROL_ERR : CONTROL_OK} ${className}`} {...a} {...rest} />}
    </Shell>
  );
}

export interface SelectFieldProps extends FieldShellProps, Omit<SelectHTMLAttributes<HTMLSelectElement>, "id"> {
  id?: string;
  options: readonly { value: string; label: string }[];
  placeholder?: string;
}

export function SelectField({ id, label, hint, error, required, optionalText, options, placeholder, className = "", ...rest }: SelectFieldProps) {
  const auto = useId();
  const fid = id ?? auto;
  return (
    <Shell id={fid} label={label} hint={hint} error={error} required={required} optionalText={optionalText}>
      {(a) => (
        <select id={fid} className={`${CONTROL} ${error ? CONTROL_ERR : CONTROL_OK} ${className}`} {...a} {...rest}>
          {placeholder !== undefined && <option value="">{placeholder}</option>}
          {options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      )}
    </Shell>
  );
}
