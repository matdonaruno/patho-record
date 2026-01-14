"""
バックアップ機能
USB/NAS両対応
"""
import os
import shutil
import sqlite3
from datetime import datetime, timedelta
from config import Config
from models import AppSettings
import logging

logger = logging.getLogger(__name__)


def get_backup_type():
    """現在のバックアップタイプを取得（AppSettings優先）"""
    return AppSettings.get('backup_type', Config.BACKUP_TYPE)


class BackupManager:
    """バックアップ管理"""

    def __init__(self):
        self.backup_dir = Config.BACKUP_DIR
        self.retention_days = Config.BACKUP_RETENTION_DAYS
        self.db_path = os.path.join(Config.BASE_DIR, Config.DATABASE_PATH)
        self._storage_checker = None

    @property
    def storage_checker(self):
        """バックアップタイプに応じたストレージチェッカーを返す"""
        backup_type = get_backup_type()
        if backup_type == 'usb':
            from usb_check import USBChecker
            return USBChecker()
        else:
            from nas_check import NASChecker
            return NASChecker()

    def get_backup_type(self):
        """現在のバックアップタイプを取得"""
        return get_backup_type()

    def create_backup(self):
        """
        バックアップを作成
        Returns: (success: bool, message: str, backup_path: str or None)
        """
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_type = get_backup_type()

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

            # 外部ストレージへのコピー
            external_backup_path = None
            checker = self.storage_checker

            if checker.is_connected():
                if backup_type == 'usb':
                    external_backup_path = self._copy_to_usb(local_backup_path, backup_filename)
                else:
                    external_backup_path = self._copy_to_nas(local_backup_path, backup_filename)
            else:
                storage_name = 'USB' if backup_type == 'usb' else 'NAS'
                logger.warning(f"{storage_name}未接続: 外部バックアップをスキップ")

            # 古いバックアップの削除
            self._cleanup_old_backups()

            return True, "バックアップ完了", external_backup_path or local_backup_path

        except Exception as e:
            logger.error(f"バックアップ失敗: {str(e)}")
            return False, f"バックアップ失敗: {str(e)}", None

    def _copy_to_usb(self, local_path, filename):
        """USBにバックアップをコピー"""
        try:
            from usb_check import USBChecker
            usb_checker = USBChecker()
            mount_point = usb_checker.get_mount_point()
            if not mount_point:
                return None

            usb_backup_dir = os.path.join(mount_point, Config.USB_BACKUP_FOLDER)
            if not os.path.exists(usb_backup_dir):
                os.makedirs(usb_backup_dir)

            usb_backup_path = os.path.join(usb_backup_dir, filename)
            shutil.copy2(local_path, usb_backup_path)

            # ログファイルもコピー
            self._copy_logs_to_storage(usb_backup_dir)

            logger.info(f"USBバックアップ完了: {usb_backup_path}")
            return usb_backup_path

        except Exception as e:
            logger.error(f"USBコピー失敗: {str(e)}")
            return None

    def _copy_to_nas(self, local_path, filename):
        """NASにバックアップをコピー"""
        try:
            from nas_check import NASChecker
            nas_checker = NASChecker()
            nas_backup_dir = nas_checker.get_backup_dir()
            if not nas_backup_dir:
                return None

            nas_backup_path = os.path.join(nas_backup_dir, filename)
            shutil.copy2(local_path, nas_backup_path)

            # ログファイルもコピー
            self._copy_logs_to_storage(nas_backup_dir)

            logger.info(f"NASバックアップ完了: {nas_backup_path}")
            return nas_backup_path

        except Exception as e:
            logger.error(f"NASコピー失敗: {str(e)}")
            return None

    def _copy_logs_to_storage(self, backup_dir):
        """ログファイルを外部ストレージにコピー"""
        try:
            logs_dir = os.path.join(backup_dir, 'logs')
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
        """古いバックアップを削除（ローカルのみ、外部ストレージは永久保存）"""
        cutoff_date = datetime.now() - timedelta(days=self.retention_days)

        # ローカルバックアップのクリーンアップ
        self._cleanup_directory(self.backup_dir, cutoff_date)

        # 外部ストレージバックアップは削除しない（アーカイブとして永久保存）

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

    def _get_external_backup_dir(self):
        """外部ストレージのバックアップディレクトリを取得"""
        backup_type = get_backup_type()
        try:
            if backup_type == 'usb':
                from usb_check import USBChecker
                usb_checker = USBChecker()
                if usb_checker.is_connected():
                    mount_point = usb_checker.get_mount_point()
                    if mount_point:
                        return os.path.join(mount_point, Config.USB_BACKUP_FOLDER)
            else:
                from nas_check import NASChecker
                nas_checker = NASChecker()
                if nas_checker.is_connected():
                    return nas_checker.get_backup_dir()
        except Exception as e:
            logger.error(f"外部ストレージディレクトリ取得失敗: {str(e)}")
        return None

    def get_last_backup_info(self):
        """最後のバックアップ情報を取得"""
        backups = []
        backup_type = get_backup_type()

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

        # 外部ストレージバックアップを確認
        external_backup_dir = self._get_external_backup_dir()
        if external_backup_dir and os.path.exists(external_backup_dir):
            location = 'usb' if backup_type == 'usb' else 'nas'
            for filename in os.listdir(external_backup_dir):
                if filename.endswith('.db'):
                    filepath = os.path.join(external_backup_dir, filename)
                    backups.append({
                        'path': filepath,
                        'filename': filename,
                        'location': location,
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
        backup_type = get_backup_type()

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

        # 外部ストレージ
        external_backup_dir = self._get_external_backup_dir()
        if external_backup_dir and os.path.exists(external_backup_dir):
            location = 'usb' if backup_type == 'usb' else 'nas'
            for filename in os.listdir(external_backup_dir):
                if filename.endswith('.db'):
                    filepath = os.path.join(external_backup_dir, filename)
                    backups.append({
                        'filename': filename,
                        'location': location,
                        'size': os.path.getsize(filepath),
                        'modified': datetime.fromtimestamp(os.path.getmtime(filepath)).isoformat()
                    })

        backups.sort(key=lambda x: x['modified'], reverse=True)
        return backups


def check_storage_on_startup():
    """
    起動時のストレージチェック
    Returns: (success: bool, message: str, can_continue: bool)
    """
    backup_type = get_backup_type()

    if backup_type == 'usb':
        from usb_check import check_usb_on_startup
        return check_usb_on_startup()
    else:
        from nas_check import check_nas_on_startup
        return check_nas_on_startup()
