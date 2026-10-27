import { readFileSync } from "node:fs";
import type { Schemas } from "@compass/api-client";
import { describe, expect, test } from "vitest";
import { FALLBACK_HELP, readStoredHelp, storeHelp } from "./cache";

const HELP: Schemas["PatientHelpOut"] = {
  version: 1,
  checked_on: "2026-10-25",
  title: "Если сейчас очень тяжело",
  text: "Позвоните по номеру 112.",
  sources: [{ source: "s", locator: "п. 2", title: "Постановление", url: "https://example.org" }],
  contacts: [
    { id: "emergency", number: "112", label: "Позвонить 112", primary: true, sources: [] },
  ],
  triggers: [],
};

function memoryStorage(): Pick<Storage, "getItem" | "setItem"> {
  const values = new Map<string, string>();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => void values.set(key, value),
  };
}

describe("stored patient help", () => {
  test("the last server answer is read back", () => {
    const storage = memoryStorage();

    storeHelp(HELP, storage);

    expect(readStoredHelp(storage)).toEqual(HELP);
  });

  test("nothing stored gives null", () => {
    expect(readStoredHelp(memoryStorage())).toBeNull();
  });

  test.each([
    ["broken JSON", "{"],
    ["no contacts", JSON.stringify({ ...HELP, contacts: [] })],
    ["no triggers list", JSON.stringify({ ...HELP, triggers: undefined })],
  ])("%s is ignored", (_name, raw) => {
    const storage = memoryStorage();
    storage.setItem("compass.patient-help", raw);

    expect(readStoredHelp(storage)).toBeNull();
  });

  test("blocked storage reads as null and does not throw on write", () => {
    const blocked = {
      getItem: () => {
        throw new DOMException("denied", "SecurityError");
      },
      setItem: () => {
        throw new DOMException("denied", "SecurityError");
      },
    };

    expect(() => storeHelp(HELP, blocked)).not.toThrow();
    expect(readStoredHelp(blocked)).toBeNull();
  });
});

describe("fallback help", () => {
  test("the fallback number is the primary contact in knowledge", () => {
    // Спека #57, раздел 6: единственный номер в коде PWA совпадает с данными knowledge/.
    const yaml = readFileSync(
      new URL("../../../../knowledge/patient-help.yaml", import.meta.url),
      "utf8",
    );
    const primary = /number: "(\d+)"\s+label: "([^"]+)"\s+primary: true/.exec(yaml);

    expect(primary?.slice(1)).toEqual([
      FALLBACK_HELP.contacts[0]?.number,
      FALLBACK_HELP.contacts[0]?.label,
    ]);
    expect(FALLBACK_HELP.contacts).toHaveLength(1);
    expect(FALLBACK_HELP.text).toBeNull();
  });
});
