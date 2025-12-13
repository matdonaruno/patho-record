# Patho Return

病理検体返却管理システム - バーコードスキャンで検体の返却状況を追跡

## 機能

- バーコードスキャンによる検体登録
- 3種類の返却状態管理（主治医返却、病理返却、患者返却）
- 履歴検索・フィルタリング
- アプリ内アップデート機能（git pull）

## Linux Mint インストール

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

## 技術スタック

- Python 3 / Flask
- SQLite
- Bootstrap 5
- Vanilla JavaScript

## ライセンス

MIT
