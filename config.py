"""
アプリケーション設定
"""
import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()


class Config:
    """基本設定"""
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')
    DEBUG = os.getenv('DEBUG', 'False').lower() == 'true'

    # データベース
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DATABASE_PATH = os.getenv('DATABASE_PATH', 'data/app.db')
    SQLALCHEMY_DATABASE_URI = f'sqlite:///{os.path.join(BASE_DIR, DATABASE_PATH)}'
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # セッション
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)

    # バックアップタイプ（'nas' または 'usb'）
    BACKUP_TYPE = os.getenv('BACKUP_TYPE', 'nas')

    # NAS バックアップ（SMB/CIFS）
    # デフォルトは空 → 設定画面から設定が必要
    NAS_HOST = os.getenv('NAS_HOST', '')
    NAS_SHARE = os.getenv('NAS_SHARE', '')
    NAS_USERNAME = os.getenv('NAS_USERNAME', '')  # 空=匿名アクセス
    NAS_PASSWORD = os.getenv('NAS_PASSWORD', '')
    NAS_MOUNT_POINT = os.getenv('NAS_MOUNT_POINT', '/mnt/nas_backup')
    NAS_REQUIRED = os.getenv('NAS_REQUIRED', 'False').lower() == 'true'
    NAS_BACKUP_FOLDER = os.getenv('NAS_BACKUP_FOLDER', 'barcode_app_backups')

    # USB バックアップ
    # デフォルトは空 → 設定画面から設定が必要
    USB_UUID = os.getenv('USB_UUID', '')
    USB_MOUNT_POINT = os.getenv('USB_MOUNT_POINT', '/media/usb_backup')
    USB_REQUIRED = os.getenv('USB_REQUIRED', 'False').lower() == 'true'
    USB_BACKUP_FOLDER = os.getenv('USB_BACKUP_FOLDER', 'barcode_app_backups')

    # バックアップ
    BACKUP_TIME = os.getenv('BACKUP_TIME', '02:00')
    BACKUP_RETENTION_DAYS = int(os.getenv('BACKUP_RETENTION_DAYS', '365'))
    BACKUP_DIR = os.path.join(BASE_DIR, 'backups')

    # 返却期限
    DEFAULT_RETURN_DAYS = int(os.getenv('DEFAULT_RETURN_DAYS', '14'))

    # ログ
    LOG_FILE = os.getenv('LOG_FILE', 'logs/app.log')
    LOG_MAX_SIZE = int(os.getenv('LOG_MAX_SIZE', '10485760'))  # 10MB
    LOG_BACKUP_COUNT = int(os.getenv('LOG_BACKUP_COUNT', '5'))

    # サーバー設定
    # プロダクション: 0.0.0.0 でネットワークアクセス可能
    # 開発: 127.0.0.1 でローカルのみ
    HOST = os.getenv('HOST', '0.0.0.0')
    PORT = int(os.getenv('PORT', '5000'))
