"""
データベースモデル
"""
from datetime import datetime, timedelta
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class User(db.Model):
    """ユーザー（操作者）"""
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=True)  # パスワード（任意）
    is_admin = db.Column(db.Boolean, default=False)  # 管理者フラグ
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)

    # リレーション
    item_logs = db.relationship('ItemLog', backref='scanned_by', lazy='dynamic')
    audit_logs = db.relationship('AuditLog', backref='user', lazy='dynamic')

    def set_password(self, password):
        """パスワードをハッシュ化して保存"""
        if password:
            self.password_hash = generate_password_hash(password)
        else:
            self.password_hash = None

    def check_password(self, password):
        """パスワードを検証"""
        if not self.password_hash:
            return True  # パスワード未設定の場合は常にOK
        return check_password_hash(self.password_hash, password)

    @property
    def has_password(self):
        """パスワードが設定されているか"""
        return self.password_hash is not None

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'is_admin': self.is_admin,
            'has_password': self.has_password,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'is_active': self.is_active
        }


class ItemLog(db.Model):
    """スキャン履歴"""
    __tablename__ = 'item_logs'

    id = db.Column(db.Integer, primary_key=True)
    barcode = db.Column(db.String(4096), nullable=True, index=True)  # 2次元バーコード対応、メモのみも可
    patient_id = db.Column(db.String(100), nullable=True, index=True)  # 患者ID
    quantity = db.Column(db.Integer, default=1)
    scanned_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    scanned_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    expected_return_date = db.Column(db.DateTime, nullable=True, index=True)
    preliminary_report = db.Column(db.Boolean, default=False, index=True)  # 仮報告済み
    preliminary_report_at = db.Column(db.DateTime, nullable=True)  # 仮報告日時
    returned = db.Column(db.Boolean, default=False, index=True)  # 結果返却済み
    returned_at = db.Column(db.DateTime, nullable=True)  # 結果返却日時
    block_quantity = db.Column(db.Integer, default=0)  # ブロック返却個数
    block_returned_at = db.Column(db.DateTime, nullable=True)  # ブロック返却日時
    slide_quantity = db.Column(db.Integer, default=0)  # スライド返却個数
    slide_returned_at = db.Column(db.DateTime, nullable=True)  # スライド返却日時
    completed = db.Column(db.Boolean, default=False, index=True)  # 完了フラグ（完了ボタン押下）
    completed_at = db.Column(db.DateTime, nullable=True)  # 完了日時
    notes = db.Column(db.Text, nullable=True)
    deleted_at = db.Column(db.DateTime, nullable=True, index=True)

    @property
    def block_returned(self):
        """ブロック返却済みかどうか（個数 > 0 なら返却済み）"""
        return self.block_quantity > 0

    @property
    def slide_returned(self):
        """スライド返却済みかどうか（個数 > 0 なら返却済み）"""
        return self.slide_quantity > 0

    @property
    def all_returned(self):
        """全て返却済みかどうか（完了ボタン押下で完了）"""
        return self.completed

    # 複合インデックス
    __table_args__ = (
        db.Index('ix_barcode_scannedat', 'barcode', 'scanned_at'),
        db.Index('ix_returned_deleted', 'returned', 'deleted_at'),
        db.Index('ix_completed_deleted', 'completed', 'deleted_at'),
    )

    @property
    def is_overdue(self):
        """期限超過かどうか"""
        if self.returned or self.deleted_at:
            return False
        if not self.expected_return_date:
            return False
        return datetime.utcnow() > self.expected_return_date

    @property
    def days_until_due(self):
        """返却期限までの日数（負の場合は超過日数）"""
        if not self.expected_return_date:
            return None
        delta = self.expected_return_date - datetime.utcnow()
        return delta.days

    def to_dict(self):
        return {
            'id': self.id,
            'barcode': self.barcode,
            'patient_id': self.patient_id,
            'quantity': self.quantity,
            'scanned_by_id': self.scanned_by_id,
            'scanned_by_name': self.scanned_by.name if self.scanned_by else None,
            'scanned_at': self.scanned_at.isoformat() + 'Z' if self.scanned_at else None,
            'expected_return_date': self.expected_return_date.isoformat() + 'Z' if self.expected_return_date else None,
            'preliminary_report': self.preliminary_report,
            'preliminary_report_at': self.preliminary_report_at.isoformat() + 'Z' if self.preliminary_report_at else None,
            'returned': self.returned,
            'returned_at': self.returned_at.isoformat() + 'Z' if self.returned_at else None,
            'block_quantity': self.block_quantity,
            'block_returned': self.block_returned,
            'block_returned_at': self.block_returned_at.isoformat() + 'Z' if self.block_returned_at else None,
            'slide_quantity': self.slide_quantity,
            'slide_returned': self.slide_returned,
            'slide_returned_at': self.slide_returned_at.isoformat() + 'Z' if self.slide_returned_at else None,
            'completed': self.completed,
            'completed_at': self.completed_at.isoformat() + 'Z' if self.completed_at else None,
            'all_returned': self.all_returned,
            'notes': self.notes,
            'deleted_at': self.deleted_at.isoformat() + 'Z' if self.deleted_at else None,
            'is_overdue': self.is_overdue,
            'days_until_due': self.days_until_due
        }


class AuditLog(db.Model):
    """監査ログ"""
    __tablename__ = 'audit_logs'

    id = db.Column(db.Integer, primary_key=True)
    action = db.Column(db.String(50), nullable=False)  # CREATE, UPDATE, DELETE
    table_name = db.Column(db.String(50), nullable=False)
    record_id = db.Column(db.Integer, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    old_value = db.Column(db.Text, nullable=True)
    new_value = db.Column(db.Text, nullable=True)

    __table_args__ = (
        db.Index('ix_audit_table_record', 'table_name', 'record_id'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'action': self.action,
            'table_name': self.table_name,
            'record_id': self.record_id,
            'user_id': self.user_id,
            'user_name': self.user.name if self.user else None,
            'timestamp': self.timestamp.isoformat() if self.timestamp else None,
            'old_value': self.old_value,
            'new_value': self.new_value
        }


class AppSettings(db.Model):
    """アプリケーション設定"""
    __tablename__ = 'app_settings'

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text, nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @classmethod
    def get(cls, key, default=None):
        """設定値を取得"""
        setting = cls.query.filter_by(key=key).first()
        return setting.value if setting else default

    @classmethod
    def set(cls, key, value):
        """設定値を保存"""
        setting = cls.query.filter_by(key=key).first()
        if setting:
            setting.value = value
        else:
            setting = cls(key=key, value=value)
            db.session.add(setting)
        db.session.commit()
        return setting
