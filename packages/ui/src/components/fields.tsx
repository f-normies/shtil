import {
  type HTMLInputTypeAttribute,
  type InputHTMLAttributes,
  type MouseEvent as ReactMouseEvent,
  type ReactNode,
  type PointerEvent as ReactPointerEvent,
  type TextareaHTMLAttributes,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
} from "react";

type FieldSharedProps = {
  label: string;
  hint?: string;
  error?: string;
  hideLabel?: boolean;
  wrapperClassName?: string;
};

type InputFieldProps = FieldSharedProps &
  Omit<InputHTMLAttributes<HTMLInputElement>, "type"> & {
    type?: HTMLInputTypeAttribute;
    multiline?: false;
  };

type TextareaFieldProps = FieldSharedProps &
  TextareaHTMLAttributes<HTMLTextAreaElement> & {
    multiline: true;
    type?: never;
  };

export type TextFieldProps = InputFieldProps | TextareaFieldProps;

type SelectionSnapshot = {
  start: number | null;
  end: number | null;
  direction: "forward" | "backward" | "none" | null;
  restoreFocus: boolean;
};

const SHOW_PASSWORD_ICON = new URL("../assets/ikonka-glaz-vrach.svg", import.meta.url).href;
const HIDE_PASSWORD_ICON = new URL("../assets/ikonka-glaz-vrach-zakryt.svg", import.meta.url).href;
const PATIENT_SHOW_PASSWORD_ICON = new URL("../assets/ikonka-glaz-patsient.svg", import.meta.url)
  .href;
const PATIENT_HIDE_PASSWORD_ICON = new URL(
  "../assets/ikonka-glaz-patsient-zakryt.svg",
  import.meta.url,
).href;

function describedBy(
  supplied: string | undefined,
  hintId: string | undefined,
  errorId: string | undefined,
) {
  const ids = [supplied, hintId, errorId].filter(Boolean);
  return ids.length > 0 ? ids.join(" ") : undefined;
}

