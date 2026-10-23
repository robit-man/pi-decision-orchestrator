# Implementation register

Status meanings: planned, implementing, verified, unsupported. A capability is verified only when its API, persistence, browser interaction, and relevant automated tests pass.

| ID | Capability | Status | Dependencies | Verification evidence |
|---|---|---|---|---|
| P0 | Repository, product and design contracts, reproducible builds | implementing | none | PRODUCT.md; GitHub identity verified as robit-man |
| P1 | SQLite migrations, native service, authentication, telemetry | planned | P0 | |
| P2 | System One, chat/tool and remote adapters; resource scheduler | planned | P1 | |
| P3 | Model catalog, lifecycle, compatibility, real Pi benchmarks | planned | P2 | Ollama 0.40.8 running; baseline qwen3:0.6b smoke test on Pi |
| P4 | Decision CRUD, versions, form editor, graph editor, validation | planned | P1,P2 | |
| P5 | Central execution, policies, action authorization, cancellation | planned | P2,P4 | |
| P6 | HTTP, webhook, polling, MQTT, encrypted credentials | planned | P1,P5 | |
| P7 | Live tests, datasets, comparisons, calibration, history | planned | P3,P5 | |
| P8 | Responsive dashboard, onboarding, accessibility, all eight flows | planned | P3-P7 | |
| P9 | Installer, update, backup, restore, uninstall, recovery | planned | P1-P8 | |
| P10 | Security, reliability, browser, real hardware and reboot checks | planned | P1-P9 | |
| P11 | Public GitHub publication from laptop | planned | P10 | |

## Hardware facts

- Raspberry Pi 4 Model B Rev 1.4, 7.6 GiB RAM, aarch64 kernel 6.1.21-v8+.
- Raspbian Bullseye armhf userspace, Python 3.9.2 initially; application requires a separately provisioned Python 3.11+ runtime.
- Ollama 0.40.8 API at 127.0.0.1:11434, ARM64 container, CPU only, inference parallelism 1.
- Compatibility claims and performance measurements must come from actual target execution.
