# Azure IP Ranges API 利用ガイド

Azure Public Cloud の Service Tags データを、サービス名・リージョンから検索する正引き API と、IP アドレスから検索する逆引き API の利用方法を説明します。

## 接続先

Docker Compose の既定設定では、ベース URL は次のとおりです。

```text
http://localhost:8000
```

`.env` の `API_PORT` を変更した場合は、`8000` を設定したポート番号に置き換えてください。

API 仕様はブラウザーでも確認できます。

| 仕様 | URL |
|---|---|
| Swagger UI | `http://localhost:8000/docs` |
| ReDoc | `http://localhost:8000/redoc` |
| OpenAPI JSON | `http://localhost:8000/openapi.json` |

## Endpoint 一覧

| Method | Path | 用途 |
|---|---|---|
| `GET` | `/api/v1/ranges` | サービス名、リージョン、IP バージョンから CIDR を検索 |
| `GET` | `/api/v1/ip/{ip_address}` | IPv4 または IPv6 から該当 Service Tag を検索 |
| `GET` | `/api/v1/metadata` | 現在ロードされているデータのメタデータを取得 |
| `GET` | `/health/live` | API プロセスの生存確認 |
| `GET` | `/health/ready` | 検証済みデータを検索可能か確認 |

認証ヘッダーやリクエストボディは不要です。すべて読み取り専用の `GET` API です。

## サービス名から CIDR を検索する

```http
GET /api/v1/ranges
```

### Query parameter

| Parameter | 必須 | 値 | 説明 |
|---|---|---|---|
| `service` | はい | 文字列 | 完全な Service Tag 名または `system_service`。大文字・小文字は区別しません |
| `region` | いいえ | 文字列 | Azure リージョンの完全一致フィルター。大文字・小文字は区別しません |
| `ip_version` | いいえ | `4` または `6` | IPv4 または IPv6 のプレフィックスだけを返します |

`service` は部分一致ではありません。たとえば `Storage` は名前が正確に `Storage` のタグを検索します。複数のタグをサービス種別で検索する場合は配布データ上の `system_service`（Storage の場合は通常 `AzureStorage`）を指定し、特定のタグだけを検索する場合は `Storage.JapanEast` のような完全な Service Tag 名を指定してください。

### 使用例

名前が `Storage` のグローバル Service Tag を検索します。

```powershell
curl.exe "http://localhost:8000/api/v1/ranges?service=Storage"
```

```bash
curl "http://localhost:8000/api/v1/ranges?service=Storage"
```

Japan East の IPv4 プレフィックスに絞り込みます。

```powershell
$uri = "http://localhost:8000/api/v1/ranges?service=Storage.JapanEast&region=japaneast&ip_version=4"
Invoke-RestMethod -Uri $uri
```

### HTTP 200 response

```json
{
  "service": "Storage.JapanEast",
  "region": "japaneast",
  "ip_version": 4,
  "items": [
    {
      "name": "Storage.JapanEast",
      "id": "Storage.JapanEast",
      "change_number": 1,
      "region": "japaneast",
      "platform": "Azure",
      "system_service": "AzureStorage",
      "address_prefixes": [
        "20.60.0.0/16"
      ]
    }
  ]
}
```

`items` の各要素は 1 つの Service Tag を表します。`address_prefixes` には、指定した `ip_version` に一致する CIDR だけが含まれます。

検索条件に一致するタグがない場合も HTTP `200` です。

```json
{
  "service": "UnknownService",
  "region": null,
  "ip_version": null,
  "items": []
}
```

## IP アドレスから Service Tag を逆引きする

```http
GET /api/v1/ip/{ip_address}
```

指定した IP アドレスを包含するすべての Service Tag と CIDR を返します。同じ IP が複数の Service Tag やプレフィックスに含まれる場合、すべての一致が最長プレフィックス順で返されます。

### IPv4 の使用例

```powershell
curl.exe "http://localhost:8000/api/v1/ip/20.60.1.10"
```

```bash
curl "http://localhost:8000/api/v1/ip/20.60.1.10"
```

### IPv6 の使用例

IPv6 は URL path に含めるため、クライアント側で URL エンコードする方法が安全です。

```powershell
$ip = [uri]::EscapeDataString("2001:db8::1")
Invoke-RestMethod -Uri "http://localhost:8000/api/v1/ip/$ip"
```

```bash
curl "http://localhost:8000/api/v1/ip/2001%3Adb8%3A%3A1"
```

### HTTP 200 response

