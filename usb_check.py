"""
USB チェック機能
Ubuntu / macOS 両対応
"""
import os
import subprocess
import platform
import re
from config import Config


def get_usb_setting(key, default=None):
    """AppSettingsからUSB設定を取得（フォールバック: Config）"""
    try:
        from flask import has_app_context
        if has_app_context():
            from models import AppSettings
            value = AppSettings.get(f'usb_{key}')
            if value is not None and value != '':
                return value
    except (RuntimeError, ImportError):
        # Flaskコンテキスト外
        pass

    # Config からフォールバック
    config_key = f'USB_{key.upper()}'
    return getattr(Config, config_key, default)


class USBChecker:
    """USBメモリの接続状態を確認"""

    def __init__(self):
        self.system = platform.system()
        # AppSettings優先で設定を読み込み
        self.uuid = get_usb_setting('uuid', '')
        self.mount_point = get_usb_setting('mount_point', '/media/usb_backup')
        required_val = get_usb_setting('required', 'true')
        self.required = str(required_val).lower() == 'true'
        self.backup_folder = get_usb_setting('backup_folder', 'barcode_app_backups')

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

    def is_usb_valid(self):
        """設定されたUSBデバイスIDと一致するか検証"""
        # 動的に設定を読み込む（AppSettingsから）
        try:
            from models import AppSettings
            usb_device_id = AppSettings.get('usb_device_id', '')
        except Exception:
            usb_device_id = ''

        if not usb_device_id:
            # デバイスIDが未設定の場合はUUID確認のみ
            return self.is_connected()

        if self.system == 'Darwin':
            return self._validate_macos_device(usb_device_id)
        elif self.system == 'Linux':
            return self._validate_linux_device(usb_device_id)
        else:
            return self.is_connected()

    def _validate_macos_device(self, device_id):
        """macOSでUSBデバイスIDを検証"""
        try:
            # system_profilerでUSBデバイス一覧を取得
            result = subprocess.run(
                ['system_profiler', 'SPUSBDataType'],
                capture_output=True,
                text=True
            )

            if device_id in result.stdout:
                # デバイスIDが見つかったらマウントポイントを確認
                return self._find_mounted_usb()

            return False
        except Exception:
            return False

    def _validate_linux_device(self, device_id):
        """LinuxでUSBデバイスIDを検証"""
        try:
            # lsusbでデバイス確認
            result = subprocess.run(
                ['lsusb'],
                capture_output=True,
                text=True
            )

            if device_id in result.stdout:
                return self.is_connected()

            return False
        except Exception:
            return False

    def _find_mounted_usb(self):
        """マウントされているUSBを探す"""
        try:
            volumes_path = '/Volumes'
            if os.path.exists(volumes_path):
                for volume in os.listdir(volumes_path):
                    volume_path = os.path.join(volumes_path, volume)
                    if os.path.ismount(volume_path):
                        self.mount_point = volume_path
                        return True
            return False
        except Exception:
            return False

    def get_connected_usb_devices(self):
        """接続中のUSBデバイス一覧を取得"""
        devices = []

        if self.system == 'Darwin':
            devices = self._get_macos_usb_devices()
        elif self.system == 'Linux':
            devices = self._get_linux_usb_devices()

        return devices

    def _get_macos_usb_devices(self):
        """macOSで接続中のUSBデバイス情報を取得"""
        devices = []
        try:
            result = subprocess.run(
                ['system_profiler', 'SPUSBDataType', '-detailLevel', 'mini'],
                capture_output=True,
                text=True
            )

            # シンプルなパース
            current_device = {}
            for line in result.stdout.split('\n'):
                line = line.strip()
                if ':' in line:
                    if line.endswith(':') and not line.startswith('USB'):
                        # デバイス名
                        if current_device:
                            devices.append(current_device)
                        current_device = {'name': line[:-1]}
                    elif 'Serial Number:' in line:
                        current_device['serial'] = line.split(':')[1].strip()
                    elif 'Vendor ID:' in line:
                        current_device['vendor_id'] = line.split(':')[1].strip()
                    elif 'Product ID:' in line:
                        current_device['product_id'] = line.split(':')[1].strip()

            if current_device:
                devices.append(current_device)

            # シリアル番号があるデバイスのみ返す
            return [d for d in devices if d.get('serial')]

        except Exception:
            return []

    def _get_linux_usb_devices(self):
        """LinuxでUSBデバイス情報を取得"""
        devices = []
        try:
            result = subprocess.run(
                ['lsusb', '-v'],
                capture_output=True,
                text=True
            )

            # シンプルなパース
            for line in result.stdout.split('\n'):
                if 'iSerial' in line and 'Serial' in line:
                    parts = line.split()
                    if len(parts) > 2:
                        serial = parts[-1]
                        if serial and serial != '0':
                            devices.append({'serial': serial})

            return devices
        except Exception:
            return []

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
