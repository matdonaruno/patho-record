"""
NAS チェック機能（SMB/CIFS）
Linux Mint / Ubuntu 対応
Buffalo NAS 直結運用対応（SMB1）
"""
import os
import subprocess
import platform
import logging
import hashlib
from datetime import datetime
from config import Config

logger = logging.getLogger(__name__)


def get_nas_setting(key, default=None):
    """AppSettingsからNAS設定を取得（フォールバック: Config）"""
    try:
        from flask import has_app_context
        if has_app_context():
            from models import AppSettings
            value = AppSettings.get(f'nas_{key}')
            if value is not None and value != '':
                return value
    except (RuntimeError, ImportError):
        # Flaskコンテキスト外
        pass

    # Config からフォールバック
    config_key = f'NAS_{key.upper()}'
    return getattr(Config, config_key, default)


class NASChecker:
    """NAS（SMB/CIFS）の接続状態を確認・管理"""

    def __init__(self):
        self.system = platform.system()
        # AppSettings優先で設定を読み込み
        self.host = get_nas_setting('host', '')
        self.share = get_nas_setting('share', '')
        self.username = get_nas_setting('username', '')
        self.password = get_nas_setting('password', '')
        self.mount_point = get_nas_setting('mount_point', '/mnt/nas_backup')
        required_val = get_nas_setting('required', 'true')
        self.required = str(required_val).lower() == 'true'
        self.backup_folder = get_nas_setting('backup_folder', 'barcode_app_backups')

    def is_connected(self):
        """NASがマウントされているか確認"""
        if not self.host:
            # ホストが設定されていない場合は開発モード
            return False

        # マウントポイントが存在し、マウントされているか確認
        if os.path.ismount(self.mount_point):
            return True

        # マウントされていない場合、自動マウントを試行
        return self._try_mount()

    def is_nas_valid(self):
        """NASが有効で書き込み可能か検証"""
        if not self.is_connected():
            return False

        # 書き込みテスト
        test_file = os.path.join(self.mount_point, '.write_test')
        try:
            with open(test_file, 'w') as f:
                f.write('test')
            os.remove(test_file)
            return True
        except Exception as e:
            logger.warning(f"NAS書き込みテスト失敗: {e}")
            return False

    def _try_mount(self):
        """NASのマウントを試行（SMB1/SMB2/SMB3 フォールバック対応）"""
        # まずfstab経由を試す
        if self._try_mount_without_sudo():
            return True

        try:
            # マウントポイントディレクトリを作成
            if not os.path.exists(self.mount_point):
                os.makedirs(self.mount_point, exist_ok=True)

            smb_path = f"//{self.host}/{self.share}"

            # 認証設定
            if self.username:
                credentials = f"username={self.username},password={self.password}"
            else:
                credentials = "guest,uid=1000,gid=1000"

            # SMBバージョンを順番に試行（Buffalo NAS直結の場合SMB1が必要な場合あり）
            smb_versions = ['1.0', '2.0', '2.1', '3.0']

            for vers in smb_versions:
                mount_options = f"{credentials},iocharset=utf8,vers={vers}"

                result = subprocess.run(
                    ['sudo', 'mount', '-t', 'cifs', smb_path, self.mount_point,
                     '-o', mount_options],
                    capture_output=True,
                    text=True,
                    timeout=10
                )

                if result.returncode == 0:
                    logger.info(f"NASマウント成功 (SMB {vers}): {smb_path} -> {self.mount_point}")
                    return True
                else:
                    logger.debug(f"SMB {vers} 失敗: {result.stderr}")

            logger.warning(f"NASマウント失敗（全バージョン試行済み）")
            return False

        except subprocess.TimeoutExpired:
            logger.error("NASマウント: タイムアウト")
            return False
        except Exception as e:
            logger.error(f"NASマウントエラー: {e}")
            return False

    def _try_mount_without_sudo(self):
        """fstabに設定済みの場合のマウント（sudoなし）"""
        try:
            result = subprocess.run(
                ['mount', self.mount_point],
                capture_output=True,
                text=True,
                timeout=10
            )
            return result.returncode == 0
        except Exception:
            return False

    def unmount(self):
        """NASをアンマウント"""
        try:
            if os.path.ismount(self.mount_point):
                result = subprocess.run(
                    ['sudo', 'umount', self.mount_point],
                    capture_output=True,
                    text=True
                )
                return result.returncode == 0
            return True
        except Exception as e:
            logger.error(f"NASアンマウントエラー: {e}")
            return False

    def get_mount_point(self):
        """マウントポイントを取得"""
        if self.is_connected():
            return self.mount_point
        return None

    def get_backup_dir(self):
        """バックアップ用ディレクトリパスを取得"""
        if self.is_connected():
            backup_dir = os.path.join(self.mount_point, self.backup_folder)
            if not os.path.exists(backup_dir):
                try:
                    os.makedirs(backup_dir)
                except Exception as e:
                    logger.error(f"バックアップディレクトリ作成失敗: {e}")
                    return None
            return backup_dir
        return None

    def get_status(self):
        """現在の状態を取得"""
        connected = self.is_connected()
        return {
            'connected': connected,
            'host': self.host,
            'share': self.share,
            'mount_point': self.mount_point if connected else None,
            'required': self.required,
            'system': self.system
        }

    def check_nas_reachable(self):
        """NASにネットワーク的に到達可能か確認"""
        if not self.host:
            return False
        try:
            result = subprocess.run(
                ['ping', '-c', '1', '-W', '2', self.host],
                capture_output=True,
                text=True,
                timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False

    def get_nas_info(self):
        """NASの共有情報を取得（smbclient）"""
        try:
            cmd = ['smbclient', '-L', f'//{self.host}', '-N']
            if self.username:
                cmd = ['smbclient', '-L', f'//{self.host}',
                       '-U', f'{self.username}%{self.password}']

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode == 0:
                return {
                    'success': True,
                    'output': result.stdout,
                    'shares': self._parse_shares(result.stdout)
                }
            else:
                return {
                    'success': False,
                    'error': result.stderr
                }
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }

    def _parse_shares(self, output):
        """smbclient出力から共有一覧をパース"""
        shares = []
        in_share_section = False
        for line in output.split('\n'):
            if 'Sharename' in line and 'Type' in line:
                in_share_section = True
                continue
            if in_share_section:
                if line.strip() == '' or 'Reconnecting' in line:
                    break
                parts = line.split()
                if len(parts) >= 2:
                    shares.append({
                        'name': parts[0],
                        'type': parts[1]
                    })
        return shares

    def verify_backup(self, local_file_path, remote_file_path):
        """バックアップファイルの整合性を検証"""
        try:
            if not os.path.exists(local_file_path):
                return {'success': False, 'error': 'ローカルファイルが存在しません'}

            if not os.path.exists(remote_file_path):
                return {'success': False, 'error': 'リモートファイルが存在しません'}

            # ファイルサイズ比較
            local_size = os.path.getsize(local_file_path)
            remote_size = os.path.getsize(remote_file_path)

            if local_size != remote_size:
                return {
                    'success': False,
                    'error': f'ファイルサイズ不一致: ローカル={local_size}, リモート={remote_size}'
                }

            # MD5ハッシュ比較
            local_hash = self._calculate_md5(local_file_path)
            remote_hash = self._calculate_md5(remote_file_path)

            if local_hash != remote_hash:
                return {
                    'success': False,
                    'error': f'ハッシュ不一致: ローカル={local_hash[:8]}..., リモート={remote_hash[:8]}...'
                }

            return {
                'success': True,
                'local_size': local_size,
                'remote_size': remote_size,
                'hash': local_hash,
                'message': 'バックアップ検証成功'
            }

        except Exception as e:
            return {'success': False, 'error': str(e)}

    def _calculate_md5(self, file_path):
        """ファイルのMD5ハッシュを計算"""
        hash_md5 = hashlib.md5()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b''):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()

    def get_backup_files(self):
        """NAS上のバックアップファイル一覧を取得"""
        backup_dir = self.get_backup_dir()
        if not backup_dir:
            return []

        files = []
        try:
            for filename in os.listdir(backup_dir):
                if filename.endswith('.db'):
                    filepath = os.path.join(backup_dir, filename)
                    stat = os.stat(filepath)
                    files.append({
                        'filename': filename,
                        'path': filepath,
                        'size': stat.st_size,
                        'modified': datetime.fromtimestamp(stat.st_mtime).isoformat()
                    })
            files.sort(key=lambda x: x['modified'], reverse=True)
        except Exception as e:
            logger.error(f"バックアップファイル一覧取得失敗: {e}")

        return files

    def run_full_diagnostics(self):
        """NAS接続の完全診断を実行"""
        results = {
            'timestamp': datetime.now().isoformat(),
            'tests': []
        }

        # 1. 設定確認
        results['tests'].append({
            'name': '設定確認',
            'status': 'ok' if self.host else 'skip',
            'detail': f'ホスト: {self.host}, 共有: {self.share}' if self.host else 'NAS設定なし'
        })

        if not self.host:
            results['overall'] = 'skip'
            return results

        # 2. ネットワーク到達性
        reachable = self.check_nas_reachable()
        results['tests'].append({
            'name': 'ネットワーク到達性 (ping)',
            'status': 'ok' if reachable else 'error',
            'detail': f'{self.host} への ping ' + ('成功' if reachable else '失敗')
        })

        if not reachable:
            results['overall'] = 'error'
            return results

        # 3. SMB接続確認
        nas_info = self.get_nas_info()
        results['tests'].append({
            'name': 'SMB接続 (smbclient)',
            'status': 'ok' if nas_info['success'] else 'warning',
            'detail': f"共有: {', '.join([s['name'] for s in nas_info.get('shares', [])])}" if nas_info['success'] else nas_info.get('error', '不明')
        })

        # 4. マウント状態
        connected = self.is_connected()
        results['tests'].append({
            'name': 'マウント状態',
            'status': 'ok' if connected else 'error',
            'detail': f'{self.mount_point} にマウント' + ('済み' if connected else '失敗')
        })

        if not connected:
            results['overall'] = 'error'
            return results

        # 5. 書き込みテスト
        writable = self.is_nas_valid()
        results['tests'].append({
            'name': '書き込みテスト',
            'status': 'ok' if writable else 'error',
            'detail': '書き込み可能' if writable else '書き込み不可'
        })

        # 6. バックアップディレクトリ
        backup_dir = self.get_backup_dir()
        results['tests'].append({
            'name': 'バックアップディレクトリ',
            'status': 'ok' if backup_dir else 'error',
            'detail': backup_dir if backup_dir else '作成失敗'
        })

        # 7. 既存バックアップ確認
        backup_files = self.get_backup_files()
        results['tests'].append({
            'name': '既存バックアップ',
            'status': 'ok',
            'detail': f'{len(backup_files)}件のバックアップファイル'
        })

        # 総合判定
        error_count = sum(1 for t in results['tests'] if t['status'] == 'error')
        warning_count = sum(1 for t in results['tests'] if t['status'] == 'warning')

        if error_count > 0:
            results['overall'] = 'error'
        elif warning_count > 0:
            results['overall'] = 'warning'
        else:
            results['overall'] = 'ok'

        return results


