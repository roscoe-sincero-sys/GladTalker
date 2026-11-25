#!/usr/bin/env python3
"""GladTalker Studio engine: local FastAPI + WebSocket backend for the Studio UI (issue #11).

Usage::

    python studio_server.py                      # http://127.0.0.1:7860  (docs at /docs)
    python studio_server.py --device dml:0       # force a device (auto, cpu, cuda, dml, dml:N, mps)
    python studio_server.py --open               # web mode: also open the Studio UI in your browser
    python studio_server.py --port 0 --token X --exit-with-parent PID
                                                 # desktop sidecar: random port, bearer token,
                                                 # stops when the desktop app (PID) closes

With ``--port 0`` the chosen port is printed as a single JSON line on stdout
(``{"event": "listening", "host": ..., "port": ...}``) so a parent process can connect.
"""

import argparse
import ipaddress
import json
import logging
import os
import socket
import sys
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def open_in_browser(url: str) -> None:
    import webbrowser

    try:
        webbrowser.open(url)
    except Exception as exc:  # noqa: BLE001 - never fail the engine over a browser
        logging.warning("Couldn't open a browser (%s). Open %s yourself.", exc, url)


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
    p.add_argument("--open", action="store_true",
                   help="Open the Studio UI in your web browser once the engine is ready (web mode).")
    p.add_argument("--exit-with-parent", type=int, metavar="PID",
                   help="Shut down when process PID ends (used by the desktop app).")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    from src.studio.server import DEFAULT_HOSTS, DEFAULT_ORIGINS, StudioConfig, create_app

    hosts = list(DEFAULT_HOSTS) + args.allow_host
    if not is_loopback(args.host):
        if not (args.token or "").strip():
            print(f"Refusing to listen on {args.host} without a token: other computers could start renders "
                  "and read files. Pass --token (or set GLADTALKER_STUDIO_TOKEN), or use --host 127.0.0.1.",
                  file=sys.stderr)
            return 2
        hosts.append(args.host)
        logging.warning("Binding to %s exposes GladTalker Studio beyond this computer; add the address "
                        "clients will use with --allow-host.", args.host)

    import uvicorn

    config = StudioConfig(
        device=args.device,
        workspace=args.workspace.resolve(),
        token=(args.token or "").strip() or None,
        allowed_origins=list(DEFAULT_ORIGINS) + args.allow_origin,
        allowed_hosts=hosts,
    )
    app = create_app(config)

    family = socket.AF_INET6 if ":" in args.host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.bind((args.host, args.port))
    port = sock.getsockname()[1]
    shown = f"[{args.host}]" if ":" in args.host else args.host
    ready_line = json.dumps({"event": "listening", "host": args.host, "port": port,
                             "url": f"http://{shown}:{port}", "docs": f"http://{shown}:{port}/docs"})

    class AnnouncingServer(uvicorn.Server):
        """Prints the ready line only once uvicorn is accepting connections, so a parent process
        can connect the moment it reads it."""

        async def startup(self, sockets=None):
            await super().startup(sockets=sockets)
            if self.started and not self.should_exit:
                print(ready_line, flush=True)
                if args.open and config.token:
                    # A fresh browser tab wouldn't have the token, so it would only show an
                    # offline page. The token is never put in a URL handed to another program
                    # (process lists show command lines); the user opens it with the token.
                    print(f"--open: this engine requires a token, so no browser was opened. Open "
                          f"http://{shown}:{port}/#token=<your token> yourself (the part after # "
                          f"stays in the browser).", file=sys.stderr, flush=True)
                elif args.open:
                    # The announced address, not "localhost": that's a different browser origin
                    # (with its own saved project) and may resolve to ::1, which isn't bound.
                    open_in_browser(f"http://{shown}:{port}/")

    server = AnnouncingServer(uvicorn.Config(app, log_level=args.log_level, ws="auto"))
    if args.exit_with_parent:
        from src.studio.parent_watch import watch_parent

        def stop():
            server.should_exit = True  # graceful: finish requests, run shutdown (jobs, previews)
            threading.Timer(15.0, lambda: os._exit(0)).start()  # in case shutdown hangs

        watch_parent(args.exit_with_parent, stop)
    server.run(sockets=[sock])
    return 0 if server.started else 1


if __name__ == "__main__":
    raise SystemExit(main())
