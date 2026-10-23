# Pi Decision Orchestrator

An operational, local-first decision workbench for an 8 GB Raspberry Pi 4. Operators build typed decisions and bounded workflows, inspect full distributions, evaluate labeled fixtures, and authorize configured actions. No cloud service or external database is required.

## Product contract

The user's Implementation Work Order v1.0 is authoritative. All eight primary sections—Overview, Decisions, Builder, Connections, Models, Live Testing, History, Settings—must operate against the same versioned API and SQLite state. Forms and the visual graph edit the same workflow definition. Drafts are editable; published versions are immutable execution snapshots. Import/export excludes secrets. External actions require explicit authorization and an allowlisted configured destination, and are disabled during dry runs. Failed or uncertain decisions abstain by default. Reported probabilities describe model output, not calibrated correctness.

Operators use an authenticated LAN browser on desktop, tablet, or phone. The first successful session must support installing a model, defining outcomes and input fields without JSON, testing, inspecting probabilities, saving, and associating a connection. Advanced JSON import remains optional.

## Platform

Python 3.11+, FastAPI, standard SQLite in WAL mode, shared asynchronous HTTP client, SSE telemetry. TypeScript with Preact and Vite produces static production assets served by the backend. A native systemd application service listens on port 8765. Ollama 0.40+ remains on localhost. Inference concurrency is one, with a bounded queue, cancellation, timeouts, memory and temperature gates. MQTT is optional but implemented using paho-mqtt with TLS. Providers normalize native System One, structured chat/tool output, and optional remote APIs.

The connected test Pi has a 64-bit kernel and a 32-bit Bullseye userspace. Its existing Ollama 0.40.8 runs in an ARM64 container. The application itself must not require Docker. The compatibility runtime and all measured behavior are documented separately from the standard ARM64 installation.

## Authorization and persistence

Administrator, operator, and viewer roles; browser sessions with CSRF protection; scoped API tokens; authenticated webhooks. Connection secrets are encrypted with a server-side key, never returned in API payloads or logs. SQLite history is bounded and migrations are versioned. App data lives in /var/lib/pi-decision and configuration in /etc/pi-decision. Administrative controls include retention, backups, tokens, model defaults, and deployment status.

## Delivery

Build locally in pi-decision-orchestrator, verify locally and on the real Pi, then publish a public repository under robit-man from the laptop. Maintain TASKS.md with dependencies, test evidence, and unsupported configurations. Do not represent an unimplemented or unverified feature as complete.
