import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { tanstackRouter } from '@tanstack/router-plugin/vite'
import path from "path"

export default defineConfig(({ mode }) => {
  // Load env from the project root (2 levels up)
  const envDir = path.resolve(import.meta.dirname, '../../');
  const env = loadEnv(mode, envDir, '');
  const mcpServerUrl = env.MCP_SERVER_URL || process.env.MCP_SERVER_URL || `http://localhost:${env.MCP_PORT || process.env.MCP_PORT || 10000}`;

  // Preserve the browser Host (e.g. localhost:3000) when proxying. changeOrigin
  // defaults to true and rewrites Host to the Docker service name (whiskers-agent),
  // which FastMCP's HostOriginGuardMiddleware rejects with 421 on /api/* routes.
  // Production nginx uses proxy_set_header Host $host — match that here.
  const mcpProxy = { target: mcpServerUrl, changeOrigin: false };

  return {
    clearScreen: false,
    plugins: [
      // Ensure it is called before react()
      tanstackRouter({
        target: 'react',
        autoCodeSplitting: true,
        routeFileIgnorePattern: '.+\\.test\\.(ts|tsx)$',
      }),
      react(),
      tailwindcss(),
    ],
    resolve: {
      alias: {
        "@": path.resolve(import.meta.dirname, "./src"),
      },
    },
    server: {
      proxy: {
        "/api/": mcpProxy,
        "/oauth/": mcpProxy,
        "/mcp": mcpProxy,
      },
      port: parseInt(env.FRONTEND_PORT || '3000'),
      hmr: {
        clientPort: 3000,
      },
    },
    build: {
      outDir: "../console/dist",
    },
  };
})