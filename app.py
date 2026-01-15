"""
バーコード管理アプリ - メインアプリケーション
"""
import os
import json
import csv
import io
import subprocess
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from functools import wraps

# アプリバージョン
APP_VERSION = "1.2.0"
GITHUB_REPO = "matdonaruno/patho-record"

from flask import (
    Flask, render_template, request, jsonify, session,
    redirect, url_for, flash, Response
)

from config import Config
from models import db, User, ItemLog, AuditLog, AppSettings
from logger import setup_logger, get_audit_logger
from nas_check import NASChecker
from usb_check import USBChecker
from backup import BackupManager, check_storage_on_startup, get_backup_type
from validators import (
    ValidationError, validate_barcode, validate_patient_id,
    validate_notes, validate_quantity, validate_user_name,
    validate_password, validate_return_days, sanitize_input
)

# Flask アプリ初期化
app = Flask(__name__)
app.config.from_object(Config)

# データベース初期化
db.init_app(app)

# ロガー設定
logger = setup_logger(app)
audit_logger = get_audit_logger()

# バックアップマネージャー
backup_manager = BackupManager()


# ============================================================
# ユーティリティ
# ============================================================

def login_required(f):
    """ログイン必須デコレータ"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'error': 'ログインが必要です'}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def get_current_user():
    """現在のログインユーザーを取得"""
    user_id = session.get('user_id')
    if user_id:
        return User.query.get(user_id)
    return None


def create_audit_log(action, table_name, record_id, old_value=None, new_value=None):
    """監査ログを作成"""
    user = get_current_user()
    log = AuditLog(
        action=action,
        table_name=table_name,
        record_id=record_id,
        user_id=user.id if user else None,
        old_value=json.dumps(old_value, ensure_ascii=False) if old_value else None,
        new_value=json.dumps(new_value, ensure_ascii=False) if new_value else None
    )
    db.session.add(log)
    db.session.commit()

    # ファイルログにも出力
    audit_logger.info(
        f"ACTION={action} TABLE={table_name} RECORD={record_id} "
        f"USER={user.name if user else 'SYSTEM'} "
        f"OLD={old_value} NEW={new_value}"
    )


# ============================================================
# ルート: 認証
# ============================================================

@app.route('/')
def index():
    """ルート - ログインまたはメイン画面へリダイレクト"""
    if 'user_id' in session:
        return redirect(url_for('main'))
    return redirect(url_for('login'))


def get_storage_status():
    """現在のバックアップタイプに応じたストレージ状態を取得"""
    backup_type = get_backup_type()
    if backup_type == 'usb':
        status = USBChecker().get_status()
        status['type'] = 'usb'
        return status
    else:
        status = NASChecker().get_status()
        status['type'] = 'nas'
        return status


@app.route('/login')
def login():
    """ログイン画面（ユーザー選択）"""
    users = User.query.filter_by(is_active=True).order_by(User.name).all()
    storage_status = get_storage_status()
    return render_template('login.html', users=users, storage_status=storage_status)


@app.route('/login', methods=['POST'])
def do_login():
    """ログイン処理"""
    data = request.json if request.is_json else request.form
    user_id = data.get('user_id')
    password = data.get('password', '')

    if not user_id:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'error': 'ユーザーを選択してください'}), 400
        flash('ユーザーを選択してください', 'error')
        return redirect(url_for('login'))

    user = User.query.get(user_id)
    if not user or not user.is_active:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'error': '無効なユーザーです'}), 400
        flash('無効なユーザーです', 'error')
        return redirect(url_for('login'))

    # 管理者以外はストレージ接続チェック
    if not user.is_admin:
        backup_type = get_backup_type()
        storage_valid = False
        if backup_type == 'usb':
            storage_valid = USBChecker().is_usb_valid()
            error_msg = 'USBに接続できません。USBメモリを接続してから再度ログインしてください。'
        else:
            storage_valid = NASChecker().is_nas_valid()
            error_msg = 'NASに接続できません。ネットワーク接続を確認してから再度ログインしてください。'

        if not storage_valid:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'error': error_msg}), 403
            flash(error_msg, 'error')
            return redirect(url_for('login'))

    # パスワードチェック（パスワード設定済みの場合のみ）
    if user.has_password:
        if not password:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'error': 'パスワードを入力してください', 'needs_password': True}), 400
            flash('パスワードを入力してください', 'error')
            return redirect(url_for('login'))

        if not user.check_password(password):
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'error': 'パスワードが正しくありません'}), 400
            flash('パスワードが正しくありません', 'error')
            return redirect(url_for('login'))

    session['user_id'] = user.id
    session.permanent = True

    logger.info(f"ユーザーログイン: {user.name}")

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'success': True, 'redirect': url_for('main')})
    return redirect(url_for('main'))


@app.route('/logout')
def logout():
    """ログアウト"""
    user = get_current_user()
    if user:
        logger.info(f"ユーザーログアウト: {user.name}")
    session.clear()
    return redirect(url_for('login'))


@app.route('/register', methods=['POST'])
def register_user():
    """ログイン画面からのユーザー登録（パスワードなしユーザーのみ）"""
    data = request.json
    name = data.get('name', '').strip()

    if not name:
        return jsonify({'error': '名前を入力してください'}), 400

    # 重複チェック
    existing = User.query.filter_by(name=name).first()
    if existing:
        return jsonify({'error': 'この名前は既に使用されています'}), 400

    # パスワードなしの一般ユーザーとして作成
    user = User(name=name, is_admin=False)
    db.session.add(user)
    db.session.commit()

    logger.info(f"ユーザー登録: {name}")

    return jsonify({
        'success': True,
        'user': user.to_dict()
    })


# ============================================================
# ルート: メイン画面
# ============================================================

@app.route('/main')
@login_required
def main():
    """メイン画面（スキャン + 履歴）"""
    user = get_current_user()
    storage_status = get_storage_status()
    last_backup = backup_manager.get_last_backup_info()

    # 期限超過件数
    overdue_count = ItemLog.query.filter(
        ItemLog.completed == False,
        ItemLog.deleted_at == None,
        ItemLog.expected_return_date < datetime.utcnow()
    ).count()

    # 未完了件数
    unreturned_count = ItemLog.query.filter(
        ItemLog.completed == False,
        ItemLog.deleted_at == None
    ).count()

    return render_template(
        'main.html',
        user=user,
        storage_status=storage_status,
        last_backup=last_backup,
        overdue_count=overdue_count,
        unreturned_count=unreturned_count,
        default_return_days=Config.DEFAULT_RETURN_DAYS
    )


# ============================================================
# ルート: スキャン / 登録
# ============================================================

@app.route('/scan', methods=['POST'])
@login_required
def scan():
    """スキャン登録"""
    data = sanitize_input(request.json or {})
    user = get_current_user()

    try:
        barcode = validate_barcode(data.get('barcode'))
        patient_id = validate_patient_id(data.get('patient_id'))
        notes = validate_notes(data.get('notes'))
        quantity = validate_quantity(data.get('quantity', 1), '個数', default=1)
        block_quantity = validate_quantity(data.get('block_quantity', 0), 'ブロック数')
        slide_quantity = validate_quantity(data.get('slide_quantity', 0), 'スライド数')
        returned = bool(data.get('returned', False))
    except ValidationError as e:
        return jsonify({'error': e.message}), 400

    # バーコードまたはメモのいずれかが必要
    if not barcode and not notes:
        return jsonify({'error': 'バーコードまたはメモを入力してください'}), 400

    if quantity < 1:
        return jsonify({'error': '個数は1以上を指定してください'}), 400

    # 期待返却日を計算（設定値を使用）
    return_days_setting = AppSettings.get('return_days', str(Config.DEFAULT_RETURN_DAYS))
    return_days = int(return_days_setting)
    expected_return_date = datetime.utcnow() + timedelta(days=return_days)

    # 新規レコード作成
    item = ItemLog(
        barcode=barcode,
        patient_id=patient_id,
        quantity=quantity,
        scanned_by_id=user.id,
        expected_return_date=expected_return_date,
        returned=returned,
        block_quantity=block_quantity,
        slide_quantity=slide_quantity,
        notes=notes
    )

    db.session.add(item)
    db.session.commit()

    # 監査ログ
    create_audit_log('CREATE', 'item_logs', item.id, new_value=item.to_dict())

    logger.info(f"スキャン登録: バーコード={barcode}, 個数={quantity}, ユーザー={user.name}")

    return jsonify({
        'success': True,
        'item': item.to_dict()
    })


# ============================================================
# ルート: 履歴
# ============================================================

@app.route('/history')
@login_required
def history():
    """履歴取得"""
    # フィルタパラメータ
    filter_type = request.args.get('filter', 'all')
    search = request.args.get('search', '').strip()
    sort = request.args.get('sort', 'newest')
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 50))

    # ベースクエリ（削除されていないもの）
    query = ItemLog.query.filter(ItemLog.deleted_at == None)

    # フィルタ適用
    if filter_type == 'unreturned':
        query = query.filter(ItemLog.completed == False)
    elif filter_type == 'overdue':
        query = query.filter(
            ItemLog.completed == False,
            ItemLog.expected_return_date < datetime.utcnow()
        )
    elif filter_type == 'today':
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        query = query.filter(ItemLog.scanned_at >= today_start)
    elif filter_type == 'yesterday':
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        yesterday_start = today_start - timedelta(days=1)
        query = query.filter(
            ItemLog.scanned_at >= yesterday_start,
            ItemLog.scanned_at < today_start
        )
    elif filter_type == 'today_incomplete':
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        query = query.filter(
            ItemLog.scanned_at >= today_start,
            ItemLog.completed == False
        )
    elif filter_type == 'yesterday_incomplete':
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        yesterday_start = today_start - timedelta(days=1)
        query = query.filter(
            ItemLog.scanned_at >= yesterday_start,
            ItemLog.scanned_at < today_start,
            ItemLog.completed == False
        )
    elif filter_type == 'incomplete':
        # 未完了のもの（完了ボタンが押されていないもの）
        query = query.filter(ItemLog.completed == False)

    # 検索
    if search:
        query = query.filter(
            db.or_(
                ItemLog.barcode.contains(search),
                ItemLog.patient_id.contains(search),
                ItemLog.notes.contains(search)
            )
        )

    # ソート
    if sort == 'oldest':
        query = query.order_by(ItemLog.scanned_at.asc())
    elif sort == 'overdue':
        # 期限超過を優先（期限が古い順）
        query = query.order_by(ItemLog.expected_return_date.asc().nullslast())
    elif sort == 'barcode':
        query = query.order_by(ItemLog.barcode.asc(), ItemLog.scanned_at.desc())
    else:  # newest (default)
        query = query.order_by(ItemLog.scanned_at.desc())

    # ページネーション
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)

    return jsonify({
        'items': [item.to_dict() for item in pagination.items],
        'total': pagination.total,
        'pages': pagination.pages,
        'current_page': page,
        'has_next': pagination.has_next,
        'has_prev': pagination.has_prev
    })


# ============================================================
# ルート: 更新 / 削除
# ============================================================

@app.route('/item/<int:item_id>', methods=['GET'])
@login_required
def get_item(item_id):
    """単一アイテム取得"""
    item = ItemLog.query.get_or_404(item_id)
    return jsonify({'success': True, 'item': item.to_dict()})


@app.route('/update/<int:item_id>', methods=['POST'])
@login_required
def update_item(item_id):
    """履歴更新"""
    item = ItemLog.query.get_or_404(item_id)
    data = sanitize_input(request.json or {})

    # 変更前の値を保存
    old_value = item.to_dict()
    now = datetime.utcnow()

    try:
        # 更新可能なフィールド
        if 'quantity' in data:
            item.quantity = validate_quantity(data['quantity'], '個数', default=1)
        if 'returned' in data:
            new_returned = bool(data['returned'])
            # 結果返却が初めてチェックされた時にタイムスタンプを記録
            if new_returned and not item.returned:
                item.returned_at = now
            elif not new_returned:
                item.returned_at = None
            item.returned = new_returned
        if 'block_quantity' in data:
            new_block_quantity = validate_quantity(data['block_quantity'], 'ブロック数')
            # ブロック返却が初めて入力された時にタイムスタンプを記録
            if new_block_quantity > 0 and item.block_quantity == 0:
                item.block_returned_at = now
            elif new_block_quantity == 0:
                item.block_returned_at = None
            item.block_quantity = new_block_quantity
        if 'slide_quantity' in data:
            new_slide_quantity = validate_quantity(data['slide_quantity'], 'スライド数')
            # スライド返却が初めて入力された時にタイムスタンプを記録
            if new_slide_quantity > 0 and item.slide_quantity == 0:
                item.slide_returned_at = now
            elif new_slide_quantity == 0:
                item.slide_returned_at = None
            item.slide_quantity = new_slide_quantity
        if 'notes' in data:
            item.notes = validate_notes(data['notes'])
    except ValidationError as e:
        return jsonify({'error': e.message}), 400
    if 'expected_return_date' in data:
        if data['expected_return_date']:
            item.expected_return_date = datetime.fromisoformat(data['expected_return_date'])
        else:
            item.expected_return_date = None
    # 完了フラグの処理
    if 'completed' in data:
        new_completed = bool(data['completed'])
        if new_completed and not item.completed:
            item.completed_at = now
        elif not new_completed:
            item.completed_at = None
        item.completed = new_completed

    db.session.commit()

    # 監査ログ
    create_audit_log('UPDATE', 'item_logs', item.id, old_value=old_value, new_value=item.to_dict())

    user = get_current_user()
    logger.info(f"履歴更新: ID={item_id}, ユーザー={user.name}")

    return jsonify({
        'success': True,
        'item': item.to_dict()
    })


@app.route('/delete/<int:item_id>', methods=['POST'])
@login_required
def delete_item(item_id):
    """履歴削除（ソフトデリート）"""
    item = ItemLog.query.get_or_404(item_id)

    # 変更前の値を保存
    old_value = item.to_dict()

    # ソフトデリート
    item.deleted_at = datetime.utcnow()
    db.session.commit()

    # 監査ログ
    create_audit_log('DELETE', 'item_logs', item.id, old_value=old_value)

    user = get_current_user()
    logger.info(f"履歴削除: ID={item_id}, ユーザー={user.name}")

    return jsonify({'success': True})


# ============================================================
# ルート: ユーザー管理
# ============================================================

@app.route('/users')
@login_required
def list_users():
    """ユーザー一覧"""
    users = User.query.order_by(User.name).all()
    return jsonify({
        'users': [user.to_dict() for user in users]
    })


@app.route('/users', methods=['POST'])
@login_required
def create_user():
    """ユーザー作成"""
    data = sanitize_input(request.json or {})

    try:
        name = validate_user_name(data.get('name'), required=True)
        password = validate_password(data.get('password'))
    except ValidationError as e:
        return jsonify({'error': e.message}), 400

    is_admin = bool(data.get('is_admin', False))

    # 重複チェック
    existing = User.query.filter_by(name=name).first()
    if existing:
        return jsonify({'error': 'この名前は既に使用されています'}), 400

    user = User(name=name, is_admin=is_admin)
    if password:
        user.set_password(password)

    db.session.add(user)
    db.session.commit()

    # 監査ログ
    create_audit_log('CREATE', 'users', user.id, new_value=user.to_dict())

    logger.info(f"ユーザー作成: {name}")

    return jsonify({
        'success': True,
        'user': user.to_dict()
    })


@app.route('/users/<int:user_id>', methods=['POST'])
@login_required
def update_user(user_id):
    """ユーザー更新"""
    user = User.query.get_or_404(user_id)
    data = sanitize_input(request.json or {})

    old_value = user.to_dict()

    try:
        if 'name' in data:
            name = validate_user_name(data.get('name'), required=True)
            # 重複チェック
            existing = User.query.filter(User.name == name, User.id != user_id).first()
            if existing:
                return jsonify({'error': 'この名前は既に使用されています'}), 400
            user.name = name

        if 'is_active' in data:
            user.is_active = bool(data['is_active'])

        if 'is_admin' in data:
            user.is_admin = bool(data['is_admin'])

        # パスワード変更
        if 'password' in data:
            password = validate_password(data.get('password'))
            if password:
                user.set_password(password)
            elif data.get('clear_password'):
                user.password_hash = None  # パスワードをクリア
    except ValidationError as e:
        return jsonify({'error': e.message}), 400

    db.session.commit()

    # 監査ログ
    create_audit_log('UPDATE', 'users', user.id, old_value=old_value, new_value=user.to_dict())

    return jsonify({
        'success': True,
        'user': user.to_dict()
    })


@app.route('/users/<int:user_id>/delete', methods=['POST'])
@login_required
def delete_user(user_id):
    """ユーザー削除（ソフトデリート）"""
    # 管理者権限チェック
    if not current_user.is_admin:
        return jsonify({'error': '管理者権限が必要です'}), 403

    # 削除対象ユーザー取得
    user = User.query.get_or_404(user_id)

    # 自分自身の削除を防止
    if user.id == current_user.id:
        return jsonify({'error': '自分自身は削除できません'}), 400

    # 既に削除済みか確認
    if not user.is_active:
        return jsonify({'error': 'このユーザーは既に削除されています'}), 400

    # 最後の管理者の削除を防止
    if user.is_admin:
        active_admin_count = User.query.filter_by(is_admin=True, is_active=True).count()
        if active_admin_count <= 1:
            return jsonify({'error': '最後の管理者ユーザーは削除できません'}), 400

    old_value = user.to_dict()

    # ソフトデリート: is_activeをFalseに設定
    user.is_active = False
    db.session.commit()

    # 監査ログ
    create_audit_log('DELETE', 'users', user.id, old_value=old_value, new_value=user.to_dict())

    return jsonify({
        'success': True,
        'message': f'ユーザー「{user.name}」を削除しました'
    })


# ============================================================
# ルート: エクスポート
# ============================================================

@app.route('/export/csv')
@login_required
def export_csv():
    """CSV エクスポート"""
    # フィルタパラメータ
    filter_type = request.args.get('filter', 'all')
    search = request.args.get('search', '').strip()

    # クエリ構築
    query = ItemLog.query.filter(ItemLog.deleted_at == None)

    if filter_type == 'unreturned':
        query = query.filter(ItemLog.completed == False)
    elif filter_type == 'overdue':
        query = query.filter(
            ItemLog.completed == False,
            ItemLog.expected_return_date < datetime.utcnow()
        )

    if search:
        query = query.filter(
            db.or_(
                ItemLog.barcode.contains(search),
                ItemLog.notes.contains(search)
            )
        )

    items = query.order_by(ItemLog.scanned_at.desc()).all()

    # CSV 生成
    output = io.StringIO()
    writer = csv.writer(output)

    # ヘッダー
    writer.writerow([
        'ID', 'バーコード', '個数', 'スキャン者', 'スキャン日時',
        'ブロック個数', 'スライド個数', 'メモ'
    ])

    # データ
    for item in items:
        writer.writerow([
            item.id,
            item.barcode,
            item.quantity,
            item.scanned_by.name if item.scanned_by else '',
            item.scanned_at.strftime('%Y-%m-%d %H:%M:%S') if item.scanned_at else '',
            item.block_quantity or '',
            item.slide_quantity or '',
            item.notes or ''
        ])

    output.seek(0)

    # ファイル名
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f'barcode_export_{timestamp}.csv'

    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={
            'Content-Disposition': f'attachment; filename={filename}',
            'Content-Type': 'text/csv; charset=utf-8-sig'
        }
    )


# ============================================================
# ルート: バックアップ
# ============================================================

@app.route('/backup/status')
@login_required
def backup_status():
    """バックアップ状態"""
    storage_status = get_storage_status()
    last_backup = backup_manager.get_last_backup_info()
    backups = backup_manager.list_backups()

    return jsonify({
        'storage': storage_status,
        'last_backup': last_backup,
        'backups': backups[:10]  # 最新10件
    })


@app.route('/backup/run', methods=['POST'])
@login_required
def run_backup():
    """手動バックアップ実行"""
    success, message, path = backup_manager.create_backup()
    return jsonify({
        'success': success,
        'message': message,
        'path': path
    })


# ============================================================
# ルート: 監査ログ
# ============================================================

@app.route('/audit-logs')
@login_required
def audit_logs():
    """監査ログ取得"""
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 50))

    query = AuditLog.query.order_by(AuditLog.timestamp.desc())
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)

    return jsonify({
        'logs': [log.to_dict() for log in pagination.items],
        'total': pagination.total,
        'pages': pagination.pages,
        'current_page': page
    })


# ============================================================
# ルート: 設定
# ============================================================

@app.route('/settings')
@login_required
def settings():
    """設定画面"""
    user = get_current_user()
    storage_status = get_storage_status()
    users = User.query.order_by(User.name).all()

    # 返却期限日数を取得（デフォルトはConfig値）
    return_days = AppSettings.get('return_days', str(Config.DEFAULT_RETURN_DAYS))
    # 現在のバックアップタイプを取得
    backup_type = get_backup_type()

    return render_template(
        'settings.html',
        user=user,
        storage_status=storage_status,
        users=users,
        config=Config,
        return_days=int(return_days),
        backup_type=backup_type
    )


@app.route('/settings/return-days', methods=['GET', 'POST'])
@login_required
def settings_return_days():
    """返却期限日数の取得・更新"""
    if request.method == 'GET':
        return_days = AppSettings.get('return_days', str(Config.DEFAULT_RETURN_DAYS))
        return jsonify({'return_days': int(return_days)})

    data = sanitize_input(request.json or {})

    try:
        days = validate_return_days(data.get('days'))
    except ValidationError as e:
        return jsonify({'error': e.message}), 400

    AppSettings.set('return_days', str(days))

    user = get_current_user()
    logger.info(f"返却期限日数を変更: {days}日 (ユーザー: {user.name})")

    return jsonify({
        'success': True,
        'return_days': days
    })


@app.route('/settings/nas-config', methods=['GET', 'POST'])
@login_required
def settings_nas_config():
    """NAS設定の取得・更新"""
    if request.method == 'GET':
        return jsonify({
            'nas_host': Config.NAS_HOST,
            'nas_share': Config.NAS_SHARE,
            'nas_mount_point': Config.NAS_MOUNT_POINT,
            'nas_required': Config.NAS_REQUIRED
        })

    # 設定変更は環境変数または.envファイルで行うため、
    # ここでは接続テストのみ実行可能
    user = get_current_user()
    logger.info(f"NAS設定を確認 (ユーザー: {user.name})")

    return jsonify({
        'success': True,
        'message': 'NAS設定は.envファイルで変更してください'
    })


@app.route('/settings/storage-status')
@login_required
def get_storage_status_api():
    """ストレージ接続状態を取得"""
    backup_type = get_backup_type()

    if backup_type == 'usb':
        checker = USBChecker()
        status = checker.get_status()
        status['type'] = 'usb'
        connected = checker.is_connected()
        return jsonify({
            'status': status,
            'connected': connected,
            'backup_type': 'usb'
        })
    else:
        checker = NASChecker()
        status = checker.get_status()
        status['type'] = 'nas'
        reachable = checker.check_nas_reachable()
        return jsonify({
            'status': status,
            'reachable': reachable,
            'backup_type': 'nas'
        })


@app.route('/settings/nas-status')
@login_required
def get_nas_status():
    """NAS接続状態を取得（後方互換性のため維持）"""
    checker = NASChecker()
    status = checker.get_status()
    reachable = checker.check_nas_reachable()

    return jsonify({
        'status': status,
        'reachable': reachable
    })


@app.route('/settings/backup-type', methods=['GET', 'POST'])
@login_required
def settings_backup_type():
    """バックアップタイプの取得・更新"""
    if request.method == 'GET':
        backup_type = get_backup_type()
        return jsonify({'backup_type': backup_type})

    data = sanitize_input(request.json or {})
    new_type = data.get('backup_type', '').lower()

    if new_type not in ('usb', 'nas'):
        return jsonify({'error': 'バックアップタイプは usb または nas を指定してください'}), 400

    AppSettings.set('backup_type', new_type)

    user = get_current_user()
    logger.info(f"バックアップタイプを変更: {new_type} (ユーザー: {user.name})")

    return jsonify({
        'success': True,
        'backup_type': new_type
    })


@app.route('/settings/storage-config', methods=['GET', 'POST'])
@login_required
def settings_storage_config():
    """ストレージ設定の取得・更新（管理者のみ）"""
    user = get_current_user()
    if not user.is_admin:
        return jsonify({'error': '管理者権限が必要です'}), 403

    if request.method == 'GET':
        # 現在の設定を取得
        return jsonify({
            'nas': {
                'host': AppSettings.get('nas_host', Config.NAS_HOST or ''),
                'share': AppSettings.get('nas_share', Config.NAS_SHARE or ''),
                'username': AppSettings.get('nas_username', Config.NAS_USERNAME or ''),
                'password': '***' if AppSettings.get('nas_password', Config.NAS_PASSWORD) else '',
                'mount_point': AppSettings.get('nas_mount_point', Config.NAS_MOUNT_POINT or '/mnt/nas_backup'),
                'backup_folder': AppSettings.get('nas_backup_folder', Config.NAS_BACKUP_FOLDER or 'barcode_app_backups'),
            },
            'usb': {
                'uuid': AppSettings.get('usb_uuid', Config.USB_UUID or ''),
                'mount_point': AppSettings.get('usb_mount_point', Config.USB_MOUNT_POINT or '/media/usb_backup'),
                'backup_folder': AppSettings.get('usb_backup_folder', Config.USB_BACKUP_FOLDER or 'barcode_app_backups'),
            }
        })

    # POST: 設定を保存
    data = sanitize_input(request.json or {})
    storage_type = data.get('type')

    if storage_type == 'nas':
        # NAS設定を保存
        if 'host' in data:
            AppSettings.set('nas_host', data['host'])
        if 'share' in data:
            AppSettings.set('nas_share', data['share'])
        if 'username' in data:
            AppSettings.set('nas_username', data['username'])
        if 'password' in data and data['password'] != '***':
            AppSettings.set('nas_password', data['password'])
        if 'mount_point' in data:
            AppSettings.set('nas_mount_point', data['mount_point'])
        if 'backup_folder' in data:
            AppSettings.set('nas_backup_folder', data['backup_folder'])

        logger.info(f"NAS設定を更新 (ユーザー: {user.name})")

    elif storage_type == 'usb':
        # USB設定を保存
        if 'uuid' in data:
            AppSettings.set('usb_uuid', data['uuid'])
        if 'mount_point' in data:
            AppSettings.set('usb_mount_point', data['mount_point'])
        if 'backup_folder' in data:
            AppSettings.set('usb_backup_folder', data['backup_folder'])

        logger.info(f"USB設定を更新 (ユーザー: {user.name})")

    else:
        return jsonify({'error': 'typeは nas または usb を指定してください'}), 400

    return jsonify({'success': True})


@app.route('/settings/nas-detect', methods=['POST'])
@login_required
def nas_auto_detect():
    """NASのIPアドレスから共有フォルダを自動検出し、設定を自動保存"""
    user = get_current_user()
    if not user.is_admin:
        return jsonify({'error': '管理者権限が必要です'}), 403

    data = sanitize_input(request.json or {})
    host = data.get('host', '').strip()

    if not host:
        return jsonify({'error': 'IPアドレスを入力してください'}), 400

    import subprocess

    # 1. ping で到達確認
    try:
        result = subprocess.run(
            ['ping', '-c', '1', '-W', '2', host],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return jsonify({'error': f'{host} に接続できません。IPアドレスを確認してください'}), 400
    except Exception:
        return jsonify({'error': f'{host} への接続がタイムアウトしました'}), 400

    # 2. smbclient で共有フォルダを検出（SMB1/SMB2/SMB3対応）
    shares = []
    smb_error = None
    try:
        # Buffalo NAS等のSMB1対応のため、複数のプロトコルを試行
        smb_protocols = [
            ['smbclient', '-L', f'//{host}', '-N', '-m', 'NT1'],  # SMB1
            ['smbclient', '-L', f'//{host}', '-N', '-m', 'SMB2'],  # SMB2
            ['smbclient', '-L', f'//{host}', '-N'],  # デフォルト
        ]

        for cmd in smb_protocols:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode == 0 or 'Sharename' in result.stdout:
                # 共有フォルダをパース
                in_share_section = False
                for line in result.stdout.split('\n'):
                    if 'Sharename' in line and 'Type' in line:
                        in_share_section = True
                        continue
                    if in_share_section:
                        if line.strip() == '' or 'Reconnecting' in line:
                            break
                        parts = line.split()
                        if len(parts) >= 2 and parts[1] == 'Disk':
                            share_name = parts[0]
                            # システム共有を除外
                            if not share_name.startswith('IPC') and not share_name.endswith('$'):
                                shares.append(share_name)
                if shares:
                    break
            smb_error = result.stderr
    except subprocess.TimeoutExpired:
        return jsonify({'error': 'NASからの応答がタイムアウトしました'}), 400
    except FileNotFoundError:
        # smbclient がない場合はデフォルト値を使用
        shares = ['share']
    except Exception as e:
        return jsonify({'error': f'共有フォルダの検出に失敗: {str(e)}'}), 400

    # 共有フォルダが見つからない場合
    if not shares:
        error_msg = 'NASの共有フォルダが見つかりません。'
        if smb_error:
            error_msg += f' ({smb_error.strip()[:100]})'
        return jsonify({'error': error_msg}), 400

    # 3. 最初の共有フォルダを使用（なければ 'share'）
    detected_share = shares[0] if shares else 'share'

    # 4. 設定を自動保存
    mount_point = '/mnt/nas_backup'
    AppSettings.set('nas_host', host)
    AppSettings.set('nas_share', detected_share)
    AppSettings.set('nas_mount_point', mount_point)
    AppSettings.set('nas_backup_folder', 'barcode_app_backups')
    # 認証情報は空（匿名アクセス）
    if not AppSettings.get('nas_username'):
        AppSettings.set('nas_username', '')
    if not AppSettings.get('nas_password'):
        AppSettings.set('nas_password', '')

    logger.info(f"NAS設定を自動検出・保存: {host}/{detected_share} (ユーザー: {user.name})")

    # 5. マウント試行（SMB1/SMB2/SMB3フォールバック）
    mount_success = False
    mount_error = None
    try:
        # マウントポイントディレクトリを作成
        if not os.path.exists(mount_point):
            os.makedirs(mount_point, exist_ok=True)

        smb_path = f"//{host}/{detected_share}"
        smb_versions = ['1.0', '2.0', '2.1', '3.0']

        for vers in smb_versions:
            mount_options = f"guest,uid=1000,gid=1000,iocharset=utf8,vers={vers}"
            result = subprocess.run(
                ['sudo', 'mount', '-t', 'cifs', smb_path, mount_point, '-o', mount_options],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode == 0:
                mount_success = True
                logger.info(f"NASマウント成功 (SMB {vers}): {smb_path}")
                break
            else:
                mount_error = result.stderr.strip()
                logger.debug(f"SMB {vers} マウント失敗: {mount_error}")
    except subprocess.TimeoutExpired:
        mount_error = "マウントタイムアウト"
    except Exception as e:
        mount_error = str(e)

    # 結果を返す
    response_data = {
        'success': True,
        'detected': {
            'host': host,
            'share': detected_share,
            'shares_available': shares,
            'mount_point': mount_point,
            'backup_folder': 'barcode_app_backups'
        },
        'mount_success': mount_success
    }

    if mount_success:
        response_data['message'] = f'NAS設定完了: {host}/{detected_share} (マウント成功)'
    else:
        response_data['message'] = f'NAS検出完了: {host}/{detected_share}'
        response_data['mount_warning'] = f'自動マウントに失敗しました。手動でfstab設定が必要な場合があります。'
        if mount_error:
            response_data['mount_error'] = mount_error[:200]

    return jsonify(response_data)


# ============================================================
# バックアップ検証・診断機能
# ============================================================

@app.route('/settings/backup-diagnostics')
@login_required
def backup_diagnostics():
    """バックアップシステムの完全診断を実行"""
    user = get_current_user()
    if not user.is_admin:
        return jsonify({'error': '管理者権限が必要です'}), 403

    backup_type = get_backup_type()

    if backup_type == 'nas':
        checker = NASChecker()
        results = checker.run_full_diagnostics()
    else:
        # USB用の診断
        checker = USBChecker()
        results = {
            'timestamp': datetime.now().isoformat(),
            'tests': [
                {
                    'name': 'USB接続確認',
                    'status': 'ok' if checker.is_connected() else 'error',
                    'detail': checker.get_mount_point() or 'USB未接続'
                },
                {
                    'name': '書き込みテスト',
                    'status': 'ok' if checker.is_usb_valid() else 'error',
                    'detail': '書き込み可能' if checker.is_usb_valid() else '書き込み不可'
                }
            ],
            'overall': 'ok' if checker.is_usb_valid() else 'error'
        }

    results['backup_type'] = backup_type
    return jsonify(results)


@app.route('/settings/backup-verify', methods=['POST'])
@login_required
def backup_verify():
    """最新バックアップの整合性を検証"""
    user = get_current_user()
    if not user.is_admin:
        return jsonify({'error': '管理者権限が必要です'}), 403

    # 最新のローカルバックアップを取得
    local_backups = []
    if os.path.exists(Config.BACKUP_DIR):
        for f in os.listdir(Config.BACKUP_DIR):
            if f.endswith('.db'):
                local_backups.append(os.path.join(Config.BACKUP_DIR, f))
    local_backups.sort(reverse=True)

    if not local_backups:
        return jsonify({'success': False, 'error': 'ローカルバックアップが見つかりません'})

    latest_local = local_backups[0]
    filename = os.path.basename(latest_local)

    # バックアップタイプに応じてリモートパスを取得
    backup_type = get_backup_type()
    if backup_type == 'nas':
        checker = NASChecker()
        backup_dir = checker.get_backup_dir()
    else:
        checker = USBChecker()
        mount_point = checker.get_mount_point()
        backup_dir = os.path.join(mount_point, Config.USB_BACKUP_FOLDER) if mount_point else None

    if not backup_dir:
        return jsonify({'success': False, 'error': 'リモートストレージに接続できません'})

    remote_path = os.path.join(backup_dir, filename)

    # 検証実行
    if backup_type == 'nas':
        result = checker.verify_backup(latest_local, remote_path)
    else:
        # USB用の簡易検証
        import hashlib
        try:
            if not os.path.exists(remote_path):
                result = {'success': False, 'error': 'リモートファイルが存在しません'}
            else:
                local_size = os.path.getsize(latest_local)
                remote_size = os.path.getsize(remote_path)
                if local_size == remote_size:
                    result = {'success': True, 'message': 'バックアップ検証成功', 'local_size': local_size, 'remote_size': remote_size}
                else:
                    result = {'success': False, 'error': f'サイズ不一致: ローカル={local_size}, リモート={remote_size}'}
        except Exception as e:
            result = {'success': False, 'error': str(e)}

    result['filename'] = filename
    result['backup_type'] = backup_type
    return jsonify(result)


@app.route('/settings/insert-demo-data', methods=['POST'])
@login_required
def insert_demo_data():
    """デモデータを挿入してバックアップをテスト"""
    user = get_current_user()
    if not user.is_admin:
        return jsonify({'error': '管理者権限が必要です'}), 403

    try:
        # デモデータを挿入
        demo_items = []
        timestamp = datetime.now().strftime('%H%M%S')

        for i in range(3):
            barcode = f"DEMO-{timestamp}-{i+1:03d}"
            item = ItemLog(
                barcode=barcode,
                patient_id=f"P{timestamp}{i}",
                notes=f"バックアップテスト用デモデータ #{i+1}",
                user_id=user.id,
                quantity=1,
                expected_return_date=datetime.utcnow() + timedelta(days=14)
            )
            db.session.add(item)
            demo_items.append(barcode)

        db.session.commit()
        logger.info(f"デモデータ挿入: {len(demo_items)}件 (ユーザー: {user.name})")

        # バックアップを実行
        success, message, path = backup_manager.create_backup()

        if success:
            return jsonify({
                'success': True,
                'demo_items': demo_items,
                'backup_path': path,
                'message': f'{len(demo_items)}件のデモデータを挿入し、バックアップを作成しました'
            })
        else:
            return jsonify({
                'success': False,
                'demo_items': demo_items,
                'error': f'デモデータは挿入しましたが、バックアップに失敗しました: {message}'
            })

    except Exception as e:
        db.session.rollback()
        logger.error(f"デモデータ挿入失敗: {e}")
        return jsonify({'success': False, 'error': str(e)})


@app.route('/settings/cleanup-demo-data', methods=['POST'])
@login_required
def cleanup_demo_data():
    """デモデータを削除"""
    user = get_current_user()
    if not user.is_admin:
        return jsonify({'error': '管理者権限が必要です'}), 403

    try:
        # DEMO-で始まるバーコードを持つレコードを削除
        demo_items = ItemLog.query.filter(ItemLog.barcode.like('DEMO-%')).all()
        count = len(demo_items)

        for item in demo_items:
            db.session.delete(item)

        db.session.commit()
        logger.info(f"デモデータ削除: {count}件 (ユーザー: {user.name})")

        return jsonify({
            'success': True,
            'deleted_count': count,
            'message': f'{count}件のデモデータを削除しました'
        })

    except Exception as e:
        db.session.rollback()
        logger.error(f"デモデータ削除失敗: {e}")
        return jsonify({'success': False, 'error': str(e)})


@app.route('/settings/fstab-entry')
@login_required
def get_fstab_entry():
    """fstabエントリを生成（NAS直結運用用）"""
    user = get_current_user()
    if not user.is_admin:
        return jsonify({'error': '管理者権限が必要です'}), 403

    from nas_check import generate_fstab_entry
    entry = generate_fstab_entry()

    if entry:
        return jsonify({
            'success': True,
            'entry': entry,
            'instructions': [
                '1. sudo nano /etc/fstab を実行',
                '2. 以下の行を追加:',
                entry,
                '3. 保存して終了',
                '4. sudo mount -a でテスト'
            ]
        })
    else:
        return jsonify({
            'success': False,
            'error': 'NAS設定がありません'
        })


@app.route('/settings/fstab-add', methods=['POST'])
@login_required
def add_fstab_entry():
    """fstabエントリを自動追加してマウント"""
    user = get_current_user()
    if not user.is_admin:
        return jsonify({'error': '管理者権限が必要です'}), 403

    from nas_check import generate_fstab_entry, NASChecker
    entry = generate_fstab_entry()

    if not entry:
        return jsonify({'success': False, 'error': 'NAS設定がありません'})

    checker = NASChecker()
    mount_point = checker.mount_point

    try:
        # 1. 既存のfstabを読み込み
        with open('/etc/fstab', 'r') as f:
            fstab_content = f.read()

        # 2. 既にエントリが存在するか確認
        if mount_point in fstab_content:
            # 既存エントリがある場合はマウントのみ試行
            result = subprocess.run(['sudo', 'mount', '-a'], capture_output=True, text=True, timeout=30)
            if result.returncode == 0 and os.path.ismount(mount_point):
                return jsonify({
                    'success': True,
                    'message': 'fstab設定は既に存在します。マウント完了しました。',
                    'already_exists': True
                })
            else:
                return jsonify({
                    'success': False,
                    'error': f'fstab設定済みですがマウント失敗: {result.stderr.strip()[:100]}'
                })

        # 3. fstabにエントリを追加
        add_result = subprocess.run(
            ['sudo', 'bash', '-c', f'echo "{entry}" >> /etc/fstab'],
            capture_output=True, text=True, timeout=10
        )
        if add_result.returncode != 0:
            return jsonify({
                'success': False,
                'error': f'fstab追加失敗: {add_result.stderr.strip()[:100]}'
            })

        logger.info(f"fstabエントリ追加: {entry} (ユーザー: {user.name})")

        # 4. マウントポイントディレクトリを作成
        if not os.path.exists(mount_point):
            subprocess.run(['sudo', 'mkdir', '-p', mount_point], capture_output=True, timeout=5)

        # 5. mount -a でマウント
        mount_result = subprocess.run(['sudo', 'mount', '-a'], capture_output=True, text=True, timeout=30)

        if mount_result.returncode == 0 and os.path.ismount(mount_point):
            return jsonify({
                'success': True,
                'message': 'fstab設定を追加し、NASをマウントしました。',
                'entry': entry
            })
        else:
            return jsonify({
                'success': True,
                'message': 'fstab設定を追加しました。再起動後に自動マウントされます。',
                'warning': f'現在のマウントに失敗: {mount_result.stderr.strip()[:100]}',
                'entry': entry
            })

    except PermissionError:
        return jsonify({
            'success': False,
            'error': 'sudo権限がありません。手動でfstabを編集してください。'
        })
    except Exception as e:
        logger.error(f"fstab追加エラー: {e}")
        return jsonify({
            'success': False,
            'error': f'エラー: {str(e)[:100]}'
        })


# ============================================================
# アップデート機能
# ============================================================

@app.route('/settings/version')
@login_required
def get_version():
    """現在のバージョンを取得"""
    return jsonify({
        'version': APP_VERSION
    })


@app.route('/settings/check-update')
@login_required
def check_update():
    """GitHubから最新バージョンを確認"""
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        req = urllib.request.Request(url, headers={'User-Agent': 'Patho-Return-App'})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            latest_version = data.get('tag_name', '').lstrip('v')
            release_name = data.get('name', '')
            release_body = data.get('body', '')
            published_at = data.get('published_at', '')

            # バージョン比較（簡易版）
            current_parts = [int(x) for x in APP_VERSION.split('.')]
            latest_parts = [int(x) for x in latest_version.split('.') if x.isdigit()]

            has_update = False
            if len(latest_parts) >= 3:
                for i in range(min(len(current_parts), len(latest_parts))):
                    if latest_parts[i] > current_parts[i]:
                        has_update = True
                        break
                    elif latest_parts[i] < current_parts[i]:
                        break

            return jsonify({
                'success': True,
                'current_version': APP_VERSION,
                'latest_version': latest_version,
                'has_update': has_update,
                'release_name': release_name,
                'release_notes': release_body,
                'published_at': published_at
            })
    except urllib.error.URLError as e:
        return jsonify({
            'success': False,
            'error': 'ネットワークエラー: インターネット接続を確認してください',
            'current_version': APP_VERSION
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'エラー: {str(e)}',
            'current_version': APP_VERSION
        })


@app.route('/settings/do-update', methods=['POST'])
@login_required
def do_update():
    """git pullでアップデートを実行"""
    try:
        # アプリのディレクトリを取得
        app_dir = os.path.dirname(os.path.abspath(__file__))

        # git pullを実行
        result = subprocess.run(
            ['git', 'pull', 'origin', 'main'],
            cwd=app_dir,
            capture_output=True,
            text=True,
            timeout=60
        )

        if result.returncode == 0:
            output = result.stdout.strip()
            if 'Already up to date' in output or 'Already up-to-date' in output:
                return jsonify({
                    'success': True,
                    'message': '既に最新版です',
                    'needs_restart': False,
                    'output': output
                })
            else:
                return jsonify({
                    'success': True,
                    'message': 'アップデート完了。アプリを再起動してください。',
                    'needs_restart': True,
                    'output': output
                })
        else:
            return jsonify({
                'success': False,
                'error': f'git pullに失敗しました: {result.stderr}',
                'output': result.stdout
            })
    except subprocess.TimeoutExpired:
        return jsonify({
            'success': False,
            'error': 'タイムアウト: 更新に時間がかかりすぎています'
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'エラー: {str(e)}'
        })


# ============================================================
# 初期化・スケジューラー
# ============================================================

def init_db():
    """データベース初期化"""
    with app.app_context():
        db.create_all()

        # デフォルト管理者ユーザーがなければ作成
        if User.query.count() == 0:
            admin_user = User(name='管理者', is_admin=True)
            admin_user.set_password('admin')  # 初期パスワード: admin
            db.session.add(admin_user)
            db.session.commit()
            logger.info("デフォルト管理者ユーザーを作成しました（初期パスワード: admin）")


def auto_update():
    """起動時の自動アップデート"""
    try:
        app_dir = os.path.dirname(os.path.abspath(__file__))

        # git pullを実行
        result = subprocess.run(
            ['git', 'pull', 'origin', 'main'],
            cwd=app_dir,
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode == 0:
            output = result.stdout.strip()
            if 'Already up to date' in output or 'Already up-to-date' in output:
                logger.info("自動アップデート: 既に最新版です")
                return False, "既に最新版"
            else:
                logger.info(f"自動アップデート: アップデート完了\n{output}")
                print(f"\n✨ アップデート完了！最新版に更新されました\n")
                return True, "アップデート完了"
        else:
            logger.warning(f"自動アップデート失敗: {result.stderr}")
            return False, f"アップデート失敗: {result.stderr}"

    except subprocess.TimeoutExpired:
        logger.warning("自動アップデート: タイムアウト")
        return False, "タイムアウト"
    except Exception as e:
        logger.warning(f"自動アップデート: エラー - {e}")
        return False, str(e)


def auto_migrate():
    """起動時の自動マイグレーション"""
    import sqlite3

    db_path = os.path.join(Config.BASE_DIR, Config.DATABASE_PATH)

    if not os.path.exists(db_path):
        logger.info("マイグレーション: データベースファイルが存在しません（スキップ）")
        return

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # patient_id列が既に存在するか確認
        cursor.execute("PRAGMA table_info(item_logs)")
        columns = [column[1] for column in cursor.fetchall()]

        if 'patient_id' not in columns:
            logger.info("マイグレーション: patient_id列を追加しています...")

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
            logger.info("マイグレーション: patient_id列を追加しました")

        conn.close()

    except Exception as e:
        logger.error(f"マイグレーション失敗: {e}")
        if 'conn' in locals():
            conn.close()


def check_and_run_daily_backup():
    """その日の初回起動時バックアップを確認・実行"""
    today = datetime.utcnow().strftime('%Y-%m-%d')
    last_backup_date = AppSettings.get('last_backup_date', '')

    if last_backup_date == today:
        logger.info(f"本日のバックアップは実行済み: {today}")
        return True, "本日のバックアップは実行済み"

    # ストレージが接続されているか確認
    backup_type = get_backup_type()
    storage_valid = False
    if backup_type == 'usb':
        storage_valid = USBChecker().is_usb_valid()
        storage_name = 'USB'
    else:
        storage_valid = NASChecker().is_nas_valid()
        storage_name = 'NAS'

    if not storage_valid:
        logger.warning(f"{storage_name}未接続または書き込み不可: バックアップをスキップ")
        return False, f"{storage_name}未接続または書き込み不可"

    # バックアップ実行
    logger.info("初回起動バックアップ開始")
    success, message, path = backup_manager.create_backup()

    if success:
        # バックアップ日を記録
        AppSettings.set('last_backup_date', today)
        logger.info(f"初回起動バックアップ完了: {path}")
        return True, f"バックアップ完了: {path}"
    else:
        logger.error(f"初回起動バックアップ失敗: {message}")
        return False, message


# ============================================================
# メイン
# ============================================================

if __name__ == '__main__':
    # データディレクトリ作成
    os.makedirs(os.path.dirname(Config.DATABASE_PATH), exist_ok=True)
    os.makedirs('logs', exist_ok=True)
    os.makedirs('backups', exist_ok=True)

    # 自動アップデート
    print("🔄 アップデートを確認しています...")
    updated, message = auto_update()
    if updated:
        print("✨ 最新版に更新されました")

    # ストレージチェック
    success, message, can_continue = check_storage_on_startup()
    backup_type = get_backup_type()
    storage_name = 'USB' if backup_type == 'usb' else 'NAS'
    logger.info(f"{storage_name} チェック: {message}")

    if not can_continue:
        print(f"\n⚠️  {message}")
        print(f"{storage_name}に接続してから再起動してください。")
        exit(1)

    # データベース初期化
    init_db()

    # 自動マイグレーション
    auto_migrate()

    # その日の初回起動時バックアップ
    with app.app_context():
        backup_success, backup_message = check_and_run_daily_backup()
        if backup_success:
            print(f"💾 バックアップ: {backup_message}")
        else:
            print(f"⚠️  バックアップ: {backup_message}")

    # アプリ起動
    print("\n🎀 バーコード管理アプリを起動しています...")
    if Config.HOST == '0.0.0.0':
        print(f"📍 アクセス: http://localhost:{Config.PORT} または http://<このPCのIPアドレス>:{Config.PORT}")
    else:
        print(f"📍 アクセス: http://{Config.HOST}:{Config.PORT}")
    print(f"💾 {storage_name}: {message}")
    print("\nCtrl+C で終了\n")

    app.run(host=Config.HOST, port=Config.PORT, debug=Config.DEBUG)
