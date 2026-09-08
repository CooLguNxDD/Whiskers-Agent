# Cat Admin Frontend

The Cat Admin Frontend is a Single Page Application (SPA) dashboard for interacting with the Whiskers Agent Server. It provides an administrative interface for managing plugins, reviewing tools and configurations, and viewing real-time interactions with the multi-agent system.

## Tech Stack
This project leverages a modern React ecosystem:
- **Framework:** [React 18](https://react.dev/) with [Vite](https://vitejs.dev/)
- **Routing:** [TanStack Router](https://tanstack.com/router/latest)
- **State Management:** [Zustand](https://zustand-demo.pmnd.rs/) (Client State) and [TanStack Query v5](https://tanstack.com/query/latest) (Server State)
- **Styling:** [Tailwind CSS](https://tailwindcss.com/) with [shadcn/ui](https://ui.shadcn.com/) components
- **Animations:** [Framer Motion](https://www.framer.com/motion/)

## Prerequisites
- Node.js v20 (or higher recommended)
- [pnpm](https://pnpm.io/) package manager (`npm i -g pnpm`)

## Setup & Installation

1. Navigate to the frontend directory:
   ```bash
   cd frontend/cat-admin-frontend
   ```

2. Install the dependencies:
   ```bash
   pnpm install
   ```

3. Ensure the environment variables match your backend setup. When running via Docker, these are automatically configured. The key variables used by the frontend are:
   - `VITE_API_BASE_URL`: Base URL for backend API calls (e.g., `http://localhost:10000`).
   - `MCP_SERVER_URL`: The URL of the MCP server, used in OAuth flows.

## Development Server

To start the Vite development server with Hot Module Replacement (HMR):
```bash
pnpm dev
```
By default, the server will be available at `http://localhost:3000`.

*Note on Docker:* If you are using the full Docker dev environment (`docker-compose.yml` + `docker-compose.dev.yml`), the frontend will automatically build and start the dev server, and HMR is correctly configured for the container.

## Linting & Testing

The project uses ESLint for static code analysis and Vitest for unit testing.

- **Run Linters:**
  ```bash
  pnpm lint
  ```

- **Run Tests:**
  ```bash
  pnpm test
  ```

## Building for Production

To create a production build of the frontend:
```bash
pnpm build
```

This will output the compiled assets to the `dist` directory. The production Dockerfile uses this command and serves the built files via Nginx.