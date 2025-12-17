#!/usr/bin/env python3
"""
患者IDフィールド追加マイグレーション

実行方法:
    venv/bin/python migrate_add_patient_id.py
"""
import sqlite3
import os

def migrate():
    db_path = os.path.join(os.path.dirname(__file__), 'data', 'app.db')

    if not os.path.exists(db_path):
        print(f"エラー: データベースファイルが見つかりません: {db_path}")
        return False

    print(f"データベース: {db_path}")

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # patient_id列が既に存在するか確認
        cursor.execute("PRAGMA table_info(item_logs)")
        columns = [column[1] for column in cursor.fetchall()]

        if 'patient_id' in columns:
            print("✓ patient_id列は既に存在します。マイグレーション不要です。")
            conn.close()
            return True

        print("patient_id列を追加しています...")

        # patient_id列を追加
        cursor.execute("""
            ALTER TABLE item_logs
            ADD COLUMN patient_id TEXT
        """)

        # インデックスを作成
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS ix_item_logs_patient_id
            ON item_logs (patient_id)
        """)

        conn.commit()
        print("✓ patient_id列とインデックスを追加しました")

        # 確認
        cursor.execute("PRAGMA table_info(item_logs)")
        columns = [column[1] for column in cursor.fetchall()]

        if 'patient_id' in columns:
            print("✓ マイグレーション成功")
            conn.close()
            return True
        else:
            print("✗ マイグレーション失敗: patient_id列が見つかりません")
            conn.rollback()
            conn.close()
            return False

    except Exception as e:
        print(f"✗ エラーが発生しました: {e}")
        if 'conn' in locals():
            conn.rollback()
            conn.close()
        return False

if __name__ == '__main__':
    print("=" * 60)
    print("  患者IDフィールド追加マイグレーション")
    print("=" * 60)
    print()

    success = migrate()

    print()
    if success:
        print("マイグレーション完了")
    else:
        print("マイグレーション失敗")
    print()