```json
{
  "ip_address": "20.60.1.10",
  "items": [
    {
      "name": "Storage.JapanEast",
      "id": "Storage.JapanEast",
      "change_number": 1,
      "region": "japaneast",
      "platform": "Azure",
      "system_service": "AzureStorage",
      "prefix": "20.60.0.0/16"
    }
  ]
}
```

有効な IP アドレスが Azure Service Tags のどのプレフィックスにも一致しない場合は、HTTP `200` と空の `items` を返します。

```json
{
  "ip_address": "192.0.2.1",
  "items": []
}
```

## データのメタデータを取得する

```http
GET /api/v1/metadata
```

```powershell
Invoke-RestMethod -Uri "http://localhost:8000/api/v1/metadata"
```

### HTTP 200 response

```json
{
  "source_url": "https://download.microsoft.com/download/.../ServiceTags_Public_20260803.json",
  "filename": "ServiceTags_Public_20260803.json",
  "fetched_at": "2026-08-06T10:00:00Z",
  "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "change_number": 420,
  "tag_count": 3050,
  "prefix_count": 90000
}
```

| Field | 説明 |
|---|---|
| `source_url` | 実際に取得した Microsoft の JSON URL |
| `filename` | 配布 JSON のファイル名 |
| `fetched_at` | キャッシュを取得した UTC 日時 |
| `sha256` | 検証済み JSON の SHA-256 |
| `change_number` | JSON 全体の変更番号 |
| `tag_count` | Service Tag の件数 |
| `prefix_count` | IPv4/IPv6 プレフィックスの合計件数 |

## Health check

### Liveness

```http
GET /health/live
```

API プロセスが稼働していれば HTTP `200` を返します。検索データが利用できない場合でも `200` です。

```json
{
  "status": "ok"
}
```

### Readiness

```http
GET /health/ready
```

検索に使用できる検証済みデータがロードされていれば HTTP `200` を返します。

```json
{
  "ready": true,
  "reason": null,
  "fetched_at": "2026-08-06T10:00:00Z",
  "change_number": 420,
  "stale": false
}
```

データを利用できない場合は HTTP `503` です。

```json
{
  "ready": false,
  "reason": "No validated Azure IP ranges data is available.",
  "fetched_at": null,
  "change_number": null,
  "stale": true
}
```

ロードバランサーやコンテナー監視では、プロセス監視に `/health/live`、トラフィック受付可否の判定に `/health/ready` を使用してください。

## Status code とエラー

| Status | 意味 |
|---|---|
| `200` | リクエスト成功。一致なしの場合も空の `items` として成功します |
| `422` | 必須 parameter の欠落、空白の `service`、不正な `ip_version`、不正な IP アドレス |
| `503` | 起動時のダウンロード、検証、またはキャッシュ更新に失敗し、検索可能なデータがありません |

### HTTP 422

```json
{
  "detail": [
    {
      "type": "value_error",
      "loc": [
        "path",
        "ip_address"
      ],
      "msg": "Value error, IP address must be a valid IPv4 or IPv6 address",
      "input": "not-an-ip"
    }
  ]
}
```

### HTTP 503

`/api/v1/ranges`、`/api/v1/ip/{ip_address}`、`/api/v1/metadata` は共通のエラー形式を返します。

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

`reason` と `retryable` は失敗原因によって変化します。`retryable: true` の場合でも、更新処理はアプリケーション起動時だけ実行されるため、原因を解消してコンテナーを再起動してください。

## PowerShell で結果を処理する例

Japan East の Storage IPv4 CIDR を文字列配列として取得します。

```powershell
$response = Invoke-RestMethod -Uri (
    "http://localhost:8000/api/v1/ranges" +
    "?service=Storage.JapanEast&region=japaneast&ip_version=4"
)

$cidrs = $response.items |
    ForEach-Object { $_.address_prefixes } |
    Sort-Object -Unique

$cidrs
```

IP アドレスに該当する Service Tag 名を取得します。

```powershell
$ip = "20.60.1.10"
$response = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/ip/$ip"
$response.items.name | Sort-Object -Unique
```

## Python から呼び出す例

標準ライブラリだけで呼び出せます。

```python
import json
from urllib.parse import urlencode
from urllib.request import urlopen

query = urlencode(
    {
        "service": "Storage.JapanEast",
        "region": "japaneast",
        "ip_version": 4,
    }
)

with urlopen(
    f"http://localhost:8000/api/v1/ranges?{query}",
    timeout=10,
) as response:
    payload = json.load(response)

for item in payload["items"]:
    for prefix in item["address_prefixes"]:
        print(item["name"], prefix)
```
