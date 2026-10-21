"""Internal worker-to-host operations. No credential payloads cross this interface."""

import json
import os
import urllib.request
import urllib.error


class InternalAuthorizationError(RuntimeError):
    pass


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
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code in (401,403):
            raise InternalAuthorizationError('Local application authorization failed; restart the application connection') from None
        raise
    if result.get('saved') and 'generation' in result:
        session['generation']=result['generation']
        os.environ['MEDIAWORKBENCH_SESSION']=json.dumps(session)
    return result
