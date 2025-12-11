"""
ロギング設定
"""
import os
import logging
from logging.handlers import RotatingFileHandler
from config import Config


def setup_logger(app):
    """アプリケーションのロガーを設定"""
    # ログディレクトリの作成
    log_dir = os.path.dirname(Config.LOG_FILE)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir)

    # ファイルハンドラ
    file_handler = RotatingFileHandler(
        Config.LOG_FILE,
        maxBytes=Config.LOG_MAX_SIZE,
        backupCount=Config.LOG_BACKUP_COUNT,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    ))

    # コンソールハンドラ
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG if Config.DEBUG else logging.INFO)
    console_handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s'
    ))

    # アプリロガーに追加
    app.logger.addHandler(file_handler)
    app.logger.addHandler(console_handler)
    app.logger.setLevel(logging.DEBUG if Config.DEBUG else logging.INFO)

    return app.logger


def get_audit_logger():
    """監査ログ専用のロガーを取得"""
    logger = logging.getLogger('audit')

    if not logger.handlers:
        # 監査ログ専用ファイル
        audit_log_path = Config.LOG_FILE.replace('.log', '_audit.log')
        handler = RotatingFileHandler(
            audit_log_path,
            maxBytes=Config.LOG_MAX_SIZE,
            backupCount=Config.LOG_BACKUP_COUNT,
            encoding='utf-8'
        )
        handler.setFormatter(logging.Formatter(
            '%(asctime)s - AUDIT - %(message)s'
        ))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

    return logger
