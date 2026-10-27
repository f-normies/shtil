import { fetchClient, installSessionExpiryHandler, shouldRetryApiQuery } from "@compass/api-client";
import { lockMessagingAccess } from "@compass/ui";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRouter, RouterProvider } from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { routeTree } from "./routeTree.gen";
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, retry: shouldRetryApiQuery } },
});

const router = createRouter({ routeTree, basepath: "/doctor", context: { queryClient } });

installSessionExpiryHandler(fetchClient, {
  queryClient,
  isLoginPage: () => router.state.location.pathname === "/login",
  onExpired: () => {
    lockMessagingAccess();
    return router.navigate({ to: "/login", replace: true });
  },
});

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
        <RouterProvider router={router} />
      </QueryClientProvider>
    </StrictMode>,
  );
}