function SingleLineField({
  multiline: _multiline,
  id: suppliedId,
  label,
  hint,
  error,
  hideLabel = false,
  wrapperClassName = "",
  className = "",
  type = "text",
  disabled = false,
  "aria-describedby": suppliedDescription,
  "aria-invalid": suppliedInvalid,
  onInput: suppliedOnInput,
  onKeyDown: suppliedOnKeyDown,
  onPointerDown: suppliedOnPointerDown,
  onBlur: suppliedOnBlur,
  ...inputProps
}: InputFieldProps) {
  void _multiline;
  const generatedId = useId();
  const id = suppliedId ?? generatedId;
  const hintId = hint ? `${id}-hint` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  const inputRef = useRef<HTMLInputElement>(null);
  const selectionRef = useRef<SelectionSnapshot | null>(null);
  const pendingSelectionFrameRef = useRef<number | null>(null);
  const pointerActivationRef = useRef(false);
  const [passwordVisible, setPasswordVisible] = useState(false);
  const [toggled, setToggled] = useState(false);
  const isPassword = type === "password";
  const actualType = isPassword && passwordVisible ? "text" : type;
  const isPatientMode =
    typeof document !== "undefined" && document.documentElement.dataset.forma === "patsient";

  function cancelPendingSelectionRestore() {
    const pendingFrameId = pendingSelectionFrameRef.current;
    if (pendingFrameId === null) return;

    cancelAnimationFrame(pendingFrameId);
    pendingSelectionFrameRef.current = null;
    selectionRef.current = null;
  }

  useLayoutEffect(() => {
    const selection = selectionRef.current;
    const input = inputRef.current;
    if (!selection || !input || input.type !== actualType) return;

    if (pendingSelectionFrameRef.current !== null) {
      cancelAnimationFrame(pendingSelectionFrameRef.current);
      pendingSelectionFrameRef.current = null;
    }

    function restoreSelection(snapshot: SelectionSnapshot) {
      const currentInput = inputRef.current;
      if (!currentInput) return;

      if (snapshot.restoreFocus) currentInput.focus();
      if (snapshot.start !== null && snapshot.end !== null) {
        currentInput.setSelectionRange(
          snapshot.start,
          snapshot.end,
          snapshot.direction ?? undefined,
        );
      }
    }

    restoreSelection(selection);

    const frameId = requestAnimationFrame(() => {
      if (pendingSelectionFrameRef.current !== frameId) return;
      restoreSelection(selection);
      pendingSelectionFrameRef.current = null;
      if (selectionRef.current === selection) selectionRef.current = null;
    });
    pendingSelectionFrameRef.current = frameId;
  }, [actualType]);

  useEffect(
    () => () => {
      if (pendingSelectionFrameRef.current === null) return;
      cancelAnimationFrame(pendingSelectionFrameRef.current);
      pendingSelectionFrameRef.current = null;
    },
    [],
  );

  function handlePasswordPointerDown(event: ReactPointerEvent<HTMLButtonElement>) {
    if (!event.isPrimary || event.button !== 0) return;
    pointerActivationRef.current = true;
    event.preventDefault();
  }

  function togglePassword(event: ReactMouseEvent<HTMLButtonElement>) {
    const input = inputRef.current;
    if (!input) return;

    if (!selectionRef.current) {
      selectionRef.current = {
        start: input.selectionStart,
        end: input.selectionEnd,
        direction: input.selectionDirection,
        restoreFocus: pointerActivationRef.current || event.detail > 0,
      };
    }
    pointerActivationRef.current = false;
    setToggled(true);
    setPasswordVisible((visible) => !visible);
  }

  return (
    <div
      className={`ui-field ${wrapperClassName}`.trim()}
      data-invalid={Boolean(error)}
      data-disabled={disabled}
    >
      <label
        className={`ui-field__label ${hideLabel ? "ui-field__label--visually-hidden" : ""}`.trim()}
        htmlFor={id}
      >
        {label}
      </label>
      <div
        className={`ui-field__control ${isPassword ? "ui-field__control--password" : ""}`.trim()}
      >
        <input
          {...inputProps}
          ref={inputRef}
          id={id}
          type={actualType}
          disabled={disabled}
          aria-describedby={describedBy(suppliedDescription, hintId, errorId)}
          aria-invalid={error ? true : suppliedInvalid}
          className={`ui-field__input ${className}`.trim()}
          onInput={(event) => {
            cancelPendingSelectionRestore();
            suppliedOnInput?.(event);
          }}
          onKeyDown={(event) => {
            cancelPendingSelectionRestore();
            suppliedOnKeyDown?.(event);
          }}
          onPointerDown={(event) => {
            cancelPendingSelectionRestore();
            suppliedOnPointerDown?.(event);
          }}
          onBlur={(event) => {
            cancelPendingSelectionRestore();
            suppliedOnBlur?.(event);
          }}
        />
        {isPassword && (
          <button
            type="button"
            className="ui-field__password-toggle"
            aria-label={passwordVisible ? "Скрыть пароль" : "Показать пароль"}
            title={passwordVisible ? "Скрыть пароль" : "Показать пароль"}
            aria-pressed={passwordVisible}
            aria-controls={id}
            disabled={disabled}
            onPointerDown={handlePasswordPointerDown}
            onPointerCancel={() => {
              pointerActivationRef.current = false;
            }}
            onClick={togglePassword}
          >
            <img
              src={
                isPatientMode
                  ? passwordVisible
                    ? PATIENT_HIDE_PASSWORD_ICON
                    : PATIENT_SHOW_PASSWORD_ICON
                  : passwordVisible
                    ? HIDE_PASSWORD_ICON
                    : SHOW_PASSWORD_ICON
              }
              alt=""
              width="20"
              height="20"
            />
          </button>
        )}
      </div>
      {isPassword && (
        // Смена видимости озвучивается: у кнопки меняется только название (кадр 01б).
        <span className="sr-only" role="status">
          {toggled ? (passwordVisible ? "Пароль показан" : "Пароль скрыт") : ""}
        </span>
      )}
      {hint && (
        <p className="ui-field__hint" id={hintId}>
          {hint}
        </p>
      )}
      {error && (
        <p className="ui-field__error" id={errorId} role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

function MultilineField({
  multiline: _multiline,
  id: suppliedId,
  label,
  hint,
  error,
  hideLabel = false,
  wrapperClassName = "",
  className = "",
  disabled = false,
  "aria-describedby": suppliedDescription,
  "aria-invalid": suppliedInvalid,
  ...textareaProps
}: TextareaFieldProps) {
  void _multiline;
  const generatedId = useId();
  const id = suppliedId ?? generatedId;
  const hintId = hint ? `${id}-hint` : undefined;
  const errorId = error ? `${id}-error` : undefined;

  return (
    <div
      className={`ui-field ${wrapperClassName}`.trim()}
      data-invalid={Boolean(error)}
      data-disabled={disabled}
    >
      <label
        className={`ui-field__label ${hideLabel ? "ui-field__label--visually-hidden" : ""}`.trim()}
        htmlFor={id}
      >
        {label}
      </label>
      <div className="ui-field__control ui-field__control--multiline">
        <textarea
          {...textareaProps}
          id={id}
          disabled={disabled}
          aria-describedby={describedBy(suppliedDescription, hintId, errorId)}
          aria-invalid={error ? true : suppliedInvalid}
          className={`ui-field__input ui-field__textarea ${className}`.trim()}
        />
      </div>
      {hint && (
        <p className="ui-field__hint" id={hintId}>
          {hint}
        </p>
      )}
      {error && (
        <p className="ui-field__error" id={errorId} role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

/** Поле с подписью и сообщениями, которое может быть однострочным или многострочным. */
export function TextField(props: TextFieldProps) {
  return props.multiline ? <MultilineField {...props} /> : <SingleLineField {...props} />;
}

export function Checkbox({
  children,
  ...props
}: Omit<InputHTMLAttributes<HTMLInputElement>, "type"> & { children: ReactNode }) {
  const id = useId();
  return (
    <div className="flex items-start gap-3">
      <input id={id} type="checkbox" className="mt-0.5 size-5 shrink-0 accent-primary" {...props} />
      <label htmlFor={id} className="text-sm text-ink">
        {children}
      </label>
    </div>
  );
}
