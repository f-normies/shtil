import type { Schemas } from "@compass/api-client";
import { describe, expect, test } from "vitest";
import { changedText, commonPeriod, periodLabel } from "./surveys";

type Setting = Schemas["ScaleSettingOut"];

const DEFAULT: Setting = {
  scale_id: "phq-9",
  scale_title: "PHQ-9",
  enabled: true,
  period_days: 14,
  default_period_days: 14,
  updated_at: null,
  updated_by: null,
  updated_by_name: null,
};

describe("periodLabel", () => {
  // Спека #180: врач выбирает «раз в неделю» или «раз в две недели».
  test("7 и 14 дней — словами", () => {
    expect(periodLabel(7)).toBe("Раз в неделю");
    expect(periodLabel(14)).toBe("Раз в две недели");
  });

  test("другой период из knowledge/ — числом дней", () => {
    expect(periodLabel(10)).toBe("Раз в 10 дней");
  });
});

describe("changedText", () => {
  test("пока врач не менял — по умолчанию", () => {
    expect(changedText(DEFAULT)).toBe("По умолчанию");
  });

  test("кто и когда изменил", () => {
    const changed = {
      ...DEFAULT,
      period_days: 7,
      updated_at: "2026-10-29T09:00:00Z",
      updated_by: "00000000-0000-0000-0000-000000000001",
      updated_by_name: "Ведущий врач",
    };

    expect(changedText(changed)).toBe("Изменено: Ведущий врач, 29 окт");
  });
});

describe("commonPeriod", () => {
  const plan = (period_days: number) => ({
    scale_id: "x",
    period_days,
    grace_days: 3,
    planned: [],
  });

  test("общий период всех шкал", () => {
    expect(commonPeriod([plan(14), plan(14)])).toBe(14);
  });

  test("у шкал разные периоды — общего нет", () => {
    expect(commonPeriod([plan(7), plan(14)])).toBeUndefined();
  });

  test("без шкал — общего нет", () => {
    expect(commonPeriod([])).toBeUndefined();
  });
});
