#!/bin/bash
# Patho Return - 起動スクリプト

# アプリケーションディレクトリに移動
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$APP_DIR"

# 既存のプロセスを確認
if lsof -ti:5000 &> /dev/null; then
    echo "アプリは既に起動しています"
    # ブラウザで開く
    xdg-open "http://127.0.0.1:5000" 2>/dev/null || sensible-browser "http://127.0.0.1:5000" 2>/dev/null &
    exit 0
fi

# アプリをバックグラウンドで起動（仮想環境のPythonを直接使用）
venv/bin/python app.py &
APP_PID=$!

# 起動を待つ
echo "Patho Return を起動中..."
sleep 3

# 起動確認
if kill -0 $APP_PID 2>/dev/null; then
    echo "起動完了！ブラウザを開いています..."
    # ブラウザで開く
    xdg-open "http://127.0.0.1:5000" 2>/dev/null || sensible-browser "http://127.0.0.1:5000" 2>/dev/null &
else
    echo "起動に失敗しました"
    # エラーダイアログを表示（zenityがある場合）
    if command -v zenity &> /dev/null; then
        zenity --error --text="Patho Return の起動に失敗しました。\nログを確認してください。" --title="エラー"
    fi
    exit 1
fi

# プロセスが終了するまで待機（ウィンドウを閉じるまで）
wait $APP_PID
