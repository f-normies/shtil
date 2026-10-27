import { describe, expect, test } from "vitest";
import { retryOnce } from "./delivery";

type Outcome = { response: { ok: boolean } };

/** Запрос, который отвечает по очереди заданными исходами; `null` — сбой сети. */
function sender(outcomes: (boolean | null)[]) {
  let calls = 0;
  const send = (): Promise<Outcome> => {
    const ok = outcomes[calls++];
    return ok === null
      ? Promise.reject(new TypeError("network"))
      : Promise.resolve({ response: { ok: ok ?? true } });
  };
  return { send, calls: () => calls };
}

describe("retryOnce", () => {
  test("sends a successful mark once", async () => {
    const s = sender([true]);

    expect(await retryOnce(s.send)).toBe(true);
    expect(s.calls()).toBe(1);
  });

  // openapi-fetch не бросает исключение на 4xx и 5xx, а при пустом теле ответа
  // оставляет `error` пустым. Неудачу видно только по `response.ok`.
  test("repeats a mark the server answered with an error", async () => {
    const s = sender([false, true]);

    expect(await retryOnce(s.send)).toBe(true);
    expect(s.calls()).toBe(2);
  });

  test("repeats a mark lost to a network error", async () => {
    const s = sender([null, true]);

    expect(await retryOnce(s.send)).toBe(true);
    expect(s.calls()).toBe(2);
  });

  test("gives up after the second failure", async () => {
    const s = sender([false, false, true]);

    expect(await retryOnce(s.send)).toBe(false);
    expect(s.calls()).toBe(2);
  });
});
