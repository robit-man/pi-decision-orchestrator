# Pi Decision Orchestrator

A local-first decision workbench for the Raspberry Pi. You define a decision once — the question, the allowed outcomes, the input fields — and a small local model answers it with a full probability distribution. A policy then accepts the answer or abstains, and accepted answers can trigger the actions you've authorized (HTTP, webhook, MQTT, chained decisions).

Everything runs on the Pi: FastAPI + SQLite for the backend, a Preact dashboard served from the same process, and Ollama on localhost for inference. No cloud service, Docker or external database is required.

## Quick start

On a Raspberry Pi 4/5 running a **64-bit** OS (Raspberry Pi OS Bookworm 64-bit recommended):

```bash
curl -fsSL https://raw.githubusercontent.com/robit-man/pi-decision-orchestrator/main/bootstrap.sh | sudo bash
```

That one command:

1. Installs `git`, `curl` and `python3-venv` if they're missing.
2. Installs and starts Ollama if nothing is answering on `127.0.0.1:11434`.
3. Clones this repo to `/opt/pi-decision-orchestrator/src`, or pulls the latest `main` if it's already there.
4. Backs up existing data (on updates), then installs and starts the `pi-decision` systemd service.

When it finishes it prints the dashboard URL. Open `http://<pi-address>:8765` and sign in with:

```bash
sudo cat /etc/pi-decision/initial-admin.txt
```

**To update later, run the same command again.** It pulls, backs up, and redeploys.

No models are downloaded automatically. Install one from the **Models** page (start with `laya`, ~840 MB).

<details>
<summary>Install from a manual clone instead</summary>

```bash
git clone https://github.com/robit-man/pi-decision-orchestrator.git
cd pi-decision-orchestrator
sudo ./install.sh
```

`install.sh` expects Ollama 0.40+ to be running already.
</details>

<details>
<summary>Bootstrap options</summary>

Set these before `sudo bash` with `sudo env VAR=value bash`:

| Variable | Default | Purpose |
|---|---|---|
| `PI_BRANCH` | `main` | Branch to deploy |
| `PI_REPO_URL` | this repo | Deploy a fork |
| `PI_SOURCE_DIR` | `/opt/pi-decision-orchestrator/src` | Where the checkout lives |
| `PI_SKIP_OLLAMA` | `0` | `1` = fail instead of installing Ollama |

</details>

## Requirements

- 64-bit ARM kernel **and** 64-bit userspace (`uname -m` → `aarch64`)
- Python 3.11+ (ships with Bookworm)
- 2 GB RAM minimum, 8 GB recommended
- 1 GB free for the app, plus room for models (300–850 MB each)
- Ollama 0.40 or newer

On x86 or other hardware, set `PI_ALLOW_NON_ARM=1` to skip the architecture check (not a supported target).

## How it works

```
 input ──► transforms ──► conditions ──► model inference ──► policy gate ──► actions
 (form, API,                             (one at a time,     accept or       (only if authorized,
  webhook, MQTT,                          bounded queue)     abstain         never in dry runs)
  HTTP poll, timer)
```

### Decisions

A decision is a typed workflow. Each one asks the model one or more **questions**:

| Type | Answer | Example |
|---|---|---|
| `choice` | one of 2–26 named outcomes | route a ticket to `billing` / `technical` |
| `noul` | true / false | "Is this an urgent outage?" |
| `score` | one of 2–10 ordered levels | severity: minor → partial → complete |

You build decisions in the **Builder**, either with forms or with the visual graph. Both edit the same definition. Graph nodes include input, transform, condition, inference, evaluate, gate, branch, action, delay, log and error.

Drafts stay editable and can be tested at any time. **Publishing** creates an immutable, numbered version, and only published versions can run in production.

### Policies: deciding when to trust the model

Every answer passes through a policy before anything happens:

- **Minimum probability** for the selected outcome (default 0.7)
- **Minimum margin** between the top two outcomes (default 0.1)
- **Require native probabilities**. Chat-style models that can't report them are rejected.
- **Deterministic conditions** on the input or context
- **Model agreement**: several inference nodes must agree
- **Measured accuracy** on a labeled dataset must meet a threshold

If any check fails, the decision **abstains** (or uses the fallback you set). Probabilities are what the model reported, not a calibrated guarantee. To measure real accuracy, use **Live Testing** with a labeled dataset.

### Models

Inference goes through Ollama on localhost with a concurrency of one. Jobs wait in a bounded queue and can be cancelled. They're refused when free memory or CPU temperature crosses a safety limit. The built-in catalog:

| Model | Size | Adapter | Notes |
|---|---|---|---|
| `laya` | 844 MB | decision | Compact typed decisions; good default |
| `tev1:0.8b` | 834 MB | decision | General decision routing |
| `d1-omni` | 850 MB | decision | Also takes images and short English audio |
| `functiongemma` | 301 MB | tools | Tool selection; no native probabilities |
| `qwen3:0.6b` | 523 MB | chat | Structured chat fallback; no native probabilities |

