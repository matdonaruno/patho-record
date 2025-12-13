#!/bin/bash
# Patho Return - Linux Mint インストールスクリプト

set -e

echo "========================================"
echo "  Patho Return インストーラー"
echo "========================================"
echo ""

# 現在のディレクトリを取得
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$APP_DIR"

echo "[1/5] システム依存関係を確認中..."

# Python3とpipの確認
if ! command -v python3 &> /dev/null; then
    echo "Python3が見つかりません。インストールします..."
    sudo apt update
    sudo apt install -y python3 python3-pip python3-venv
fi

# gitの確認
if ! command -v git &> /dev/null; then
    echo "gitが見つかりません。インストールします..."
    sudo apt install -y git
fi

echo "  -> Python3: $(python3 --version)"
echo "  -> Git: $(git --version)"

echo ""
echo "[2/5] Python仮想環境を作成中..."

# 仮想環境を作成
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "  -> 仮想環境を作成しました"
else
    echo "  -> 仮想環境は既に存在します"
fi

echo ""
echo "[3/5] 依存関係をインストール中..."

# 仮想環境を有効化してパッケージをインストール
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate

echo "  -> 依存関係のインストール完了"

echo ""
echo "[4/5] データディレクトリを作成中..."

# データディレクトリとログディレクトリを作成
mkdir -p data
mkdir -p logs
mkdir -p backup

echo "  -> data/, logs/, backup/ ディレクトリを作成しました"

echo ""
echo "[5/5] デスクトップアイコンを設定中..."

# .desktopファイルを作成
DESKTOP_FILE="$HOME/.local/share/applications/patho-return.desktop"
mkdir -p "$HOME/.local/share/applications"

cat > "$DESKTOP_FILE" << EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Patho Return
Comment=病理検体返却管理システム
Exec=$APP_DIR/start.sh
Icon=$APP_DIR/static/images/icon.svg
Terminal=false
Categories=Office;Medical;
StartupNotify=true
EOF

# 実行権限を付与
chmod +x "$DESKTOP_FILE"
chmod +x "$APP_DIR/start.sh"
chmod +x "$APP_DIR/install.sh"

# デスクトップにショートカットを作成
if [ -d "$HOME/Desktop" ]; then
    cp "$DESKTOP_FILE" "$HOME/Desktop/"
    chmod +x "$HOME/Desktop/patho-return.desktop"
    # Linux Mintでは信頼が必要な場合がある
    gio set "$HOME/Desktop/patho-return.desktop" metadata::trusted true 2>/dev/null || true
    echo "  -> デスクトップにショートカットを作成しました"
elif [ -d "$HOME/デスクトップ" ]; then
    cp "$DESKTOP_FILE" "$HOME/デスクトップ/"
    chmod +x "$HOME/デスクトップ/patho-return.desktop"
    gio set "$HOME/デスクトップ/patho-return.desktop" metadata::trusted true 2>/dev/null || true
    echo "  -> デスクトップにショートカットを作成しました"
fi

echo ""
echo "========================================"
echo "  インストール完了！"
echo "========================================"
echo ""
echo "起動方法:"
echo "  1. デスクトップの「Patho Return」アイコンをダブルクリック"
echo "  2. または: $APP_DIR/start.sh を実行"
echo ""
echo "アプリURL: http://127.0.0.1:5000"
echo ""
