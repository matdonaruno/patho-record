#!/bin/bash
# バーコード管理アプリ起動スクリプト

cd "$(dirname "$0")"

# 仮想環境がなければ作成
if [ ! -d "venv" ]; then
    echo "仮想環境を作成しています..."
    python3 -m venv venv
    source venv/bin/activate
    pip install --upgrade pip
    pip install -r requirements.txt
else
    source venv/bin/activate
fi

# 必要なディレクトリ作成
mkdir -p data logs backups

# アプリ起動
echo ""
echo "======================================"
echo "   Barcode Manager を起動しています..."
echo "======================================"
echo ""

python app.py
