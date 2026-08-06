"""Run the API with Uvicorn."""

from __future__ import annotations

import uvicorn


def main() -> None:
    uvicorn.run(
        "azure_ipranges_api.main:create_app",
        host="0.0.0.0",
        port=8000,
        factory=True,
    )


if __name__ == "__main__":
    main()
