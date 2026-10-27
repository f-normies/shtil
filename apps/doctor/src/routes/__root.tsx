import type { QueryClient } from "@tanstack/react-query";
import { createRootRouteWithContext, Outlet } from "@tanstack/react-router";
import { LoadFailed, NotFound } from "../components/window/RouteFallbacks";

export const Route = createRootRouteWithContext<{ queryClient: QueryClient }>()({
  component: () => (
    <div className="min-h-dvh font-sans">
      <Outlet />
    </div>
  ),
  errorComponent: LoadFailed,
  notFoundComponent: NotFound,
});
