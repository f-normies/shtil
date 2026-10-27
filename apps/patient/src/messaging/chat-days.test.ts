import { chatDayLabel } from "@compass/ui";
import { expect, it } from "vitest";

const NOW = new Date("2026-10-27T12:00:00+03:00");

it("names today and yesterday in words and older days by date", () => {
  expect(chatDayLabel("2026-10-27T01:00:00+03:00", NOW)).toBe("Сегодня");
  expect(chatDayLabel("2026-10-26T23:30:00+03:00", NOW)).toBe("Вчера");
  expect(chatDayLabel("2026-10-16T19:00:00+03:00", NOW)).toBe("16 октября");
});

it("adds the year for another year's messages and tolerates bad dates", () => {
  expect(chatDayLabel("2025-12-31T19:00:00+03:00", NOW)).toBe("31 декабря 2025");
  expect(chatDayLabel("not a date", NOW)).toBe("");
});
