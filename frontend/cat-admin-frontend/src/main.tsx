import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { RouterProvider, createRouter } from "@tanstack/react-router"
import { QueryClientProvider } from "@tanstack/react-query"
import { ThemeProvider } from "./components/ThemeProvider"
import { ErrorBoundary } from "./components/ErrorBoundary"
import { RouteErrorFallback } from "./components/RouteErrorFallback"
import { routeTree } from "./routeTree.gen"
import { queryClient } from "./queryClient"
import "./index.css"

const router = createRouter({
  routeTree,
  defaultErrorComponent: RouteErrorFallback,
})

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router
  }
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <ErrorBoundary>
          <RouterProvider router={router} />
        </ErrorBoundary>
      </QueryClientProvider>
    </ThemeProvider>
  </StrictMode>,
)


