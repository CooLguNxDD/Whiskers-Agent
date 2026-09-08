# JSDoc Documentation Skill

This skill defines the standards and patterns for JSDoc documentation across the project, particularly for the React-based frontend.

## Documentation Standards

### 1. Components
Every React component must have a top-level JSDoc block (1-3 lines) describing its purpose and key features.

```typescript
/**
 * AuthPill Component
 *
 * Displays the current authentication status of the MCP server connection.
 * Shows different states (disconnected, authorizing, connected) with appropriate
 * colors and animations.
 */
const AuthPill: FC<AuthPillProps> = ({ className }) => { ... }
```

### 2. Hooks and Store Slices
All hooks and Zustand store slices should include a top-level JSDoc block.

```typescript
/**
 * usePlugins Hooks
 *
 * A collection of hooks for interacting with the plugins API.
 * Provides functionality for fetching plugins and toggling their states.
 */
export function usePluginsQuery() { ... }
```

### 3. Functions
All exported functions and significant internal functions must have a short (1-2 lines) description. Input/output parameters are optional if the types are self-documenting.

```typescript
/**
 * Fetches health and operational metrics for a specific plugin by ID.
 */
export function usePluginHealthQuery(id: string) { ... }
```

### 4. Inline Comments
Use single-line comments for complex logic or business rules within functions.

```typescript
// Optimistic update: update local cache before API response
qc.setQueryData(PLUGINS_KEY, (old) => { ... })
```

## Alignment
This standard aligns with the `create-react-base` branch requirements for improved codebase maintainability and onboarding.
