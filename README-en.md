# Azure IP Ranges API

[![Python 3.13](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.116+-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)](https://docs.docker.com/compose/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A read-only API that downloads and validates Microsoft Azure Service Tags for the public cloud, then provides service-to-IP range lookup and reverse lookup from an IP address to matching Service Tags.

English | [Japanese](README.md)

## Features

- Downloads the latest Azure Service Tags JSON from Microsoft's distribution page
- Validates downloaded data, metadata, SHA-256, and item counts
- Caches validated data in a Docker named volume
- Searches address ranges by service name, region, and IP version
- Resolves IPv4 / IPv6 addresses back to matching Service Tags
- Exposes liveness, readiness, and metadata endpoints
- Keeps runtime behavior simple: data refresh happens only during application startup

## Architecture

```text
Client
    | HTTP
    v
FastAPI / Uvicorn
    |- service / region / IP version search
    |- IPv4 / IPv6 reverse lookup
    `- health / metadata
             |
             v
    In-memory index built at startup
             ^
             | validated JSON + metadata.json
             v
    Docker named volume (/data)
             ^
             | refreshed at startup only when cache is missing, invalid, or expired
             v
    Microsoft Service Tags download page
```

The application refreshes data only at startup. It does not run a background refresh worker while serving traffic.

## Requirements

- Docker Engine
- Docker Compose v2 (`docker compose` command)

Python 3.13 is also required for local development outside Docker.

## Quick Start

Copy the sample environment file only if you want to change the defaults. It is not required for a default startup.

```powershell
Copy-Item .env.example .env
docker compose up --build
```

On the first startup, the API becomes ready after the Service Tags data has been downloaded and validated.

## URLs

| Purpose      | URL                                |
| ------------ | ---------------------------------- |
| Swagger UI   | http://localhost:8000/docs         |
| ReDoc        | http://localhost:8000/redoc        |
| OpenAPI JSON | http://localhost:8000/openapi.json |
| Liveness     | http://localhost:8000/health/live  |
| Readiness    | http://localhost:8000/health/ready |

If you change `API_PORT`, replace `8000` in these URLs with your configured value.

## API Examples

For full endpoint details, response schemas, error handling, and PowerShell/Python examples, see the [API usage guide](docs/api-usage.md).

The following examples can be run directly from PowerShell. They use `curl.exe` to avoid PowerShell's `curl` alias.

### Search by Service Name

`service` matches either the full Service Tag name or `system_service`, case-insensitively.

```powershell
curl.exe "http://localhost:8000/api/v1/ranges?service=Storage.JapanEast"

Invoke-RestMethod -Uri "http://localhost:8000/api/v1/ranges?service=Storage.JapanEast"
```

Example response shape:

```json
{
  "service": "Storage.JapanEast",
  "region": null,
  "ip_version": null,
  "items": [
    {
      "name": "Storage.JapanEast",
      "id": "Storage.JapanEast",
      "change_number": 1,
      "region": "japaneast",
      "platform": "Azure",
      "system_service": "AzureStorage",
      "address_prefixes": ["20.60.0.0/16"]
    }
  ]
}
```

### Filter by Region and IP Version

`region` is optional and case-insensitive. `ip_version` is optional and must be either `4` or `6` when provided.

```powershell
curl.exe "http://localhost:8000/api/v1/ranges?service=Storage.JapanEast&region=japaneast&ip_version=4"

Invoke-RestMethod -Uri "http://localhost:8000/api/v1/ranges?service=Storage.JapanEast&region=japaneast&ip_version=4"
```

### Reverse Lookup for IPv4

```powershell
curl.exe "http://localhost:8000/api/v1/ip/20.60.1.10"

Invoke-RestMethod -Uri "http://localhost:8000/api/v1/ip/20.60.1.10"
```

Each item in the response returns the matching Service Tag and the matched `prefix` that contains the requested IP address.

```json
{
  "ip_address": "20.60.1.10",
  "items": [
    {
      "name": "Storage",
      "id": "Storage",
      "change_number": 1,
      "region": "",
      "platform": "Azure",
      "system_service": "AzureStorage",
      "prefix": "20.60.0.0/16"
    }
  ]
}
```

### Reverse Lookup for IPv6

IPv6 `:` characters usually work as-is. URL-encode the address if your proxy or client does not handle the path correctly.

```powershell
curl.exe "http://localhost:8000/api/v1/ip/2001%3Adb8%3A%3A1"

$ip = [uri]::EscapeDataString("2001:db8::1")
Invoke-RestMethod -Uri "http://localhost:8000/api/v1/ip/$ip"
```

The IPv6 address above is a documentation-only address, so it usually returns `items: []`. To verify a real match, use an address from an IPv6 CIDR returned by the service search endpoint.

### Liveness, Readiness, and Metadata

```powershell
curl.exe "http://localhost:8000/health/live"
curl.exe "http://localhost:8000/health/ready"
curl.exe "http://localhost:8000/api/v1/metadata"

Invoke-RestMethod -Uri "http://localhost:8000/health/live"
Invoke-RestMethod -Uri "http://localhost:8000/health/ready"
Invoke-RestMethod -Uri "http://localhost:8000/api/v1/metadata"
```

Main healthy-state fields:

| Endpoint           | Main fields                                                                                    |
| ------------------ | ---------------------------------------------------------------------------------------------- |
| `/health/live`     | `status`                                                                                       |
| `/health/ready`    | `ready`, `reason`, `fetched_at`, `change_number`, `stale`                                      |
| `/api/v1/metadata` | `source_url`, `filename`, `fetched_at`, `sha256`, `change_number`, `tag_count`, `prefix_count` |

## Empty Results and Validation Errors

If valid search criteria or an IP address do not match any data, the API returns HTTP `200` with an empty `items` array.

```json
{
  "ip_address": "1.2.3.4",
  "items": []
}
```

The API returns HTTP `422` when the required `service` value is missing or blank, when `ip_version` is not `4` or `6`, or when an IP address is invalid.

## Cache and Refresh Lifecycle

Docker Compose mounts the named volume `azure-ipranges-data` at `/data` inside the container. Validated Service Tags JSON, `metadata.json`, and the refresh lock survive `docker compose down` and container recreation.

1. At startup, the app loads JSON and `metadata.json` from `/data`, then validates content, SHA-256, and counts.
2. If the validated cache is still within the configured TTL (`CACHE_TTL_SECONDS=604800` by default), the in-memory index is built from the cache without a network refresh.
3. If the cache is missing, invalid, or expired, the app acquires an inter-process lock, rechecks the cache, and downloads the latest JSON from Microsoft's distribution page.
4. The downloaded content is validated, JSON and metadata are replaced, and the API becomes ready after reloading the data.
5. The next refresh decision happens on the next container or application restart.

To explicitly delete the cache volume, run:

```powershell
docker compose down --volumes
```

The next startup downloads the data again.

If refresh is required and download, validation, or persistence fails, the app does not fall back to stale or invalid data. Search APIs and `/api/v1/metadata` return HTTP `503`; `/health/ready` also returns HTTP `503` with diagnostic information. `/health/live` continues to return HTTP `200` because the process is alive.

Search and metadata `503` responses use this shape:

```json
{
  "detail": {
    "code": "data_unavailable",
    "message": "Azure IP ranges data is temporarily unavailable.",
    "reason": "No validated Azure IP ranges data is available.",
    "retryable": true
  }
}
```

`reason` and `retryable` vary by failure state.

## Configuration

Compose reads `.env` from the current directory.

| Variable               |                                                          Default | Description                                                           |
| ---------------------- | ---------------------------------------------------------------: | --------------------------------------------------------------------- |
| `API_PORT`             |                                                           `8000` | Host port exposed by Docker Compose                                   |
| `DATA_DIR`             |                                                          `/data` | Cache directory. With the provided Compose file, keep this as `/data` |
| `CACHE_TTL_SECONDS`    |                                                         `604800` | Cache TTL in seconds. Must be a positive integer. Default is 7 days   |
| `DOWNLOAD_PAGE_URL`    | `https://www.microsoft.com/en-us/download/details.aspx?id=56519` | HTTPS page used to discover the latest JSON link                      |
| `HTTP_TIMEOUT_SECONDS` |                                                             `30` | Timeout in seconds for each HTTP request. Must be positive            |
| `HTTP_RETRY_COUNT`     |                                                              `3` | Retry count after the initial attempt. `0` disables retries           |
| `LOG_LEVEL`            |                                                           `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`                       |

## Docker Operations

Stop and later resume the container:

```powershell
docker compose stop
docker compose start
```

Restart the running container:

```powershell
docker compose restart
```

Remove and recreate the container while keeping the data volume:

```powershell
docker compose down
docker compose up --build
```

Inspect logs and runtime state:

```powershell
docker compose logs -f api
docker compose ps
docker inspect --format "{{json .State.Health}}" (docker compose ps -q api)
```

## Local Development

Use Python 3.13. Create a virtual environment from PowerShell and install the project with the `dev` extras.

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

When running locally, provide a writable data directory.

```powershell
$env:DATA_DIR = Join-Path $PWD ".data"
.\.venv\Scripts\python.exe -m azure_ipranges_api
```

Run tests:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

## Troubleshooting

These checks are usually enough to start diagnosing issues:

```powershell
docker compose ps
curl.exe -i "http://localhost:8000/health/ready"
```

Common checks:

- Port conflict: change `API_PORT` in `.env` to an available port.
- `503` / unhealthy: inspect startup download, validation, or persistence errors with `docker compose logs api`.
- External connectivity: ensure the container can reach Microsoft's HTTPS distribution page and `download.microsoft.com`.
- Suspected cache corruption: run `docker compose down --volumes`, then `docker compose up --build` to download fresh data.
- Configuration errors: use positive timeout and TTL values, a non-negative retry count, an allowed log level, and an HTTPS `DOWNLOAD_PAGE_URL`.

## Contributing

Issues, improvement ideas, and documentation fixes are welcome. Please run the tests before submitting changes.

```powershell
.\.venv\Scripts\python.exe -m pytest
```

## License

This project is licensed under the [MIT License](LICENSE).
