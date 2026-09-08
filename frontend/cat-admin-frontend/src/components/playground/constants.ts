/**
 * Static data constants for the Playground: server profiles.
 * The tool registry is fetched live from the server — see useToolRegistry.ts.
 */

import type { PGServer } from "./types"

/** Saved MCP server connection profiles shown in the ServerBar. */
export const PG_SERVERS: PGServer[] = [
  {
    id: "local",
    // Label is overridden at runtime from GET /api/health instance_name (see PgStrip).
    label: "local",
    url: "http://localhost:7331/mcp",
    transport: "sse",
    latency: "12ms",
    status: "ok",
  },
  {
    id: "remote",
    label: "remote · streamable",
    url: "https://mcp.whiskers.agent/v1",
    transport: "streamable",
    latency: "47ms",
    status: "ok",
  },
  {
    id: "claude",
    label: "anthropic · demo",
    url: "https://anthropic.com/mcp/demo",
    transport: "sse",
    latency: "188ms",
    status: "warn",
  },
  {
    id: "staging",
    label: "whiskers · staging",
    url: "https://staging.whiskers.agent/mcp",
    transport: "stdio",
    latency: "—",
    status: "off",
  },
]
