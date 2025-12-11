"""
バックアップ機能
"""
import os
import shutil
import sqlite3
from datetime import datetime, timedelta
from config import Config
from usb_check import USBChecker
import logging

logger = logging.getLogger(__name__)


class BackupManager:
    """バックアップ管理"""

    def __init__(self):
        self.usb_checker = USBChecker()
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
            # SQLite の安全なバックアップ（VACUUM INTO を使用）
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

            # USBへのコピー
            usb_backup_path = None
            if self.usb_checker.is_connected():
                usb_backup_path = self._copy_to_usb(local_backup_path, backup_filename)
            else:
                logger.warning("USB未接続: USBバックアップをスキップ")

            # 古いバックアップの削除
            self._cleanup_old_backups()

            return True, "バックアップ完了", usb_backup_path or local_backup_path

        except Exception as e:
            logger.error(f"バックアップ失敗: {str(e)}")
            return False, f"バックアップ失敗: {str(e)}", None

    def _copy_to_usb(self, local_path, filename):
        """USBにバックアップをコピー"""
        try:
            mount_point = self.usb_checker.get_mount_point()
            if not mount_point:
                return None

            usb_backup_dir = os.path.join(mount_point, 'barcode_app_backups')
            if not os.path.exists(usb_backup_dir):
                os.makedirs(usb_backup_dir)

            usb_backup_path = os.path.join(usb_backup_dir, filename)
            shutil.copy2(local_path, usb_backup_path)

            # ログファイルもコピー
            self._copy_logs_to_usb(usb_backup_dir)

            logger.info(f"USBバックアップ完了: {usb_backup_path}")
            return usb_backup_path

        except Exception as e:
            logger.error(f"USBコピー失敗: {str(e)}")
            return None

    def _copy_logs_to_usb(self, usb_backup_dir):
        """ログファイルをUSBにコピー"""
        try:
            logs_dir = os.path.join(usb_backup_dir, 'logs')
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
        """古いバックアップを削除"""
        cutoff_date = datetime.now() - timedelta(days=self.retention_days)

        # ローカルバックアップのクリーンアップ
        self._cleanup_directory(self.backup_dir, cutoff_date)

        # USBバックアップのクリーンアップ
        if self.usb_checker.is_connected():
            mount_point = self.usb_checker.get_mount_point()
            if mount_point:
                usb_backup_dir = os.path.join(mount_point, 'barcode_app_backups')
                if os.path.exists(usb_backup_dir):
                    self._cleanup_directory(usb_backup_dir, cutoff_date)

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

        # USBバックアップを確認
        if self.usb_checker.is_connected():
            mount_point = self.usb_checker.get_mount_point()
            if mount_point:
                usb_backup_dir = os.path.join(mount_point, 'barcode_app_backups')
                if os.path.exists(usb_backup_dir):
                    for filename in os.listdir(usb_backup_dir):
                        if filename.endswith('.db'):
                            filepath = os.path.join(usb_backup_dir, filename)
                            backups.append({
                                'path': filepath,
                                'filename': filename,
                                'location': 'usb',
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

        # USB
        if self.usb_checker.is_connected():
            mount_point = self.usb_checker.get_mount_point()
            if mount_point:
                usb_backup_dir = os.path.join(mount_point, 'barcode_app_backups')
                if os.path.exists(usb_backup_dir):
                    for filename in os.listdir(usb_backup_dir):
                        if filename.endswith('.db'):
                            filepath = os.path.join(usb_backup_dir, filename)
                            backups.append({
                                'filename': filename,
                                'location': 'usb',
                                'size': os.path.getsize(filepath),
                                'modified': datetime.fromtimestamp(os.path.getmtime(filepath)).isoformat()
                            })

        backups.sort(key=lambda x: x['modified'], reverse=True)
        return backups
