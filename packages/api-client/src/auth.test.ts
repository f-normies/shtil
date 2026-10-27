import createFetchClient from "openapi-fetch";
import { describe, expect, it } from "vitest";
import { fetchCurrentUser, homePathFor } from "./auth";
import type { paths } from "./schema";

function clientReturning(status: number, body: unknown) {
  return createFetchClient<paths>({
    baseUrl: "http://test",
    fetch: async () =>
      new Response(JSON.stringify(body), {
        status,
        headers: { "content-type": "application/json" },
      }),
  });
}

describe("homePathFor", () => {
  it.each([
    ["doctor", "/doctor/"],
    ["admin", "/doctor/admin"],
    ["patient", "/patient/"],
  ] as const)("%s открывает %s", (role, path) => {
    expect(homePathFor(role)).toBe(path);
  });
});

describe("fetchCurrentUser", () => {
  it("возвращает пользователя при активной сессии", async () => {
    const user = {
      id: "0199f1e2-0000-7000-8000-000000000001",
      email: "doctor@example.com",
      role: "doctor",
      full_name: "Иванов Иван",
    };

    await expect(fetchCurrentUser(clientReturning(200, user))).resolves.toEqual(user);
  });

  it("возвращает null без сессии (401)", async () => {
    const problem = { type: "about:blank", title: "Unauthorized", status: 401 };

    await expect(fetchCurrentUser(clientReturning(401, problem))).resolves.toBeNull();
  });

  it("бросает ошибку при сбое сервера, а не притворяется разлогиненным", async () => {
    const problem = { type: "about:blank", title: "Internal Server Error", status: 500 };

    await expect(fetchCurrentUser(clientReturning(500, problem))).rejects.toThrow("500");
  });
});
