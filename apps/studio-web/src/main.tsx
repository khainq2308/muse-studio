import { StrictMode } from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import "@/lib/i18n";
import { router } from "@/app/router";
import { apiRetryDelay, shouldRetryApiRequest } from "@/lib/api/errors";
import { registerSessionCacheReset } from "@/lib/api/session-cache";
import { useSessionStore } from "@/lib/stores/session-store";
import "./styles/globals.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      refetchOnWindowFocus: true,
      retry: shouldRetryApiRequest,
      retryDelay: apiRetryDelay,
    },
  },
});

// Only authenticated resource data is session-scoped. Keep health/status queries intact.
registerSessionCacheReset(queryClient, useSessionStore);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
