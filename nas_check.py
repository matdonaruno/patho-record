"""
NAS チェック機能（SMB/CIFS）
Linux Mint / Ubuntu 対応
"""
import os
import subprocess
import platform
import logging
from config import Config

logger = logging.getLogger(__name__)


class NASChecker:
    """NAS（SMB/CIFS）の接続状態を確認・管理"""

    def __init__(self):
        self.system = platform.system()
        self.host = Config.NAS_HOST
        self.share = Config.NAS_SHARE
        self.username = Config.NAS_USERNAME
        self.password = Config.NAS_PASSWORD
        self.mount_point = Config.NAS_MOUNT_POINT
        self.required = Config.NAS_REQUIRED
        self.backup_folder = Config.NAS_BACKUP_FOLDER

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
        """NASのマウントを試行"""
        try:
            # マウントポイントディレクトリを作成
            if not os.path.exists(self.mount_point):
                os.makedirs(self.mount_point, exist_ok=True)

            # SMB マウントコマンドを構築
            smb_path = f"//{self.host}/{self.share}"

            if self.username:
                # 認証あり
                credentials = f"username={self.username},password={self.password}"
            else:
                # 匿名アクセス（guest）
                credentials = "guest,uid=1000,gid=1000"

            mount_options = f"{credentials},iocharset=utf8,vers=3.0"

            # mount.cifs コマンドを実行
            result = subprocess.run(
                ['sudo', 'mount', '-t', 'cifs', smb_path, self.mount_point,
                 '-o', mount_options],
                capture_output=True,
                text=True
            )

            if result.returncode == 0:
                logger.info(f"NASマウント成功: {smb_path} -> {self.mount_point}")
                return True
            else:
                logger.warning(f"NASマウント失敗: {result.stderr}")
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
                text=True
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
        try:
            result = subprocess.run(
                ['ping', '-c', '1', '-W', '2', self.host],
                capture_output=True,
                text=True
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
                text=True
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
