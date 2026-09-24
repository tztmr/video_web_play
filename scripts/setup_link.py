"""Print the one-time initialization link on the administrator's terminal."""
import argparse
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from accounts import Accounts

parser = argparse.ArgumentParser()
parser.add_argument('--base-url', default=os.environ.get('HONGGUO_PUBLIC_URL', 'http://127.0.0.1:8787'))
parser.add_argument('--open', action='store_true')
parser.add_argument('--if-needed', action='store_true', help='部署重跑时，已初始化则正常退出')
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
accounts = Accounts(os.environ.get('HONGGUO_WEB_DATA_DIR', root / '.data'))
if not accounts.needs_setup():
    if args.if_needed:
        print('已完成初始化，请使用现有管理员账号登录。')
        raise SystemExit(0)
    raise SystemExit('已完成初始化，请使用管理员账号登录。')
url = args.base_url.rstrip('/')+'/setup#'+accounts.setup_file.read_text().strip()
if args.open:
    import webbrowser
    webbrowser.open(url)
else:
    print(url)
