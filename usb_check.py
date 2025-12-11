"""
USB チェック機能
Ubuntu / macOS 両対応
"""
import os
import subprocess
import platform
from config import Config


class USBChecker:
    """USBメモリの接続状態を確認"""

    def __init__(self):
        self.system = platform.system()
        self.uuid = Config.USB_UUID
        self.mount_point = Config.USB_MOUNT_POINT
        self.required = Config.USB_REQUIRED

    def is_connected(self):
        """USBが接続されているか確認"""
        if not self.uuid:
            # UUIDが設定されていない場合は開発モードとして許可
            return True

        if self.system == 'Darwin':
            return self._check_macos()
        elif self.system == 'Linux':
            return self._check_linux()
        else:
            # 未対応OSは許可
            return True

    def _check_linux(self):
        """Linux (Ubuntu) でのUSB確認"""
        try:
            # /dev/disk/by-uuid/ でUUIDを確認
            uuid_path = f'/dev/disk/by-uuid/{self.uuid}'
            if os.path.exists(uuid_path):
                # マウントされているか確認
                result = subprocess.run(
                    ['findmnt', '-n', '-o', 'TARGET', uuid_path],
                    capture_output=True,
                    text=True
                )
                if result.returncode == 0 and result.stdout.strip():
                    self.mount_point = result.stdout.strip()
                    return True

            # lsblk でも確認
            result = subprocess.run(
                ['lsblk', '-o', 'UUID,MOUNTPOINT', '-n'],
                capture_output=True,
                text=True
            )
            for line in result.stdout.strip().split('\n'):
                parts = line.split()
                if len(parts) >= 2 and parts[0] == self.uuid:
                    self.mount_point = parts[1]
                    return True

            return False
        except Exception:
            return False

    def _check_macos(self):
        """macOS でのUSB確認"""
        try:
            # diskutil でボリュームを確認
            result = subprocess.run(
                ['diskutil', 'list', '-plist'],
                capture_output=True,
                text=True
            )

            # /Volumes/ ディレクトリをチェック
            volumes_path = '/Volumes'
            if os.path.exists(volumes_path):
                for volume in os.listdir(volumes_path):
                    volume_path = os.path.join(volumes_path, volume)
                    if os.path.ismount(volume_path):
                        # diskutil info でUUIDを確認
                        try:
                            info_result = subprocess.run(
                                ['diskutil', 'info', volume_path],
                                capture_output=True,
                                text=True
                            )
                            if self.uuid in info_result.stdout:
                                self.mount_point = volume_path
                                return True
                        except Exception:
                            continue

            return False
        except Exception:
            return False

    def get_mount_point(self):
        """マウントポイントを取得"""
        if self.is_connected():
            return self.mount_point
        return None

    def get_status(self):
        """現在の状態を取得"""
        connected = self.is_connected()
        return {
            'connected': connected,
            'mount_point': self.mount_point if connected else None,
            'uuid': self.uuid,
            'required': self.required,
            'system': self.system
        }


def check_usb_on_startup():
    """
    起動時のUSBチェック
    Returns: (success: bool, message: str, can_continue: bool)
    """
    checker = USBChecker()

    if not checker.uuid:
        return True, "USB設定なし（開発モード）", True

    if checker.is_connected():
        return True, f"USB接続確認済み: {checker.mount_point}", True

    if checker.required:
        return False, "USB未検出: アプリを起動できません。USBメモリを接続してください。", False
    else:
        return False, "USB未検出: バックアップ機能は無効です。", True
