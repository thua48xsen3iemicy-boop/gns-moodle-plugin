"""Development entry point: python -m lab_manager (no fixed-path checks, see run.py)."""

import logging

import uvicorn

from . import config
from .app import Services, create_app


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = config.load()
    app = create_app(Services(settings))
    # One process: the orchestrator and the per-session tasks live in this event loop.
    uvicorn.run(app, host=settings.listen_host, port=settings.listen_port, workers=1,
                log_level="info", access_log=False)


if __name__ == "__main__":
    main()
