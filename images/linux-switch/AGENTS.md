# Linux switch image component

Work only in this component unless explicitly asked to integrate other services.
The guest API contract is docs/CONTRACTS.md; external integration is described in
docs/INTEGRATION.md. Do not change Slot, Manager or Plugin contracts implicitly.
Keep the guest offline: QGA -> switchctl -> root-only Unix socket. No HTTP port.
Python runtime has no third-party dependencies. Capture is opt-in, bounded and
RAM-only; never accept shell commands or arbitrary capture filters from API input.
Run tests/ with pytest and compile switch_api/. Do not run privileged networking
commands or the image preparation steps on the development host.
