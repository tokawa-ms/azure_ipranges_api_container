# Azure IP Ranges API

[![Python 3.13](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.116+-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)](https://docs.docker.com/compose/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Microsoft が公開する Azure Service Tags（Public cloud）の JSON を取得・検証し、サービス名からの IP 範囲検索と、IP アドレスからの Service Tag 逆引きを提供する読み取り専用 API です。

[English](README-en.md) | 日本語

## 特徴

- Azure Service Tags の最新 JSON を Microsoft の配布ページから取得
- ダウンロード済みデータの SHA-256、件数、メタデータを検証
- Docker named volume に検証済みデータをキャッシュ
- サービス名、リージョン、IP バージョンによる範囲検索
- IPv4 / IPv6 アドレスから Service Tag を逆引き
- liveness / readiness / metadata endpoint を提供
- 稼働中のバックグラウンド更新を持たない、起動時更新のみのシンプルな構成

## 構成

```text
クライアント
    │ HTTP
    ▼
FastAPI / Uvicorn
    ├─ サービス名・リージョン・IP バージョン検索
    ├─ IPv4 / IPv6 逆引き
    └─ health / metadata
             │
             ▼
    起動時に構築するメモリ内インデックス
             ▲
             │ 検証済み JSON + metadata.json
             ▼
    Docker named volume (/data)
             ▲
             │ キャッシュがない・無効・期限切れの場合のみ起動時に更新
             ▼
    Microsoft の Service Tags 配布ページ
```

更新処理はアプリケーション起動時にだけ実行されます。稼働中の定期更新処理はありません。

## 前提条件

- Docker Engine
- Docker Compose v2（`docker compose` コマンド）

ローカル開発を行う場合は Python 3.13 も必要です。

## クイックスタート

設定を変更したい場合だけ `.env.example` を `.env` にコピーします。既定値のまま起動する場合、コピーは不要です。

```powershell
Copy-Item .env.example .env
docker compose up --build
```

初回起動時は Service Tags データのダウンロードが完了してから API が ready になります。

## アクセス URL

| 用途         | URL                                |
| ------------ | ---------------------------------- |
| Swagger UI   | http://localhost:8000/docs         |
| ReDoc        | http://localhost:8000/redoc        |
| OpenAPI JSON | http://localhost:8000/openapi.json |
| Liveness     | http://localhost:8000/health/live  |
| Readiness    | http://localhost:8000/health/ready |

`API_PORT` を変更した場合は、URL の `8000` をその値に置き換えてください。

## API の使用例

より詳しい endpoint 仕様、レスポンス、エラー処理、PowerShell/Python の利用例は [API 利用ガイド](docs/api-usage.md) を参照してください。

以下は PowerShell からそのまま実行できます。`curl` は PowerShell の別名との衝突を避けるため `curl.exe` と記載しています。

### サービス名から検索

`service` は完全な Service Tag 名または `system_service` と大文字・小文字を区別せず完全一致します。

```powershell
curl.exe "http://localhost:8000/api/v1/ranges?service=Storage.JapanEast"

Invoke-RestMethod -Uri "http://localhost:8000/api/v1/ranges?service=Storage.JapanEast"
```

レスポンスの形:

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

### リージョンと IP バージョンで絞り込み

`region` は省略可能で大文字・小文字を区別しません。`ip_version` は省略するか、`4` または `6` を指定します。

```powershell
curl.exe "http://localhost:8000/api/v1/ranges?service=Storage.JapanEast&region=japaneast&ip_version=4"

Invoke-RestMethod -Uri "http://localhost:8000/api/v1/ranges?service=Storage.JapanEast&region=japaneast&ip_version=4"
```

### IPv4 の逆引き

```powershell
curl.exe "http://localhost:8000/api/v1/ip/20.60.1.10"

Invoke-RestMethod -Uri "http://localhost:8000/api/v1/ip/20.60.1.10"
```

レスポンスの各 `items` は、その IP を含む Service Tag と一致した `prefix` を返します。

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

### IPv6 の逆引き

IPv6 の `:` は通常そのまま使用できます。プロキシやクライアントがパスを正しく扱わない場合は URL エンコードしてください。

```powershell
curl.exe "http://localhost:8000/api/v1/ip/2001%3Adb8%3A%3A1"

$ip = [uri]::EscapeDataString("2001:db8::1")
Invoke-RestMethod -Uri "http://localhost:8000/api/v1/ip/$ip"
```

上の IPv6 は予約済みドキュメント用アドレスのため、通常は `items: []` になります。実際の一致を確認する場合は、サービス検索で返された IPv6 CIDR 内のアドレスを指定してください。

### Liveness、Readiness、メタデータ

```powershell
curl.exe "http://localhost:8000/health/live"
curl.exe "http://localhost:8000/health/ready"
curl.exe "http://localhost:8000/api/v1/metadata"

Invoke-RestMethod -Uri "http://localhost:8000/health/live"
Invoke-RestMethod -Uri "http://localhost:8000/health/ready"
Invoke-RestMethod -Uri "http://localhost:8000/api/v1/metadata"
```

正常時の主なフィールド:

| Endpoint           | 主なフィールド                                                                                 |
| ------------------ | ---------------------------------------------------------------------------------------------- |
| `/health/live`     | `status`                                                                                       |
| `/health/ready`    | `ready`, `reason`, `fetched_at`, `change_number`, `stale`                                      |
| `/api/v1/metadata` | `source_url`, `filename`, `fetched_at`, `sha256`, `change_number`, `tag_count`, `prefix_count` |

## 検索結果がない場合

有効な検索条件または IP アドレスに一致するデータがない場合は、エラーではなく HTTP `200` と空の `items` 配列を返します。

```json
{
  "ip_address": "1.2.3.4",
  "items": []
}
```

必須の `service` がない、空白だけである、`ip_version` が `4` / `6` 以外である、または IP アドレスが不正な場合は HTTP `422` です。

## キャッシュと更新ライフサイクル

Compose は named volume `azure-ipranges-data` をコンテナーの `/data` にマウントします。`docker compose down` やコンテナー再作成後も、検証済み Service Tags JSON、`metadata.json`、更新ロックが保持されます。

1. 起動時に `/data` の JSON と `metadata.json` を読み込み、内容、SHA-256、件数などを検証します。
2. 検証済みキャッシュが取得時刻から 7 日以内（`CACHE_TTL_SECONDS=604800`）なら、そのキャッシュからメモリ内インデックスを構築します。ネットワーク更新は行いません。
3. キャッシュがない、無効、または期限切れなら、プロセス間ロックを取得して再確認した後、Microsoft の配布ページから最新 JSON を取得します。
4. ダウンロード内容を検証し、JSON とメタデータを置き換えた後、再読み込みしてから API を ready にします。
5. 次の更新判定はコンテナーまたはアプリケーションの再起動時です。

キャッシュを含む named volume を明示的に削除するには、次を実行します。

```powershell
docker compose down --volumes
```

次回起動時はデータを再ダウンロードします。

更新が必要な状態でダウンロード、検証、または保存に失敗した場合、古い・無効なデータへフォールバックしません。検索 API と `/api/v1/metadata` は HTTP `503`、`/health/ready` も診断情報付き HTTP `503` を返します。一方、プロセス自体の生存確認である `/health/live` は HTTP `200` のままです。

検索・メタデータの `503` は次の形です。

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

`reason` と `retryable` は失敗状態によって変わります。

## 環境変数

Compose はカレントディレクトリの `.env` を読み込みます。

| 変数                   |                                                           既定値 | 説明                                                                                               |
| ---------------------- | ---------------------------------------------------------------: | -------------------------------------------------------------------------------------------------- |
| `API_PORT`             |                                                           `8000` | ホスト側で公開するポート                                                                           |
| `DATA_DIR`             |                                                          `/data` | キャッシュ保存先。提供される Compose では named volume のマウント先 `/data` のまま使用してください |
| `CACHE_TTL_SECONDS`    |                                                         `604800` | キャッシュ有効期間。正の整数。既定値は 7 日                                                        |
| `DOWNLOAD_PAGE_URL`    | `https://www.microsoft.com/en-us/download/details.aspx?id=56519` | 最新 JSON のリンクを探索する HTTPS ページ                                                          |
| `HTTP_TIMEOUT_SECONDS` |                                                             `30` | 各 HTTP リクエストのタイムアウト秒数。正の値                                                       |
| `HTTP_RETRY_COUNT`     |                                                              `3` | 最初の試行後に行う再試行回数。`0` は再試行なし                                                     |
| `LOG_LEVEL`            |                                                           `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`                                                    |

## Docker 運用

コンテナーを停止し、後で再開する:

```powershell
docker compose stop
docker compose start
```

稼働中のコンテナーを再起動する:

```powershell
docker compose restart
```

コンテナーを削除して再作成する（データ volume は保持）:

```powershell
docker compose down
docker compose up --build
```

ログと状態を確認する:

```powershell
docker compose logs -f api
docker compose ps
docker inspect --format "{{json .State.Health}}" (docker compose ps -q api)
```

## ローカル開発

Python 3.13 を使用します。PowerShell で仮想環境を作成し、プロジェクトの `dev` extras をインストールします。

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

ローカル実行時は、書き込み可能なデータディレクトリを指定してください。

```powershell
$env:DATA_DIR = Join-Path $PWD ".data"
.\.venv\Scripts\python.exe -m azure_ipranges_api
```

テスト:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

## トラブルシューティング

通常は次の確認で十分です。

```powershell
docker compose ps
curl.exe -i "http://localhost:8000/health/ready"
```

主な確認点:

- ポート競合: `.env` の `API_PORT` を空いているポートへ変更する。
- `503` / unhealthy: `docker compose logs api` で起動時のダウンロード、検証、保存エラーを確認する。
- 外部接続: コンテナーから Microsoft の HTTPS 配布ページと `download.microsoft.com` に到達できることを確認する。
- キャッシュ破損が疑われる場合: `docker compose down --volumes` 後に `docker compose up --build` で再取得する。
- 設定エラー: 正のタイムアウトと TTL、非負の再試行回数、許可されたログレベル、HTTPS の `DOWNLOAD_PAGE_URL` を使用する。

## コントリビューション

Issue、改善提案、ドキュメント修正を歓迎します。変更前にはテストを実行してください。

```powershell
.\.venv\Scripts\python.exe -m pytest
```

## ライセンス

このプロジェクトは [MIT License](LICENSE) の下で公開されています。
