"""
入力バリデーション
アプリケーション全体で使用する入力検証関数
"""
import re


# 入力制限値
LIMITS = {
    'barcode': 100,           # バーコード最大長
    'patient_id': 50,         # 患者ID最大長
    'notes': 500,             # メモ最大長
    'user_name': 50,          # ユーザー名最大長
    'password': 100,          # パスワード最大長
    'quantity_max': 9999,     # 数量最大値
    'return_days_max': 365,   # 返却期限最大日数
}


class ValidationError(Exception):
    """バリデーションエラー"""
    def __init__(self, message, field=None):
        self.message = message
        self.field = field
        super().__init__(message)


def validate_string(value, field_name, max_length, required=False, allow_none=True):
    """
    文字列バリデーション

    Args:
        value: 入力値
        field_name: フィールド名（エラーメッセージ用）
        max_length: 最大文字数
        required: 必須かどうか
        allow_none: None/空文字を許可するか

    Returns:
        str or None: 検証済みの値（strip済み）

    Raises:
        ValidationError: バリデーション失敗時
    """
    if value is None:
        if required:
            raise ValidationError(f'{field_name}を入力してください', field_name)
        return None

    if not isinstance(value, str):
        raise ValidationError(f'{field_name}は文字列で入力してください', field_name)

    # 前後の空白を除去
    value = value.strip()

    if not value:
        if required:
            raise ValidationError(f'{field_name}を入力してください', field_name)
        return None if allow_none else ''

    if len(value) > max_length:
        raise ValidationError(
            f'{field_name}は{max_length}文字以内で入力してください（現在{len(value)}文字）',
            field_name
        )

    return value


def validate_integer(value, field_name, min_val=0, max_val=None, default=0):
    """
    整数バリデーション

    Args:
        value: 入力値
        field_name: フィールド名
        min_val: 最小値
        max_val: 最大値
        default: デフォルト値

    Returns:
        int: 検証済みの値

    Raises:
        ValidationError: バリデーション失敗時
    """
    if value is None:
        return default

    try:
        value = int(value)
    except (ValueError, TypeError):
        raise ValidationError(f'{field_name}は数値で入力してください', field_name)

    if value < min_val:
        raise ValidationError(f'{field_name}は{min_val}以上で入力してください', field_name)

    if max_val is not None and value > max_val:
        raise ValidationError(f'{field_name}は{max_val}以下で入力してください', field_name)

    return value


def validate_barcode(value, required=False):
    """バーコードバリデーション"""
    value = validate_string(value, 'バーコード', LIMITS['barcode'], required=required)

    if value:
        # 制御文字を除去（改行、タブなど）
        value = re.sub(r'[\x00-\x1f\x7f]', '', value)

    return value


def validate_patient_id(value):
    """患者IDバリデーション"""
    value = validate_string(value, '患者ID', LIMITS['patient_id'])

    if value:
        # 制御文字を除去
        value = re.sub(r'[\x00-\x1f\x7f]', '', value)

    return value


def validate_notes(value):
    """メモバリデーション"""
    value = validate_string(value, 'メモ', LIMITS['notes'])

    if value:
        # 改行は許可するが、その他の制御文字は除去
        value = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', value)

    return value


def validate_user_name(value, required=True):
    """ユーザー名バリデーション"""
    return validate_string(value, 'ユーザー名', LIMITS['user_name'], required=required)


def validate_password(value, required=False):
    """パスワードバリデーション"""
    return validate_string(value, 'パスワード', LIMITS['password'], required=required)


def validate_quantity(value, field_name='個数', default=0):
    """数量バリデーション"""
    return validate_integer(
        value,
        field_name,
        min_val=0,
        max_val=LIMITS['quantity_max'],
        default=default
    )


def validate_return_days(value):
    """返却期限日数バリデーション"""
    return validate_integer(
        value,
        '返却期限日数',
        min_val=1,
        max_val=LIMITS['return_days_max'],
        default=14
    )


def sanitize_input(data):
    """
    入力データの基本サニタイズ

    Args:
        data: リクエストデータ（dict）

    Returns:
        dict: サニタイズ済みデータ
    """
    if not isinstance(data, dict):
        return {}

    sanitized = {}
    for key, value in data.items():
        if isinstance(value, str):
            # 文字列の場合、最大10000文字に制限
            sanitized[key] = value[:10000]
        else:
            sanitized[key] = value

    return sanitized
