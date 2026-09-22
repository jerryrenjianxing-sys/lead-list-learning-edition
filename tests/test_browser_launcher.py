from types import SimpleNamespace

from tools import browser_launcher
from tools.browser_launcher import BrowserLauncher


def test_windows_cleanup_closes_relaunched_browser_by_user_data_dir(monkeypatch):
    launcher = BrowserLauncher()
    launcher.system = "Windows"
    launcher.user_data_dir = r"C:\project\browser_data\cdp_xhs_user_data_dir"
    launcher.browser_process = SimpleNamespace(pid=1234, poll=lambda: 0)
    killed = []
    owned = SimpleNamespace(
        info={
            "cmdline": [
                "msedge.exe",
                r"--user-data-dir=C:\project\browser_data\cdp_xhs_user_data_dir",
            ],
        },
        kill=lambda: killed.append("owned"),
    )
    unrelated = SimpleNamespace(
        info={"cmdline": ["msedge.exe", r"--user-data-dir=D:\other\Edge"]},
        kill=lambda: killed.append("unrelated"),
    )
    monkeypatch.setattr(
        browser_launcher,
        "psutil",
        SimpleNamespace(
            Error=Exception,
            process_iter=lambda _attrs: [owned, unrelated],
            wait_procs=lambda _processes, timeout: None,
        ),
        raising=False,
    )

    launcher.cleanup()

    assert killed == ["owned"]
    assert launcher.browser_process is None