Any other Ollama model can be added and used through the chat adapter. On the Models page you can pull, verify, warm up, unload, benchmark and set the default model.

### Connections

Connections feed inputs into decisions and receive their actions:

- **HTTP**: call an endpoint, or poll one on an interval
- **Webhooks**: authenticated inbound `POST /api/v1/hooks/{id}`
- **MQTT**: subscribe and publish, with TLS
- **Remote providers**: optional off-device model APIs

Credentials are encrypted with a server-side key (`/etc/pi-decision/secrets.key`). They never appear in API responses, logs or exports. Outbound actions only go to allowlisted destinations, each action has to be explicitly authorized, and all actions are turned off during dry runs.

### Dashboard

There are eight sections, all using the same versioned API (`/api/v1`):

| Section | What it's for |
|---|---|
| Overview | Health, queue, latency, CPU temperature and memory, streamed live |
| Decisions | List, enable, publish, duplicate, import and export |
| Builder | Form and graph editor, validation, versions |
| Connections | Inputs and outputs, connection tests, health |
| Models | Catalog, downloads, verification, benchmarks |
| Live Testing | Try inputs, inspect distributions, evaluate datasets |
| History | Every execution with inputs, answers, policy result and actions |
| Settings | Users and roles, API tokens, retention, backups, deployment status |

Roles are **administrator**, **operator** and **viewer**. Browser sessions use CSRF protection, and scripts authenticate with scoped API tokens.

### Calling a decision from code

Create a token in **Settings**, then:

```bash
curl -X POST http://<pi-address>:8765/api/v1/decisions/<decision-id>/execute \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"state": {"message": "Payment failed twice and the site is down"}}'
```

`POST /api/v1/decisions/<id>/test` runs a draft as a dry run, so no actions fire.

## Operations

| Task | Command |
|---|---|
| Update | re-run the quick-start one-liner, or `sudo /opt/pi-decision-orchestrator/src/update.sh` |
| Status | `systemctl status pi-decision` |
| Logs | `journalctl -u pi-decision -f` |
| Back up | `sudo ./backup.sh [dest]` → `/var/backups/pi-decision/*.tar.gz` |
| Restore | `sudo ./restore.sh /path/to/backup.tar.gz` |
| Uninstall | `sudo ./uninstall.sh` (removes the service, keeps data and models) |

Backups include the database, the configuration and the encryption key, so store them somewhere private.

| Path | Contents |
|---|---|
| `/opt/pi-decision-orchestrator/app` | Installed application (read-only to the service) |
| `/opt/pi-decision-orchestrator/src` | Git checkout used by the bootstrap |
| `/var/lib/pi-decision` | SQLite database (WAL) |
| `/etc/pi-decision/app.env` | Configuration (see `.env.example`) |
| `/etc/pi-decision/secrets.key` | Credential encryption key |

The service runs as an unprivileged `pi-decision` user, with systemd sandboxing and a 350 MB memory cap. It listens on port **8765**.

### Configuration

Edit `/etc/pi-decision/app.env`, then `sudo systemctl restart pi-decision`:

| Variable | Default | Notes |
|---|---|---|
| `PI_HOST` / `PI_PORT` | `0.0.0.0` / `8765` | Listen address |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama endpoint |
| `PI_SECURE_COOKIE` | `false` | Set `true` behind an HTTPS reverse proxy |
| `PI_ALLOWED_ORIGINS` | empty | Comma-separated origins when using a custom hostname or proxy |
| `PI_ADMIN_USER` / `PI_ADMIN_PASSWORD` | `admin` / random | Only used the first time the service starts |

## Development

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[test]'
pytest                      # backend tests
ruff check .

cd frontend
npm ci
npm run dev                 # Vite dev server
npm run build               # writes production assets to backend/static/
npm run test:e2e            # Playwright workflows
```

The built dashboard is committed in `backend/static/`, so a Pi deployment never needs Node. Rebuild and commit it whenever you change `frontend/`.

`scripts/verify_models.py` runs serial compatibility and latency checks against a real Ollama endpoint. Measured results from the reference Pi are in `artifacts/hardware/`.

## Project layout

```
backend/
  api/           REST routes (auth, decisions, executions, models, connections, datasets, system)
  decisions/     schema, execution engine, policies, validation, templates
  inference/     Ollama decision/chat/tool adapters, model manager, catalog
  connections/   HTTP, webhook, MQTT, encrypted credentials
  security/      sessions, tokens, vault, destination allowlist
  storage/       SQLite store and migrations
  monitoring/    system telemetry and resource gates
  static/        built dashboard
frontend/        Preact + Vite dashboard source
systemd/         service unit
tests/           pytest suite
*.sh             bootstrap, install, update, backup, restore, uninstall
```

Product and design contracts are in `PRODUCT.md`, `DESIGN.md` and `UX-CONTRACT.md`. Implementation status is in `TASKS.md`.
