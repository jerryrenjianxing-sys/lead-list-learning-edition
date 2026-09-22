"""Internal worker-to-host operations. No credential payloads cross this interface."""

import json
import os
import urllib.request


def command(action, **values):
    raw = os.environ.get("MEDIAWORKBENCH_SESSION")
    if not raw:
        return None
    session = json.loads(raw)
    request = urllib.request.Request(
        os.environ["MEDIAWORKBENCH_SERVICE_URL"] + "/api/v1/internal/session",
        data=json.dumps(
            {"lease": session["lease"], "action": action, **values}
        ).encode(),
        headers={
            "Authorization": "Bearer " + os.environ["MEDIAWORKBENCH_WORKER_TOKEN"],
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.load(response)
