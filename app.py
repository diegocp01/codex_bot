"""Development entry point for Codex Bots."""

import os
import threading
import webbrowser

from codex_bots import create_app

app = create_app()


if __name__ == "__main__":
    host = os.environ.get("CODEX_BOTS_HOST", "127.0.0.1")
    port = int(os.environ.get("CODEX_BOTS_PORT", "5055"))
    if os.environ.get("CODEX_BOTS_OPEN_BROWSER", "0") == "1":
        threading.Timer(1.1, lambda: webbrowser.open(f"http://{host}:{port}")).start()
    app.run(
        host=host,
        port=port,
        debug=os.environ.get("FLASK_DEBUG", "0") == "1",
        use_reloader=False,
    )
