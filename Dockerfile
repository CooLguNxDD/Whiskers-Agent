# Use official Python runtime as base image
FROM python:3.11-slim

# Set working directory in container
WORKDIR /app

# Optional: install CLI agents (npm global / flat binaries) for LLM_PROVIDER / GoapAgent CLI.
# Build with --build-arg INSTALL_<AGENT>=0 to skip (faster CI images).
ARG INSTALL_CLAUDE_CODE=1
ARG INSTALL_AGY=1
ARG INSTALL_GROK=1

# System deps + Node.js 22 (required by @anthropic-ai/claude-code) + optional CLI agents
# + cairo/pango (required by the cairosvg Python package -- Phase 6c SVG->PNG
# rasterization for baked portfolio visual assets; ~2 libs vs Playwright/
# chromium's ~20+hundreds of MB, since the only thing rasterized is
# svg_render.py's own deterministic markup, never arbitrary web content).
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    ca-certificates \
    gnupg \
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
        | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_22.x nodistro main" \
        > /etc/apt/sources.list.d/nodesource.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends nodejs \
    && if [ "$INSTALL_CLAUDE_CODE" = "1" ]; then \
         npm install -g @anthropic-ai/claude-code \
         && (claude --version || true); \
       fi \
    && if [ "$INSTALL_AGY" = "1" ]; then \
         curl -fsSL https://antigravity.google/cli/install.sh | bash -s -- --dir /usr/local/bin \
         && (agy --version || true); \
       fi \
    && if [ "$INSTALL_GROK" = "1" ]; then \
         GROK_BIN_DIR=/usr/local/bin curl -fsSL https://x.ai/cli/install.sh | bash \
         # Installer leaves /usr/local/bin/grok → /root/.grok/downloads/... which
         # whiskers-claude cannot traverse (/root is mode 700). Materialize real
         # world-executable binaries so privilege-dropped CLI spawns work.
         && for name in grok agent; do \
              if [ -e "/usr/local/bin/$name" ]; then \
                real="$(readlink -f "/usr/local/bin/$name")" \
                && cp -f "$real" "/usr/local/bin/${name}.bin" \
                && mv -f "/usr/local/bin/${name}.bin" "/usr/local/bin/$name" \
                && chmod a+rx "/usr/local/bin/$name"; \
              fi; \
            done \
         && (grok --version || true); \
       fi \
    && rm -rf /var/lib/apt/lists/* /root/.npm

# Dedicated non-root identity for headless Claude/agy/grok CLI subprocesses.
# The MCP server still runs as root (Docker volume/socket needs), but GoapAgent
# drops CLI children to this user so --dangerously-skip-permissions is allowed.
RUN groupadd --system whiskers-claude \
    && useradd --system --gid whiskers-claude --create-home \
         --home-dir /home/whiskers-claude --shell /usr/sbin/nologin \
         whiskers-claude \
    && mkdir -p /home/whiskers-claude/.claude /home/whiskers-claude/.grok \
    && chown -R whiskers-claude:whiskers-claude /home/whiskers-claude

# Copy requirements first for better layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the entire application
COPY . .

# CLI user must be able to read package instructions + write under logs when needed
RUN mkdir -p /app/logs/goap_agent \
    && chown -R whiskers-claude:whiskers-claude /app/logs \
    && chmod -R a+rX /app/core_graph/goap_agent/instructions || true

# Set Python to unbuffered mode for real-time logging
ENV PYTHONUNBUFFERED=1

# Claude Code/agy/grok: headless/container defaults (auth via CLAUDE_CODE_OAUTH_TOKEN / ANTHROPIC_API_KEY / GROK_AUTH_JSON)
ENV DISABLE_AUTOUPDATER=1
ENV CLAUDE_CLI_BINARY=claude
ENV AGY_CLI_BINARY=agy
ENV GROK_CLI_BINARY=grok
# Privilege-drop target for GoapAgent_run_cli_agent / claude-cli LLM (non-root)
ENV CLI_AGENT_USER=whiskers-claude
ENV CLI_AGENT_DROP_PRIVS=1

# Default HTTP port
ENV MCP_PORT=10000

# Expose the port (defaults to 10000, can be overridden)
EXPOSE ${MCP_PORT}

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:${MCP_PORT}/mcp', timeout=5)" || exit 1

# Default command: run in HTTP mode with port from environment
CMD python whiskers_agent_mcp.py --transport http --host 0.0.0.0 --port ${MCP_PORT}
