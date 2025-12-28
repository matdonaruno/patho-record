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
APP_VERSION = "1.1.0"
GITHUB_REPO = "matdonaruno/patho-record"

from flask import (
    Flask, render_template, request, jsonify, session,
    redirect, url_for, flash, Response
)

from config import Config
from models import db, User, ItemLog, AuditLog, AppSettings
from logger import setup_logger, get_audit_logger
from nas_check import check_nas_on_startup, NASChecker
from backup import BackupManager

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


@app.route('/login')
def login():
    """ログイン画面（ユーザー選択）"""
    users = User.query.filter_by(is_active=True).order_by(User.name).all()
    nas_status = NASChecker().get_status()
    return render_template('login.html', users=users, nas_status=nas_status)


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

    # 管理者以外はNAS接続チェック
    if not user.is_admin:
        nas_checker = NASChecker()
        if not nas_checker.is_nas_valid():
            error_msg = 'NASに接続できません。ネットワーク接続を確認してから再度ログインしてください。'
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
    nas_status = NASChecker().get_status()
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
        nas_status=nas_status,
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
    data = request.json
    user = get_current_user()

    barcode = data.get('barcode', '').strip() or None
    patient_id = data.get('patient_id', '').strip() or None
    quantity = int(data.get('quantity', 1))
    notes = data.get('notes', '').strip() or None
    returned = data.get('returned', False)
    block_quantity = int(data.get('block_quantity', 0))
    slide_quantity = int(data.get('slide_quantity', 0))

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
    data = request.json

    # 変更前の値を保存
    old_value = item.to_dict()
    now = datetime.utcnow()

    # 更新可能なフィールド
    if 'quantity' in data:
        item.quantity = int(data['quantity'])
    if 'returned' in data:
        new_returned = bool(data['returned'])
        # 結果返却が初めてチェックされた時にタイムスタンプを記録
        if new_returned and not item.returned:
            item.returned_at = now
        elif not new_returned:
            item.returned_at = None
        item.returned = new_returned
    if 'block_quantity' in data:
        new_block_quantity = int(data['block_quantity'])
        # ブロック返却が初めて入力された時にタイムスタンプを記録
        if new_block_quantity > 0 and item.block_quantity == 0:
            item.block_returned_at = now
        elif new_block_quantity == 0:
            item.block_returned_at = None
        item.block_quantity = new_block_quantity
    if 'slide_quantity' in data:
        new_slide_quantity = int(data['slide_quantity'])
        # スライド返却が初めて入力された時にタイムスタンプを記録
        if new_slide_quantity > 0 and item.slide_quantity == 0:
            item.slide_returned_at = now
        elif new_slide_quantity == 0:
            item.slide_returned_at = None
        item.slide_quantity = new_slide_quantity
    if 'notes' in data:
        item.notes = data['notes'].strip() or None
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
    data = request.json
    name = data.get('name', '').strip()
    password = data.get('password', '').strip()
    is_admin = data.get('is_admin', False)

    if not name:
        return jsonify({'error': '名前を入力してください'}), 400

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
    data = request.json

    old_value = user.to_dict()

    if 'name' in data:
        name = data['name'].strip()
        if name:
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
        password = data['password'].strip() if data['password'] else ''
        if password:
            user.set_password(password)
        elif data.get('clear_password'):
            user.password_hash = None  # パスワードをクリア

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
    nas_status = NASChecker().get_status()
    last_backup = backup_manager.get_last_backup_info()
    backups = backup_manager.list_backups()

    return jsonify({
        'nas': nas_status,
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
    nas_status = NASChecker().get_status()
    users = User.query.order_by(User.name).all()

    # 返却期限日数を取得（デフォルトはConfig値）
    return_days = AppSettings.get('return_days', str(Config.DEFAULT_RETURN_DAYS))

    return render_template(
        'settings.html',
        user=user,
        nas_status=nas_status,
        users=users,
        config=Config,
        return_days=int(return_days)
    )


@app.route('/settings/return-days', methods=['GET', 'POST'])
@login_required
def settings_return_days():
    """返却期限日数の取得・更新"""
    if request.method == 'GET':
        return_days = AppSettings.get('return_days', str(Config.DEFAULT_RETURN_DAYS))
        return jsonify({'return_days': int(return_days)})

    data = request.json
    days = data.get('days')

    if days is None or not isinstance(days, int) or days < 1:
        return jsonify({'error': '有効な日数を指定してください'}), 400

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


@app.route('/settings/nas-status')
@login_required
def get_nas_status():
    """NAS接続状態を取得"""
    checker = NASChecker()
    status = checker.get_status()
    reachable = checker.check_nas_reachable()

    return jsonify({
        'status': status,
        'reachable': reachable
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

    # NASが接続されているか確認
    nas_checker = NASChecker()
    if not nas_checker.is_nas_valid():
        logger.warning("NAS未接続または書き込み不可: バックアップをスキップ")
        return False, "NAS未接続または書き込み不可"

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

    # NAS チェック
    success, message, can_continue = check_nas_on_startup()
    logger.info(f"NAS チェック: {message}")

    if not can_continue:
        print(f"\n⚠️  {message}")
        print("NASに接続してから再起動してください。")
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
    print(f"💾 NAS: {message}")
    print("\nCtrl+C で終了\n")

    app.run(host=Config.HOST, port=Config.PORT, debug=Config.DEBUG)
