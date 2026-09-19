"""HMI 계정·화면 설정 저장소. 공정 기록 SQLite와 분리한 권한 0600 JSON.

초기 관리자: python3 -m gmp_hmi.core.admin_store --path <경로> --username <이름>
비밀번호는 대화식 입력 또는 --password-env 환경변수 이름으로 전달한다.
웹 세션은 프로세스 메모리에 있고 재시작하면 폐기된다. 계정 수정도 기존 세션을 폐기한다.
"""
import argparse
import copy
import getpass
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading

ROLES = ('viewer', 'operator', 'qa', 'admin')
DEFAULT_SETTINGS = dict(inventory_material_ids=['A', 'B', 'C'],
                        inventory_capacity_g=[0.0, 0.0, 0.0],
                        inventory_initial_g=[-1.0, -1.0, -1.0],
                        inventory_low_pct=20.0, ui_stale_after_s=3.0)


def _password_hash(password):
    if not isinstance(password, str) or not 12 <= len(password) <= 128:
        raise ValueError('비밀번호는 12~128자로 입력하세요')
    salt = secrets.token_hex(16)
    derived = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600000)
    return f'pbkdf2_sha256$600000${salt}${derived.hex()}'


def _password_matches(password, encoded):
    try:
        method, iterations, salt, expected = encoded.split('$')
        if method != 'pbkdf2_sha256' or not isinstance(password, str) or len(password) > 128:
            return False
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(iterations))
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False


def validate_settings(settings):
    from gmp_hmi.core.session_inventory import SessionInventory
    unknown = set(settings) - set(DEFAULT_SETTINGS)
    if unknown:
        raise ValueError('지원하지 않는 설정: ' + ', '.join(sorted(unknown)))
    value = copy.deepcopy(settings)
    for key in ('inventory_material_ids', 'inventory_capacity_g', 'inventory_initial_g'):
        if not isinstance(value.get(key), list) or len(value[key]) > 100:
            raise ValueError('원료 설정은 1~100개 배열이어야 합니다')
    try:
        SessionInventory(value['inventory_material_ids'], value['inventory_capacity_g'],
                         value['inventory_initial_g'], value['inventory_low_pct'])
        stale = float(value['ui_stale_after_s'])
    except (TypeError, KeyError, OverflowError) as exc:
        raise ValueError('원료 설정 및 통신 기준값의 형식을 확인하세요') from exc
    if not math.isfinite(stale) or not .5 <= stale <= 30:
        raise ValueError('통신 지연 표시 기준은 0.5~30초여야 합니다')
    value['ui_stale_after_s'] = stale
    value['inventory_low_pct'] = float(value['inventory_low_pct'])
    value['inventory_capacity_g'] = list(map(float, value['inventory_capacity_g']))
    value['inventory_initial_g'] = list(map(float, value['inventory_initial_g']))
    return value


class AdminStore:
    def __init__(self, path, defaults=None):
        self.path = Path(os.path.expanduser(str(path)))
        self.lock = threading.RLock()
        if self.path.exists():
            with self.path.open(encoding='utf-8') as stream:
                self.data = json.load(stream)
            if self.data.get('version') != 1 or not isinstance(self.data.get('users'), dict):
                raise ValueError('HMI 계정 파일 형식을 확인하세요')
            self.data['settings'] = validate_settings(self.data['settings'])
        else:
            self.data = {'version': 1, 'users': {},
                         'settings': validate_settings(defaults or DEFAULT_SETTINGS)}
            self._save()

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temporary = tempfile.mkstemp(prefix='.hmi-', dir=self.path.parent)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                json.dump(self.data, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def _public(user):
        return {key: user[key] for key in ('username', 'role', 'active', 'version')}

    def setup_required(self):
        with self.lock:
            return not self.data['users']

    def user(self, username):
        with self.lock:
            user = self.data['users'].get(username)
            return self._public(user) if user else None

    def users(self):
        with self.lock:
            return [self._public(user) for _, user in sorted(self.data['users'].items())]

    def authenticate(self, username, password):
        with self.lock:
            user = copy.deepcopy(self.data['users'].get(username))
        if not user or not user['active'] or not _password_matches(password, user['password_hash']):
            return None
        return self._public(user)

    def create_user(self, username, password, role='viewer', active=True):
        if not isinstance(username, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', username):
            raise ValueError('계정 이름은 영문·숫자·밑줄·점·하이픈 1~64자입니다')
        if role not in ROLES or not isinstance(active, bool):
            raise ValueError('권한 또는 활성 여부가 올바르지 않습니다')
        hashed = _password_hash(password)
        with self.lock:
            if username in self.data['users']:
                raise ValueError('이미 존재하는 계정입니다')
            if not self.data['users'] and (role != 'admin' or not active):
                raise ValueError('첫 계정은 활성 관리자여야 합니다')
            self.data['users'][username] = dict(username=username, password_hash=hashed,
                                                role=role, active=active, version=1)
            self._save()
            return self._public(self.data['users'][username])

    def update_user(self, username, changes):
        if not changes or set(changes) - {'role', 'active', 'password'}:
            raise ValueError('수정 가능한 항목은 role, active, password입니다')
        hashed = _password_hash(changes['password']) if 'password' in changes else None
        with self.lock:
            if username not in self.data['users']:
                raise ValueError('계정이 없습니다')
            user = copy.deepcopy(self.data['users'][username])
            if 'role' in changes:
                if changes['role'] not in ROLES:
                    raise ValueError('권한이 올바르지 않습니다')
                user['role'] = changes['role']
            if 'active' in changes:
                if not isinstance(changes['active'], bool):
                    raise ValueError('활성 여부는 true 또는 false입니다')
                user['active'] = changes['active']
            if hashed:
                user['password_hash'] = hashed
            others = [item for key, item in self.data['users'].items() if key != username]
            if not (user['active'] and user['role'] == 'admin') and not any(
                    item['active'] and item['role'] == 'admin' for item in others):
                raise ValueError('활성 관리자 1명 이상을 유지해야 합니다')
            user['version'] += 1
            self.data['users'][username] = user
            self._save()
            return self._public(user)

    def settings(self):
        with self.lock:
            return copy.deepcopy(self.data['settings'])

    def update_settings(self, changes):
        with self.lock:
            merged = dict(self.data['settings'], **changes)
            value = validate_settings(merged)
            self.data['settings'] = value
            self._save()
            return copy.deepcopy(value)


def main():
    parser = argparse.ArgumentParser(description='HMI 최초 관리자 계정 생성 (기존 계정 초기화 없음)')
    parser.add_argument('--path', default='~/.config/gmp_hmi/admin.json')
    parser.add_argument('--username', required=True)
    parser.add_argument('--password-env', help='비밀번호가 든 환경변수 이름. 생략하면 숨김 입력')
    args = parser.parse_args()
    store = AdminStore(args.path)
    if not store.setup_required():
        parser.error('이미 초기화되었습니다. 계정 변경은 관리자로 로그인한 뒤 진행하세요')
    password = os.environ.get(args.password_env, '') if args.password_env else getpass.getpass('새 관리자 비밀번호(12자 이상): ')
    if not args.password_env and password != getpass.getpass('비밀번호 다시 입력: '):
        parser.error('비밀번호가 일치하지 않습니다')
    try:
        store.create_user(args.username, password, 'admin')
    except ValueError as exc:
        parser.error(str(exc))
    print(f'관리자 {args.username} 생성 완료. 계정 파일: {store.path}')


if __name__ == '__main__':
    main()
