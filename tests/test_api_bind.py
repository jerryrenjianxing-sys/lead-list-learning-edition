import api.main as api_main


def test_run_api_binds_to_loopback_by_default(monkeypatch):
    calls = []
    monkeypatch.delenv("MEDIACRAWLER_API_HOST", raising=False)
    monkeypatch.delenv("MEDIACRAWLER_API_PORT", raising=False)
    monkeypatch.setattr(api_main.uvicorn, "run", lambda app, **kwargs: calls.append(kwargs))

    api_main.run_api()

    assert calls == [{"host": "127.0.0.1", "port": 8080}]


def test_run_api_allows_explicit_host_and_port(monkeypatch):
    calls = []
    monkeypatch.setenv("MEDIACRAWLER_API_HOST", "0.0.0.0")
    monkeypatch.setenv("MEDIACRAWLER_API_PORT", "18080")
    monkeypatch.setattr(api_main.uvicorn, "run", lambda app, **kwargs: calls.append(kwargs))

    api_main.run_api()

    assert calls == [{"host": "0.0.0.0", "port": 18080}]
