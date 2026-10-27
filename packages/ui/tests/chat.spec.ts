import { expect, test } from "@playwright/test";

test("keeps the controlled draft until local save succeeds", async ({ page }) => {
  await page.goto("/?case=chat&forma=vrach&tsvet=vrach");
  const draft = page.getByRole("textbox", { name: "Сообщение пациентке" });

  await draft.fill("Запись в очередь");
  await page.getByRole("button", { name: "Отправить" }).click();

  await expect(draft).toHaveValue("Запись в очередь");
  await expect(page.getByTestId("saved-message")).toHaveText("Запись в очередь");
  await expect(draft).toHaveValue("");
});

test("preserves the draft and explains a failed local save", async ({ page }) => {
  await page.goto("/?case=chat&forma=vrach&tsvet=vrach");
  const draft = page.getByRole("textbox", { name: "Сообщение пациентке" });

  await draft.fill("Не сохраняй");
  await page.getByRole("button", { name: "Отправить" }).click();

  await expect(page.getByRole("alert")).toHaveText(
    "Не удалось сохранить сообщение. Текст остался в поле.",
  );
  await expect(draft).toHaveValue("Не сохраняй");
});

test("shows chat history and reads an incoming message only when it is visible", async ({
  page,
}) => {
  await page.goto("/?case=chat&forma=vrach&tsvet=vrach");

  await expect(page.getByRole("log")).toBeVisible();
  await expect(page.getByText("Марина Лебедева", { exact: true })).toBeVisible();
  await expect(page.getByText("Добрый вечер, как вы себя чувствуете?")).toBeVisible();
  await expect(page.getByTestId("read-through")).toHaveText("1");
});

test("keeps text sending available when microphone permission is denied", async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: {
        getUserMedia: async () => {
          throw new DOMException("Permission denied", "NotAllowedError");
        },
      },
    });
  });
  await page.goto("/?case=chat&forma=patsient&tsvet=patsient-prokhladnaya");

  await page.getByRole("button", { name: "Начать запись" }).click();

  await expect(page.getByRole("alert")).toHaveText(
    "Не удалось получить доступ к микрофону. Можно отправить текстовое сообщение.",
  );
  await expect(page.getByRole("textbox", { name: "Сообщение врачу" })).toBeEnabled();
});

test("stops recording and keeps a prepared voice unsent while the chat is disabled", async ({
  page,
}) => {
  await page.addInitScript(() => {
    const state = { trackStops: 0 };
    Object.defineProperty(window, "__voiceTest", { configurable: true, value: state });
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: {
        getUserMedia: async () => ({
          getTracks: () => [
            {
              stop: () => {
                state.trackStops += 1;
              },
            },
          ],
        }),
      },
    });
    Object.defineProperty(window, "MediaRecorder", {
      configurable: true,
      value: class TestMediaRecorder {
        static isTypeSupported() {
          return true;
        }
        state = "inactive";
        mimeType = "audio/webm;codecs=opus";
        ondataavailable: ((event: { data: Blob }) => void) | null = null;
        onstop: (() => void) | null = null;
        start() {
          this.state = "recording";
        }
        stop() {
          if (this.state === "inactive") return;
          this.state = "inactive";
          this.ondataavailable?.({ data: new Blob(["voice"]) });
          this.onstop?.();
        }
      },
    });
  });
  await page.goto("/?case=chat&forma=patsient&tsvet=patsient-prokhladnaya&voice-controls=1");

  await page.getByRole("button", { name: "Начать запись" }).click();
  await expect(page.getByText(/Идёт запись/)).toBeVisible();
  await page.getByRole("button", { name: "Отключить чат" }).click();

  const sendVoice = page.getByRole("button", { name: "Отправить голосовое" });
  await expect(sendVoice).toBeDisabled();
  await expect
    .poll(() =>
      page.evaluate(
        () => (window as unknown as { __voiceTest: { trackStops: number } }).__voiceTest.trackStops,
      ),
    )
    .toBe(1);
  await sendVoice.evaluate((button: HTMLButtonElement) => button.click());
  await expect(page.getByTestId("voice-count")).toHaveText("0");

  await page.getByRole("button", { name: "Включить чат" }).click();
  await expect(sendVoice).toBeEnabled();
  await sendVoice.click();
  await expect(page.getByTestId("voice-count")).toHaveText("1");
});

test("preserves the scroll anchor when older history arrives before loading finishes", async ({
  page,
}) => {
  await page.goto("/?case=chat&forma=vrach&tsvet=vrach&pagination=1");

  const log = page.getByRole("log");
  const anchor = page.getByText("Историческое сообщение 8.", { exact: false });
  const before = await log.evaluate((element) => {
    element.style.overflowAnchor = "none";
    element.scrollTop = 240;
    return { top: anchorTop(element), scrollTop: element.scrollTop };
    function anchorTop(container: HTMLElement) {
      const target = [...container.querySelectorAll<HTMLElement>(".ui-chat-message__text")].find(
        (node) => node.textContent?.startsWith("Историческое сообщение 8."),
      );
      return target?.getBoundingClientRect().top ?? Number.NaN;
    }
  });
  await log.evaluate((element) => {
    (element.querySelector(".ui-chat__older button") as HTMLButtonElement).click();
  });
  await expect(page.getByText("Старое сообщение для проверки позиции прокрутки.")).toBeAttached();
  await expect(page.getByTestId("older-loading")).toHaveText("true");
  await expect(page.getByTestId("older-loading")).toHaveText("false");
  const after = await anchor.evaluate((element) => element.getBoundingClientRect().top);

  expect(Math.abs(after - before.top)).toBeLessThan(4);
  expect(before.scrollTop).toBeGreaterThan(0);
});

test("makes the chat history keyboard focusable", async ({ page }) => {
  await page.goto("/?case=chat&forma=vrach&tsvet=vrach");

  const log = page.getByRole("log");
  await expect(log).toHaveJSProperty("tabIndex", 0);
});
