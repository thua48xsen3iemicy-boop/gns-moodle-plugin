# Lab uplink image component

Work only in this component unless explicitly asked to integrate other services.
The task is docs/lab-uplink/TASK.md (task G); the PPPoE server (BRAS) is not part of it.
Do not change Slot, Manager or Plugin contracts implicitly: the node is marked by `uplink_port`
(manager/docs/CONTRACTS.md 4.2) and has `console_type: none` in the image catalog.
The guest is an offline appliance: no API, no HTTP port, no SSH in the final image; QGA is mandatory.
Python runtime has no third-party dependencies. PPPoE credentials never go into Git: the example
holds a placeholder, the real file is written by tools/install_guest.py at image build.
Run tests/ with pytest and compile uplink_net/. Do not run privileged networking commands or the
image preparation steps on the development host.
