from __future__ import annotations

import uvicorn

from azure_ipranges_api.__main__ import main


def test_main_starts_uvicorn_with_application_factory(monkeypatch) -> None:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def fake_run(*args, **kwargs) -> None:
        calls.append((args, kwargs))

    monkeypatch.setattr(uvicorn, "run", fake_run)

    main()

    assert calls == [
        (
            ("azure_ipranges_api.main:create_app",),
            {
                "host": "0.0.0.0",
                "port": 8000,
                "factory": True,
            },
        )
    ]
