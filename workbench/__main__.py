"""Single-user local host. Discovery is published only once the socket is bound."""

import argparse
import json
import os
import socket
import sys
import threading
from pathlib import Path
import uvicorn
from .paths import PRODUCT, data_root, discovery_path
from . import __version__


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=48139)
    parser.add_argument("--data-dir")
    args = parser.parse_args()
    if args.data_dir:
        os.environ["MEDIAWORKBENCH_DATA_DIR"] = str(Path(args.data_dir).resolve())
    root = data_root()
    root.mkdir(parents=True, exist_ok=True)
    lock = (root / ".host.lock").open("a+b")
    if os.name == "nt":
        import msvcrt

        lock.seek(0)
        lock.write(b"0")
        lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise SystemExit("A host already owns this data directory")
    else:
        import fcntl

        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    from .app import make_app

    app = make_app(root)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if os.name == "nt":
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        sock.bind(("127.0.0.1", args.port))
    except OSError:
        sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    port = sock.getsockname()[1]
    app.state.sessions.url = f"http://127.0.0.1:{port}"
    discovery = discovery_path()
    discovery.parent.mkdir(parents=True, exist_ok=True)
    content = {
        "product": PRODUCT,
        "version": __version__,
        "api_version": 1,
        "base_url": f"http://127.0.0.1:{port}",
        "token": app.state.token,
        "python": sys.executable,
        "pid": os.getpid(),
        "data_directory": str(root),
    }
    temporary = discovery.with_suffix(".tmp")
    temporary.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
    temporary.replace(discovery)
    try:
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info")
        )

        def watch_stop():
            while not server.should_exit:
                if getattr(app.state, "stop_requested", False):
                    server.should_exit = True
                    return
                threading.Event().wait(0.3)

        threading.Thread(target=watch_stop, daemon=True).start()
        server.run(sockets=[sock])
    finally:
        if discovery.exists():
            try:
                if (
                    json.loads(discovery.read_text(encoding="utf-8")).get("pid")
                    == os.getpid()
                ):
                    discovery.unlink()
            except (ValueError, OSError):
                pass
        sock.close()
        lock.close()


if __name__ == "__main__":
    main()
