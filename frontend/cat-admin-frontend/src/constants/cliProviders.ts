/**
 * LLM pool providers that run as a headless CLI subprocess on the server
 * (claude-cli / agy-cli) rather than a cloud API.
 */

/**
 * @deprecated CLI provider detection is now dynamic — fetched from GET /api/config/cli-agents.
 * This module is kept as an empty shim. Import `useCliAgentsQuery` from `@/hooks/useConfig` instead.
 */
export {}
