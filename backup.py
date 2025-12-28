"""
バックアップ機能
"""
import os
import shutil
import sqlite3
from datetime import datetime, timedelta
from config import Config
from nas_check import NASChecker
import logging

logger = logging.getLogger(__name__)


class BackupManager:
    """バックアップ管理"""

    def __init__(self):
        self.nas_checker = NASChecker()
        self.backup_dir = Config.BACKUP_DIR
        self.retention_days = Config.BACKUP_RETENTION_DAYS
        self.db_path = os.path.join(Config.BASE_DIR, Config.DATABASE_PATH)

    def create_backup(self):
        """
        バックアップを作成
        Returns: (success: bool, message: str, backup_path: str or None)
        """
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

        # ローカルバックアップディレクトリの作成
        if not os.path.exists(self.backup_dir):
            os.makedirs(self.backup_dir)

        try:
            # SQLite の安全なバックアップ（backup API を使用）
            backup_filename = f'{timestamp}_app.db'
            local_backup_path = os.path.join(self.backup_dir, backup_filename)

            # sqlite3 の backup API を使用
            source_conn = sqlite3.connect(self.db_path)
            backup_conn = sqlite3.connect(local_backup_path)

            with backup_conn:
                source_conn.backup(backup_conn)

            source_conn.close()
            backup_conn.close()

            logger.info(f"ローカルバックアップ作成: {local_backup_path}")

            # NASへのコピー
            nas_backup_path = None
            if self.nas_checker.is_connected():
                nas_backup_path = self._copy_to_nas(local_backup_path, backup_filename)
            else:
                logger.warning("NAS未接続: NASバックアップをスキップ")

            # 古いバックアップの削除
            self._cleanup_old_backups()

            return True, "バックアップ完了", nas_backup_path or local_backup_path

        except Exception as e:
            logger.error(f"バックアップ失敗: {str(e)}")
            return False, f"バックアップ失敗: {str(e)}", None

    def _copy_to_nas(self, local_path, filename):
        """NASにバックアップをコピー"""
        try:
            nas_backup_dir = self.nas_checker.get_backup_dir()
            if not nas_backup_dir:
                return None

            nas_backup_path = os.path.join(nas_backup_dir, filename)
            shutil.copy2(local_path, nas_backup_path)

            # ログファイルもコピー
            self._copy_logs_to_nas(nas_backup_dir)

            logger.info(f"NASバックアップ完了: {nas_backup_path}")
            return nas_backup_path

        except Exception as e:
            logger.error(f"NASコピー失敗: {str(e)}")
            return None

    def _copy_logs_to_nas(self, nas_backup_dir):
        """ログファイルをNASにコピー"""
        try:
            logs_dir = os.path.join(nas_backup_dir, 'logs')
            if not os.path.exists(logs_dir):
                os.makedirs(logs_dir)

            source_logs_dir = os.path.dirname(Config.LOG_FILE)
            if os.path.exists(source_logs_dir):
                for log_file in os.listdir(source_logs_dir):
                    if log_file.endswith('.log'):
                        shutil.copy2(
                            os.path.join(source_logs_dir, log_file),
                            os.path.join(logs_dir, log_file)
                        )
        except Exception as e:
            logger.error(f"ログコピー失敗: {str(e)}")

    def _cleanup_old_backups(self):
        """古いバックアップを削除（ローカルのみ、NASは永久保存）"""
        cutoff_date = datetime.now() - timedelta(days=self.retention_days)

        # ローカルバックアップのクリーンアップ
        self._cleanup_directory(self.backup_dir, cutoff_date)

        # NASバックアップは削除しない（アーカイブとして永久保存）
        # 365日より古いデータはNASから参照可能

    def _cleanup_directory(self, directory, cutoff_date):
        """指定ディレクトリ内の古いバックアップを削除"""
        try:
            for filename in os.listdir(directory):
                if not filename.endswith('.db'):
                    continue

                filepath = os.path.join(directory, filename)

                # ファイル名から日付を抽出（YYYYMMDD_HHMMSS_app.db）
                try:
                    date_str = filename[:15]  # YYYYMMDD_HHMMSS
                    file_date = datetime.strptime(date_str, '%Y%m%d_%H%M%S')

                    if file_date < cutoff_date:
                        os.remove(filepath)
                        logger.info(f"古いバックアップを削除: {filename}")
                except ValueError:
                    continue
        except Exception as e:
            logger.error(f"クリーンアップ失敗: {str(e)}")

    def get_last_backup_info(self):
        """最後のバックアップ情報を取得"""
        backups = []

        # ローカルバックアップを確認
        if os.path.exists(self.backup_dir):
            for filename in os.listdir(self.backup_dir):
                if filename.endswith('.db'):
                    filepath = os.path.join(self.backup_dir, filename)
                    backups.append({
                        'path': filepath,
                        'filename': filename,
                        'location': 'local',
                        'modified': datetime.fromtimestamp(os.path.getmtime(filepath))
                    })

        # NASバックアップを確認
        if self.nas_checker.is_connected():
            nas_backup_dir = self.nas_checker.get_backup_dir()
            if nas_backup_dir and os.path.exists(nas_backup_dir):
                for filename in os.listdir(nas_backup_dir):
                    if filename.endswith('.db'):
                        filepath = os.path.join(nas_backup_dir, filename)
                        backups.append({
                            'path': filepath,
                            'filename': filename,
                            'location': 'nas',
                            'modified': datetime.fromtimestamp(os.path.getmtime(filepath))
                        })

        if not backups:
            return None

        # 最新のバックアップを返す
        backups.sort(key=lambda x: x['modified'], reverse=True)
        latest = backups[0]
        latest['modified'] = latest['modified'].isoformat()
        return latest

    def list_backups(self):
        """利用可能なバックアップ一覧を取得"""
        backups = []

        # ローカル
        if os.path.exists(self.backup_dir):
            for filename in os.listdir(self.backup_dir):
                if filename.endswith('.db'):
                    filepath = os.path.join(self.backup_dir, filename)
                    backups.append({
                        'filename': filename,
                        'location': 'local',
                        'size': os.path.getsize(filepath),
                        'modified': datetime.fromtimestamp(os.path.getmtime(filepath)).isoformat()
                    })

        # NAS
        if self.nas_checker.is_connected():
            nas_backup_dir = self.nas_checker.get_backup_dir()
            if nas_backup_dir and os.path.exists(nas_backup_dir):
                for filename in os.listdir(nas_backup_dir):
                    if filename.endswith('.db'):
                        filepath = os.path.join(nas_backup_dir, filename)
                        backups.append({
                            'filename': filename,
                            'location': 'nas',
                            'size': os.path.getsize(filepath),
                            'modified': datetime.fromtimestamp(os.path.getmtime(filepath)).isoformat()
                        })

        backups.sort(key=lambda x: x['modified'], reverse=True)
        return backups
