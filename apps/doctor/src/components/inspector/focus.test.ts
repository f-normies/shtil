import { describe, expect, test } from "vitest";
import * as focus from "./focus";
import {
  closesOnEscape,
  initialFocus,
  insideModal,
  patientCardScope,
  reopens,
  returnFocus,
} from "./focus";

type ScopeState = { scope: string; withinScope?: string };
const closeOnScopeChange = (shown: ScopeState, nextScope: string): boolean | undefined =>
  (
    focus as unknown as {
      closesOnScopeChange?: (current: ScopeState, next: string) => boolean;
    }
  ).closesOnScopeChange?.(shown, nextScope);

describe("inspector scope changes", () => {
  test.each([
    ["/patients/p1/scales", "p1", "/patients/p1"],
    ["/doctor/patients/p1/therapy", "p1", "/doctor/patients/p1"],
  ])("uses the patient-card route root for %s", (pathname, patientId, expected) => {
    expect(patientCardScope(pathname, patientId)).toBe(expected);
  });

  test("chat stays open between sections of the same patient card", () => {
    expect(
      closeOnScopeChange(
        { scope: "/doctor/patients/p1", withinScope: "/doctor/patients/p1" },
        "/doctor/patients/p1/scales",
      ),
    ).toBe(false);
  });

  test.each(["/doctor/patients/p2", "/doctor/alerts"])(
    "chat closes after leaving its patient card for %s",
    (nextScope) => {
      expect(
        closeOnScopeChange(
          { scope: "/doctor/patients/p1", withinScope: "/doctor/patients/p1" },
          nextScope,
        ),
      ).toBe(true);
    },
  );

  test("source inspectors keep their existing exact-route scope", () => {
    expect(
      closeOnScopeChange({ scope: "/doctor/patients/p1/therapy" }, "/doctor/patients/p1"),
    ).toBe(true);
  });
});

describe("closesOnEscape", () => {
  test("Esc closes the panel", () => {
    expect(closesOnEscape({ key: "Escape", defaultPrevented: false }, false)).toBe(true);
  });

  test("other keys do not", () => {
    expect(closesOnEscape({ key: "Enter", defaultPrevented: false }, false)).toBe(false);
  });

  test("Esc taken by an upper layer (popover) does not close the panel", () => {
    expect(closesOnEscape({ key: "Escape", defaultPrevented: true }, false)).toBe(false);
  });

  test("Esc over a modal sheet closes the sheet, not the panel under it", () => {
    expect(closesOnEscape({ key: "Escape", defaultPrevented: false }, true)).toBe(false);
  });
});

describe("returnFocus", () => {
  function button(isConnected: boolean) {
    const calls: string[] = [];
    return { isConnected, focus: () => calls.push("focus"), calls };
  }

  test("focus goes back to the button that opened the inspector", () => {
    const trigger = button(true);

    returnFocus(trigger);

    expect(trigger.calls).toEqual(["focus"]);
  });

  test("a button gone from the page is left alone", () => {
    const trigger = button(false);

    returnFocus(trigger);

    expect(trigger.calls).toEqual([]);
  });

  test("no button, nothing to focus", () => {
    expect(() => returnFocus(null)).not.toThrow();
  });
});

describe("insideModal", () => {
  test("button inside an open modal sheet", () => {
    const trigger = { closest: (selector: string) => (selector === "dialog:modal" ? {} : null) };

    expect(insideModal(trigger)).toBe(true);
  });

  test("button on the page", () => {
    expect(insideModal({ closest: () => null })).toBe(false);
    expect(insideModal(null)).toBe(false);
  });
});

describe("initialFocus", () => {
  type Fake = { name: string; disabled?: boolean; hidden?: boolean };
  const element = ({ name, disabled = false, hidden = false }: Fake) => ({
    name,
    matches: (selector: string) => selector === ":disabled" && disabled,
    closest: (selector: string) => (selector === "[hidden]" && hidden ? {} : null),
  });
  const panel = (...items: Fake[]) => ({
    querySelectorAll: (selector: string) =>
      selector === "[data-autofocus]" ? items.map(element) : [],
  });

  test("the first marked control gets focus: the question field", () => {
    expect(initialFocus(panel({ name: "поле" }, { name: "Повторить" }))?.name).toBe("поле");
  });

  test("disabled and hidden controls are skipped", () => {
    const found = initialFocus(
      panel({ name: "поле", disabled: true }, { name: "скрыто", hidden: true }, { name: "назад" }),
    );
    expect(found?.name).toBe("назад");
  });

  test("nothing marked: the panel itself takes focus", () => {
    expect(initialFocus(panel())).toBeNull();
    expect(initialFocus(panel({ name: "поле", disabled: true }))).toBeNull();
  });
});

describe("reopens", () => {
  const shown = { id: "knowledge-question", scope: "/patients/p1/therapy" };

  test("the same content on the same page is kept, not recreated", () => {
    expect(reopens(shown, { id: "knowledge-question" }, "/patients/p1/therapy")).toBe(true);
  });

  test("other content, content without id or another page open anew", () => {
    expect(reopens(shown, { id: "source" }, "/patients/p1/therapy")).toBe(false);
    expect(reopens({ scope: shown.scope }, {}, "/patients/p1/therapy")).toBe(false);
    expect(reopens(shown, { id: "knowledge-question" }, "/patients/p2/therapy")).toBe(false);
    expect(reopens(null, { id: "knowledge-question" }, "/patients/p1/therapy")).toBe(false);
  });
});
