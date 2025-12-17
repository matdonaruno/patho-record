#!/bin/bash
# バーコード管理アプリ起動スクリプト

cd "$(dirname "$0")"

# 仮想環境がなければ作成
if [ ! -d "venv" ]; then
    echo "仮想環境を作成しています..."
    python3 -m venv venv
    venv/bin/pip install --upgrade pip
    venv/bin/pip install -r requirements.txt
fi

# 必要なディレクトリ作成
mkdir -p data logs backups

# アプリ起動
echo ""
echo "======================================"
echo "   Barcode Manager を起動しています..."
echo "======================================"
echo ""

venv/bin/python app.py
