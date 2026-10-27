import { fetchClient, installSessionExpiryHandler, shouldRetryApiQuery } from "@compass/api-client";
import { lockMessagingAccess } from "@compass/ui";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRouter, RouterProvider } from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { PwaUpdateNotice } from "./assessments/PwaUpdateNotice";
import { lockLocalAccessOnExpiry } from "./assessments/session-expiry";
import { routeTree } from "./routeTree.gen";
import { getAssessmentStore } from "./session";
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, retry: shouldRetryApiQuery } },
});

const router = createRouter({ routeTree, basepath: "/patient", context: { queryClient } });

installSessionExpiryHandler(fetchClient, {
  queryClient,
  isLoginPage: () => router.state.location.pathname === "/login",
  async onExpired() {
    await lockLocalAccessOnExpiry(getAssessmentStore, shownPatientId());
    await lockMessagingAccess();
    await router.navigate({ to: "/login", replace: true });
  },
});

/** The patient whose data the current page shows, as confirmed by its route guard. */
function shownPatientId(): string | null {
  for (const match of router.state.matches) {
    if (!("session" in match.context)) continue;
    const { session } = match.context;
    if (session.status === "online") return session.user.id;
    if (session.status === "offline") return session.ownerId;
  }
  return null;
}

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <PwaUpdateNotice />
        <RouterProvider router={router} />
      </QueryClientProvider>
    </StrictMode>,
  );
}
