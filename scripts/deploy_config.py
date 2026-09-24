"""Validate deployment settings without executing .env as shell code."""
import argparse
import ipaddress
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit


KEYS = ('DOMAIN', 'HONGGUO_NETWORK_MODE', 'HONGGUO_UPSTREAM_PROXY', 'COMPOSE_PROJECT_NAME')


def read_settings(path):
    values = {}
    legacy = False
    if not path.exists():
        return values
    for line in path.read_text(encoding='utf-8').splitlines():
        text = line.strip()
        if not text or text.startswith('#'):
            continue
        match = re.fullmatch(r'([A-Z_][A-Z0-9_]*)=(.*)', text)
        if not match:
            raise ValueError('.env 格式无效，请使用 KEY=value，每项单独一行。')
        key, value = match.groups()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key not in KEYS:
            if key == 'HONGGUO_SOURCE_DIR':
                legacy = True
                continue  # Older deployments used an external build context.
            raise ValueError('不支持的 .env 配置项：'+key)
        if key in values:
            raise ValueError('.env 包含重复配置项：'+key)
        values[key] = value
    if legacy:
        values.setdefault('COMPOSE_PROJECT_NAME', 'hongguo-cinema')
    return values


def validate_settings(values):
    domain = values.get('DOMAIN', '').strip().lower().rstrip('.')
    if len(domain) > 253 or not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?', domain):
        raise ValueError('DOMAIN 必须是已解析到服务器的域名，不含 https://、端口或路径。')
    if domain == 'example.com' or domain.endswith(('.example.com', '.example', '.invalid', '.test', '.localhost', '.local')):
        raise ValueError('请使用实际域名，不要使用文档中的示例域名。')
    values['DOMAIN'] = domain
    mode = values.get('HONGGUO_NETWORK_MODE', 'overseas')
    if mode not in ('direct', 'overseas'):
        raise ValueError('网络模式只能是 overseas 或 direct。')
    values['HONGGUO_NETWORK_MODE'] = mode
    proxy = values.get('HONGGUO_UPSTREAM_PROXY', '').strip()
    if mode == 'overseas':
        if not proxy:
            raise ValueError('海外模式需要容器可访问的代理地址；海外服务器可显式使用 --direct。')
        try:
            url = urlsplit(proxy)
            if url.scheme not in ('http', 'https', 'socks5') or not url.hostname or not url.port:
                raise ValueError
            if url.path not in ('', '/') or url.query or url.fragment:
                raise ValueError
            if re.search(r"[\s'\"\\]", proxy):
                raise ValueError
        except ValueError:
            raise ValueError('代理应为 http://、https:// 或 socks5://主机:端口；凭据中的特殊字符请 URL 编码。') from None
        host = url.hostname.lower()
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host == 'localhost' or host.endswith('.localhost')
        if loopback or host in ('0.0.0.0', '::'):
            raise ValueError('容器中的 127.0.0.1/localhost 不是宿主机，请使用容器可达的海外代理。')
    else:
        proxy = ''
    values['HONGGUO_UPSTREAM_PROXY'] = proxy
    project = values.get('COMPOSE_PROJECT_NAME', 'taco-cinema')
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,62}', project):
        raise ValueError('COMPOSE_PROJECT_NAME 只能含小写字母、数字、连字符、下划线。')
    values['COMPOSE_PROJECT_NAME'] = project
    return values


def main():
    parser = argparse.ArgumentParser(description='TACO小剧场部署配置')
    parser.add_argument('--existing', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--domain')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--direct', action='store_true')
    mode.add_argument('--proxy')
    parser.add_argument('--non-interactive', action='store_true')
    parser.add_argument('--reconfigure', action='store_true')
    args = parser.parse_args()
    try:
        values = read_settings(args.existing)
        interactive = not args.non_interactive and sys.stdin.isatty()
        if args.reconfigure and not interactive and not (args.domain or args.direct or args.proxy):
            raise ValueError('修改配置需要交互终端，或显式指定 --domain / --direct / --proxy。')
        if args.reconfigure and interactive and args.domain is None:
            current = values.get('DOMAIN', '')
            print(f'网站域名 [{current}]：', end='', file=sys.stderr, flush=True)
            values['DOMAIN'] = input().strip() or current
        if args.domain is not None:
            values['DOMAIN'] = args.domain
        if not values.get('DOMAIN'):
            if not interactive:
                raise ValueError('首次部署请指定 --domain 你的域名。')
            print('网站域名（例如 video.your-domain.com）：', end='', file=sys.stderr, flush=True)
            values['DOMAIN'] = input().strip()
        if args.direct:
            values['HONGGUO_NETWORK_MODE'] = 'direct'
            values['HONGGUO_UPSTREAM_PROXY'] = ''
        elif args.proxy is not None:
            values['HONGGUO_NETWORK_MODE'] = 'overseas'
            values['HONGGUO_UPSTREAM_PROXY'] = args.proxy
        if interactive and not (args.direct or args.proxy is not None) and ('HONGGUO_NETWORK_MODE' not in values or args.reconfigure):
            default = '2' if values.get('HONGGUO_NETWORK_MODE') == 'direct' else '1'
            print(f'线路：1 海外代理；2 服务器已在海外，使用其出口 [{default}]：', end='', file=sys.stderr, flush=True)
            choice = input().strip() or default
            if choice not in ('1', '2'):
                raise ValueError('线路选项无效，请重新运行。')
            values['HONGGUO_NETWORK_MODE'] = 'direct' if choice == '2' else 'overseas'
        if interactive and values.get('HONGGUO_NETWORK_MODE', 'overseas') == 'overseas' and args.proxy is None and (not values.get('HONGGUO_UPSTREAM_PROXY') or args.reconfigure):
            import getpass
            values['HONGGUO_UPSTREAM_PROXY'] = getpass.getpass('海外代理地址（输入隐藏，留空保留已有地址）：').strip() or values.get('HONGGUO_UPSTREAM_PROXY', '')
        values = validate_settings(values)
        # Single-quoted dotenv values preserve literal $ in proxy credentials.
        content = '# TACO小剧场部署配置；请勿提交此文件。\n'
        content += ''.join(f"{key}='{values[key]}'\n" for key in KEYS)
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(content)
        os.chmod(args.output, 0o600)
        print(values['DOMAIN'])
    except (ValueError, OSError, EOFError) as exc:
        parser.exit(2, str(exc)+'\n')


if __name__ == '__main__':
    main()
