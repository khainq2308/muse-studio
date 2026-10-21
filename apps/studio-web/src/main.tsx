import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { StrictMode } from "react";
import ReactDOM from "react-dom/client";
import { router } from "@/app/router";
import { shouldRetryApiRequest } from "@/lib/api/errors";
import { useSessionStore } from "@/lib/stores/session-store";
import "./styles/globals.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      refetchOnWindowFocus: true,
      retry: shouldRetryApiRequest,
    },
  },
});

// Only authenticated resource data is session-scoped. Keep health/status queries intact.
useSessionStore.subscribe((state, previousState) => {
  if (state.sessionVersion !== previousState.sessionVersion) {
    queryClient.removeQueries({ queryKey: ["projects"] });
    queryClient.removeQueries({ queryKey: ["connections"] });
  }
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