def check_nas_on_startup():
    """
    起動時のNASチェック
    Returns: (success: bool, message: str, can_continue: bool)
    """
    checker = NASChecker()

    if not checker.host:
        return True, "NAS設定なし（開発モード）", True

    # ネットワーク到達性確認
    if not checker.check_nas_reachable():
        if checker.required:
            return False, f"NAS未検出: {checker.host} に接続できません", False
        else:
            return False, "NAS未検出: バックアップ機能は無効です", True

    # マウント確認・試行
    if checker.is_connected():
        return True, f"NAS接続確認済み: {checker.mount_point}", True

    if checker.required:
        return False, "NASマウント失敗: バックアップを使用するにはNASをマウントしてください", False
    else:
        return False, "NASマウント失敗: バックアップ機能は無効です", True


def generate_fstab_entry():
    """fstabエントリを生成（直結運用用）"""
    checker = NASChecker()

    if not checker.host:
        return None

    smb_path = f"//{checker.host}/{checker.share}"

    if checker.username:
        credentials = f"username={checker.username},password={checker.password}"
    else:
        credentials = "guest"

    # SMB1対応のオプション（Buffalo NAS直結用）
    options = f"{credentials},iocharset=utf8,vers=1.0,uid=1000,gid=1000,nofail,_netdev"

    return f"{smb_path}\t{checker.mount_point}\tcifs\t{options}\t0\t0"
