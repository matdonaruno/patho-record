# Patho Return

病理検体返却管理システム - バーコードスキャンで検体の返却状況を追跡

![Demo](docs/demo.gif)

## 機能

- バーコードスキャンによる病理標本登録
- 3種類の返却状態管理（結果の返却、ブロックの返却、スライドの返却）
- 履歴検索・フィルタリング
- アプリ内アップデート機能（git pull）

## インストール (Linux Mint専用)

ターミナルで以下を実行:

```bash
sudo apt install -y git && git clone https://github.com/matdonaruno/patho-record.git && cd patho-record && ./install.sh
```

インストール完了後:
- デスクトップに「Patho Return」アイコンが表示されます
- アイコンをダブルクリックでアプリが起動します

## 手動起動

```bash
cd patho-record
./start.sh
```

## アップデート

アプリ内の設定画面から「アップデートを確認」→「アップデート実行」

または手動で:

```bash
cd patho-record
git pull origin main
```

## 使い方

1. **標本登録**: メイン画面でバーコードをスキャン → 病理標本情報が登録される
2. **返却処理**: 履歴から標本を選択 → 返却状態を更新
   - 結果の返却 / ブロックの返却 / スライドの返却 の3段階
3. **履歴検索**: フィルタ機能で返却状況を絞り込み

## データ管理

### バックアップ

| 保存先 | 場所 | 保持期間 |
|--------|------|----------|
| ローカル | `data/app.db` | 365日（自動削除） |
| USB | `barcode_app_backups/` | 永久保存 |

- バックアップはアプリ起動時に自動実行
- USBが接続されている場合、USBにもコピーされる

### 古いデータの参照（365日以前）

USBに保存されたバックアップファイルを直接参照できます：

```bash
# 1. USBのバックアップ一覧を確認
ls /media/usb_backup/barcode_app_backups/

# 2. SQLiteで開く（読み取り専用で安全に閲覧）
sqlite3 -readonly /media/usb_backup/barcode_app_backups/20240101_020000_app.db

# 3. データを確認（例：検体一覧）
SELECT * FROM items WHERE created_at < '2024-01-01';
```

※ DB Browser for SQLite（GUI）でも開けます

## 技術スタック

- Python 3 / Flask
- SQLite
- Bootstrap 5
- Vanilla JavaScript

## ライセンス

MIT
