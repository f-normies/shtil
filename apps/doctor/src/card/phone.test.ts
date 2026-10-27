import { expect, test } from "vitest";
import { formatPhone } from "./phone";

test.each([
  ["+79000000001", "+7 900 000-00-01"],
  ["+4915112345678", "+4915112345678"],
])("formats %s as %s", (raw, shown) => {
  expect(formatPhone(raw)).toBe(shown);
});
