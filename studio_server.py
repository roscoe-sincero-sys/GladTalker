#!/usr/bin/env python3
"""GladTalker Studio engine: local FastAPI + WebSocket backend for the Studio UI (issue #11).

Usage::

    python studio_server.py                      # http://127.0.0.1:7860  (docs at /docs)
    python studio_server.py --device dml:0       # force a device (auto, cpu, cuda, dml, dml:N, mps)
    python studio_server.py --port 0 --token X   # desktop sidecar: random port, bearer token

With ``--port 0`` the chosen port is printed as a single JSON line on stdout
(``{"event": "listening", "host": ..., "port": ...}``) so a parent process can connect.
"""

import argparse
import json
import logging
import os
import socket
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Run the GladTalker Studio engine (local API server).")
    p.add_argument("--host", default=os.environ.get("GLADTALKER_STUDIO_HOST", "127.0.0.1"),
                   help="Interface to bind (default 127.0.0.1; only change this on a trusted network).")
    p.add_argument("--port", type=int, default=int(os.environ.get("GLADTALKER_STUDIO_PORT", "7860")),
                   help="Port to listen on (0 = pick a free port and print it).")
    p.add_argument("--device", default=os.environ.get("GLADTALKER_DEVICE", "auto"),
                   help="Compute device: auto, cpu, cuda, cuda:N, dml, dml:N, mps.")
    p.add_argument("--workspace", type=Path, default=REPO_ROOT / "results" / "studio",
                   help="Folder for uploads and renders (default results/studio).")
    p.add_argument("--token", default=os.environ.get("GLADTALKER_STUDIO_TOKEN"),
                   help="Require this bearer token on every request (set by the desktop shell).")
    p.add_argument("--allow-origin", action="append", default=[],
                   help="Extra browser origin allowed for CORS/WebSockets (repeatable).")
    p.add_argument("--allow-host", action="append", default=[],
                   help="Extra Host header value to accept (repeatable), e.g. when binding 0.0.0.0.")
    p.add_argument("--log-level", default="info", choices=["critical", "error", "warning", "info", "debug"])
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    import uvicorn

    from src.studio.server import DEFAULT_HOSTS, DEFAULT_ORIGINS, StudioConfig, create_app

    hosts = list(DEFAULT_HOSTS) + args.allow_host
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        hosts.append(args.host)
        logging.warning("Binding to %s exposes GladTalker Studio beyond this computer. Use --token.", args.host)
    config = StudioConfig(
        device=args.device,
        workspace=args.workspace.resolve(),
        token=args.token,
        allowed_origins=list(DEFAULT_ORIGINS) + args.allow_origin,
        allowed_hosts=hosts,
    )
    app = create_app(config)

    family = socket.AF_INET6 if ":" in args.host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.bind((args.host, args.port))
    port = sock.getsockname()[1]
    shown = f"[{args.host}]" if ":" in args.host else args.host
    print(json.dumps({"event": "listening", "host": args.host, "port": port,
                      "url": f"http://{shown}:{port}", "docs": f"http://{shown}:{port}/docs"}), flush=True)

    server = uvicorn.Server(uvicorn.Config(app, log_level=args.log_level, ws="auto"))
    server.run(sockets=[sock])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
