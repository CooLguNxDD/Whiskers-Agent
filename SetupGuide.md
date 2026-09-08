# Whiskers Agent MCP Server Setup & TUI Guide

This guide describes how to configure, bootstrap, and run the Whiskers Agent MCP Server environment and its interactive Terminal User Interfaces (TUIs).

---

## 📋 Table of Contents

1. [Prerequisites](#-prerequisites)
2. [Interactive TUIs Overview](#-interactive-tuis-overview)
3. [Running via Docker (Recommended)](#-running-via-docker-recommended)
4. [Restarting the Server from the TUI](#-restarting-the-server-from-the-tui)
5. [Running on Windows Host](#-running-on-windows-host)
6. [Key Environment Variables](#-key-environment-variables)
7. [Additional Resources](#-additional-resources)

---

## ⚙️ Prerequisites

Before starting, ensure you have the following installed on your system:

- **Docker & Docker Compose** (Highly recommended for database and container services)
- **Python 3.11+** (If running locally on the Windows host)
- **Git**

---

## 🖥️ Setup and Management Scripts Overview

The project provides interactive TUIs (implementations in `terminal/tui/`), command-line helper scripts inside `terminal/script/`, and a unified setup CLI:

### 1. Unified Setup CLI & Windows Wrapper

- **Script:** [setup.py](./terminal/script/setup.py)
- **Windows Wrapper:** [setup.bat](./terminal/setup.bat)
- **Description:** A multi-command CLI to run doctor checks, scaffold configurations, migrate databases, set the admin account (first-time only), and start Docker services. Full `all` ends by launching the master setup TUI (plugin credentials are configured there, not in the CLI). Cold machines should use `./install.sh --yes` (or `.\install.ps1 -Yes`) so dependencies are installed first — `setup.py` itself needs `python-dotenv` and the Docker SDK.
- **Commands:**
  - `./install.sh --yes` / `.\install.ps1 -Yes` — uv-first venv + `requirements.txt` + `setup.py all -y`
  - `python terminal/script/setup.py doctor` (or `terminal\setup.bat doctor`) — Run preflight environment checks.
  - `python terminal/script/setup.py env` — Scaffold `.env` (real `MASTER_KEY`) and configuration files. Do not `cp .env_sample .env`.
  - `python terminal/script/setup.py llm --provider openai` — Write `LLM_PROVIDER` and the matching API key into `.env`.
  - `python terminal/script/setup.py db` — Bring up Postgres / Docker DB services.
  - `python terminal/script/setup.py migrate` — Run Alembic migrations.
  - `python terminal/script/setup.py plugin_migrate` — Apply plugin-owned DDL (`scripts/apply_plugin_migrations.py`).
  - `python terminal/script/setup.py admin` — Set admin password (generates one under `-y` if `ADMIN_PASSWORD` is unset).
  - `python terminal/script/setup.py plugins` — Keep the existing `config/plugin_config.json` boot list as-is (write recommended defaults only if the file is missing); use `--reset-plugins` to overwrite with recommended defaults.
  - `python terminal/script/setup.py up` — Start the Docker services (`docker compose up -d`).
  - `python terminal/script/setup.py health` — Run post-startup health checks.
  - `python terminal/script/setup.py tui` — Launch master setup TUI (credentials, plugins, live config). Skipped under `-y`.
  - `python terminal/script/setup.py all` — Ordered pipeline (`doctor → env → llm → db → migrate → plugin_migrate → admin → plugins → up → health → tui`). Resumes from `.whiskers/setup-state.json` (or legacy `.whiskers/setup-state.json`) unless `--force`.

### 2. Master Setup TUI

- **TUI Source:** [setup_tui.py](./terminal/tui/setup_tui.py)
- **Entry Script:** [setup_tui.py](./terminal/script/setup_tui.py)
- **Description:** A guided Text User Interface for setup, config editing, plugin boot-list management, vault/plugin credentials, and database settings.
- **Usage / Shortcuts:**
  ```powershell
  python terminal/script/setup_tui.py
  # OR
  python -m terminal
  ```

### 3. Plugin Credential Manager TUI

- **TUI Source:** [credentials.py](./terminal/tui/credentials.py)
- **Entry Script:** [manage_credentials.py](./terminal/script/manage_credentials.py)
- **Description:** An interactive picker to securely set, view, update, and delete vault-encrypted keys (e.g. Tavily API keys, Brave Search API keys, or custom credentials) stored in the database.
- **Usage / Shortcuts:**
  ```powershell
  python terminal/script/manage_credentials.py
  # OR
  python -m terminal.tui.credentials
  ```

### 4. Admin Password Bootstrapper (CLI)

- **Script:** [set_admin_password.py](./terminal/script/set_admin_password.py)
- **Description:** Bootstrap or update the admin user credentials (username and password) in the Vault. Requires inputting the username (optional, defaults to `admin`) and confirming the password securely. Can also be set non-interactively using the `ADMIN_USERNAME` and `ADMIN_PASSWORD` environment variables.
- **Usage:**
  ```powershell
  python terminal/script/set_admin_password.py
  ```

### 5. One-Shot Credential Helper (CLI)

- **Script:** [add_credentials.py](./terminal/script/add_credentials.py)
- **Description:** A one-shot CLI helper to write or update a specific plugin credential directly in the Vault.
- **Usage:**
  ```powershell
  python terminal/script/add_credentials.py -p <plugin_id> --key KEY --value VAL
  ```

---

## 🐳 Running via Docker (Recommended)

Running the TUIs inside the Docker container is the simplest path. The container environment automatically maps the correct `PYTHONPATH`, database links (`postgres` network host), and secret `MASTER_KEY`.

### Step 1: Start the Docker services

Ensure your `.env` file is configured, then start the stack:

```powershell
docker compose up -d
```

### Step 2: Run the Master Setup TUI

Launch the guided setup menu interactively inside the running container:

```powershell
docker exec -it whiskers-agent-server python /app/terminal/script/setup_tui.py
# OR
docker exec -it whiskers-agent-server python -m terminal
```

### Step 3: Run the Credential Manager TUI

Manage vault-encrypted API keys interactively inside the running container:

```powershell
docker exec -it whiskers-agent-server python /app/terminal/script/manage_credentials.py
# OR
docker exec -it whiskers-agent-server python -m terminal.tui.credentials
```

---

## 🔁 Restarting the Server from the TUI

The Master Setup TUI's **Restart server** screen (`9`) restarts the `whiskers-agent-server`
container without dropping to a host shell. It works whether the TUI is running inside the
container (`docker exec`) or on the host, via the Docker SDK (`docker` Python package) talking
to `/var/run/docker.sock`.

> ⚠️ **Security note:** `docker-compose.yml` mounts the host's `/var/run/docker.sock` into
> `whiskers-agent-server`, which grants that container full control of the host Docker daemon
> (container-escape-equivalent privilege). This is an intentional trade-off for a
> single-operator/dev setup. This feature is strictly designed for **development or single-operator
> environments only** and is **not safe** for shared or production deployments.

**One-time setup:** the socket mount and `docker` SDK dependency require an image rebuild:

```powershell
docker compose up -d --build whiskers-agent
```

**Usage:**

```powershell
docker exec -it whiskers-agent-server python /app/terminal/script/setup_tui.py
# OR
docker exec -it whiskers-agent-server python -m terminal
# → select "9. Restart server", confirm
```

If the TUI is running inside the container being restarted, that `docker exec` session ends
when the restart happens — the server itself comes back up automatically. The same action is
also available non-interactively: `python terminal/script/setup.py restart` (or `/app/terminal/script/setup.py restart` inside Docker).

---

## 💻 Running on Windows Host

If you prefer to run the setup and credential management tools natively on your Windows machine, follow these steps to configure your Python environment and direct database access.

### Step 1: Install Dependencies

Create a virtual environment and install the required libraries:

```powershell
# One-click (preferred)
.\install.ps1 -Yes

# Or manual venv
cd /path/to/your/repo
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python terminal/script/setup.py env
```

### Step 2: Set Host Environment Variables

When running on the Windows host, you must point `DATABASE_URL` to `localhost` instead of the Docker-internal hostname `postgres`:

```powershell
$env:DATABASE_URL = "postgresql+psycopg://whiskers:whiskers@localhost:5432/whiskers_agent_mcp"
$env:MASTER_KEY = "your-32-byte-base64-encryption-key" # Same value as defined in your .env
```

### Step 3: Launch the TUIs

- **Run Master Setup TUI:**

  ```powershell
  python terminal/script/setup_tui.py
  # OR
  python -m terminal
  ```

- **Run Credential Manager TUI:**
  ```powershell
  python terminal/script/manage_credentials.py
  # OR
  python -m terminal.tui.credentials
  ```

---

## 🔑 Key Environment Variables

Below are the key variables needed in your local shell or `.env` configuration:

| Variable         | Required | Description                                                                                             |
| :--------------- | :------- | :------------------------------------------------------------------------------------------------------ |
| `DATABASE_URL`   | Yes      | Database connection string (e.g., `postgresql+psycopg://whiskers:whiskers@localhost:5432/whiskers_agent_mcp`) |
| `MASTER_KEY`     | Yes (DB) | 32-byte base64 key used for vault encryption/decryption                                                 |
| `LLM_PROVIDER`   | No       | LLM service provider for workflows (`openai`, `anthropic`, `gemini`, `gemini-vertex`, `claude-cli`, `agy-cli`) |
| `OPENAI_API_KEY` | No       | API key for OpenAI (required if `LLM_PROVIDER=openai`)                                                  |
| `GOOGLE_API_KEY` | No       | API key for Gemini (required if `LLM_PROVIDER=gemini`)                                                  |
| `CLAUDE_CLI_BINARY` / `AGY_CLI_BINARY` | No | Binary names for `claude-cli` / `agy-cli` providers (must be on server PATH) |
| `CLAUDE_CODE_OAUTH_TOKEN` | No | Long-lived Claude Code OAuth token for headless/Docker (`claude setup-token` on a trusted host). Preferred auth for `LLM_PROVIDER=claude-cli` inside the container. |

---

## 🏢 Multi-Tenant Setup via TUI

Whiskers Agent MCP Server supports multi-tenancy. You can configure tenants, master users, and secondary users directly via the setup TUI.

### 1. Access the Tenant Management Screen

Run the Master Setup TUI and select `8. Tenant management`:

```powershell
docker exec -it whiskers-agent-server python /app/terminal/script/setup_tui.py
# Or if running locally: python -m terminal
```

### 2. Create a New Tenant

- From the Tenant management menu, press `c` to create a new tenant.
- Provide a **Tenant name** and **Slug**.
- Provide credentials for the **Master username** that will govern this tenant.

### 3. Manage Users

- Select an existing tenant from the list (e.g., `1`, `2`, etc.).
- Press `a` to add a new user to the selected tenant.
- Provide a username, password, and select a role (e.g., `admin`, `user`).

For more details on the multi-tenant architecture and CLI tools, refer to [MULTI_TENANT_TUI.md](./docs/MULTI_TENANT_TUI.md).

---

## 📚 Additional Resources

- Dev Rules and Project Index: [CLAUDE.md](./CLAUDE.md)
- Main Project Readme (onboarding + project structure): [ReadMe.md](./ReadMe.md)
- Living architecture context: [.claude/docs/context.md](./.claude/docs/context.md)
