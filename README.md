# TACO小剧场

基于「红果视频下载」API 与播放算法的短剧网站。所需 Python 运行模块已随仓库提供，克隆本项目即可独立运行。默认登录后浏览和观看，管理员可创建观众账号、生成免登录分享链接。视频默认由观众浏览器直接从源站获取、解密和播放，网站服务器只处理网页、账号与地址解析。无需启动原桌面 App。

服务器一键部署见 [DEPLOYMENT.md](DEPLOYMENT.md)，当前网络实测见 [NETWORK_TEST.md](NETWORK_TEST.md)。

## 服务器一键部署

在 Linux 服务器上，以 root 或具备 sudo 权限的账号执行（默认安装目录 `/opt/taco-cinema`）：

```bash
curl -fL --retry 3 https://raw.githubusercontent.com/tztmr/video_web_play/main/deploy.sh -o taco-install.sh && bash taco-install.sh
```

进入中文菜单，选择「1 一键安装」，按提示填写部署目录、已解析到服务器的域名和出口线路。脚本自动从 `https://github.com/tztmr/video_web_play.git` 拉取源码，安装所需依赖，构建容器与 Caddy HTTPS，再显示一次性管理员初始化链接。没有默认管理员密码。

菜单支持更新、修改域名和线路、状态、日志、启停、备份、初始化链接、版本检查、地址库更新与保留数据卸载。再次执行 `bash taco-install.sh` 会记住部署目录；安装目录中的 `bash deploy.sh` 也可打开菜单。

默认海外代理模式不会自动退回直连；海外服务器可显式选择自己的出口：

```bash
bash taco-install.sh --install --directory /opt/taco-cinema --domain video.your-domain.com --direct
```

**默认禁止大陆 IP 访问网站、API 和服务器视频接口，分享链接也受限制。** 港澳台不在屏蔽范围内，本机 `127.0.0.1` / `::1` 可继续本地观看。地址库随部署下载，按公网出口 IP 判断。浏览器直连的上游 CDN 不受网站控制；已经取得的视频地址或内容无法由网站远程收回。说明与运维命令见 [DEPLOYMENT.md](DEPLOYMENT.md)。

## 启动

需要 Python 3.11+、FFmpeg（包含 ffprobe）。`vendor/hongguo/` 已包含所需的 API、签名、解密和兼容播放模块，无需另行克隆下载器。

首次安装：

```bash
cd video_web_play
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
# macOS 未安装 FFmpeg 时：brew install ffmpeg
.venv/bin/python scripts/update_geoip.py
```

双击 `start.command`，或在终端运行：

```bash
.venv/bin/python server.py
```

打开 **http://127.0.0.1:8787**，默认显示登录页面。首次启动后，双击 `setup.command` 打开一次性初始化页面，设置自己的管理员账号和密码；也可运行 `.venv/bin/python scripts/setup_link.py --open`。没有默认账号或默认密码，初始化完成后链接立即失效。

终端按 Ctrl+C 停止。端口被占用时使用 `--port 8788`，同时初始化工具需要 `--base-url http://127.0.0.1:8788`。请使用 `.venv/bin/python`，确保已安装 SOCKS 代理依赖。

开发时也可以显式使用其他版本的下载器源码：

```bash
HONGGUO_SOURCE_DIR='/你的路径/红果视频下载' python3 server.py
```

程序自动在 PATH 查找 ffmpeg、ffprobe。也可设置 `HONGGUO_PLAYBACK_TOOLS_DIR` 为同时包含这两个可执行文件的绝对目录。

## 默认海外线路

本机启动时自动读取 v2rayN 当前选中节点，启动网站专用的本地 Xray SOCKS 入口，将后端上游 API、兼容模式视频请求路由到该节点；不会修改 v2rayN 的绕过大陆规则。节点配置仅通过子进程标准输入传递，不另存凭据文件。更换节点后重启网站生效。

其他环境需设置 `HONGGUO_UPSTREAM_PROXY=socks5://代理地址:端口`（应为明确的海外线路）。服务本身部署在海外主机时，可明确设置 `HONGGUO_NETWORK_MODE=direct` 使用该主机出口。没有可用海外配置时启动会报错，不自动回退。代理仅作用于后端请求；源站直连视频和封面遵循观众设备的网络/代理规则，网页不会更改这些规则。

## 已实现

- 统一入口拦截大陆 IPv4/IPv6；地址库异常或无法判定地区时拒绝公网访问。
- 默认登录；密码使用 scrypt 加盐哈希，Cookie 会话可撤销；登录失败次数限制。
- 管理中心创建/停用观众、重置密码；停用或重置会撤销原会话。
- 管理员在视频下方设置分享天数并生成链接，默认 0 天永久有效；只允许观看指定短剧，可随时撤销。
- 页面、列表 API、普通播放接口均验证登录；分享地址解析和服务器视频请求单独验证范围及有效期。
- 真人短剧、漫剧、AI 剧场浏览，题材筛选与分页。
- 真人短剧、漫剧关键词搜索；各类型人气榜单及翻页。
- 原生视频播放器、完整剧集目录、上一集/下一集、自动连播。
- 清晰度切换、倍速、全屏、进度拖动；清晰度不足时显示实际播放档位。
- 收藏、观看记录、继续观看，按账号隔离保存在当前浏览器的 localStorage。
- 深色页面，适配桌面和手机尺寸。
- 默认「源站直连」：浏览器 Worker 取片与解密，不触发服务器视频下载、转码、缓存或下一集预下载。
- 直连失败时显示原因，由观众手动选择「兼容模式」；不会自动切到服务器传输。
- 兼容模式复用原服务器流式播放和缓存，支持前台任务优先、下一集预下载以及四段 Range 下载；该模式会消耗服务器视频流量。
- 鼠标停留在剧目上时预取目录和播放模型，节省点击后的串行等待；不提前传输视频文件。
- 后端默认使用海外线路；播放器与管理中心不展示播放体验测试。

## API 与播放流程

网站导入 `vendor/hongguo/` 中的 `core.http_client.PureSignedClient`、`endpoints.duanju`、`endpoints.web_catalog` 和兼容转码模块。签名、设备注册、解析、密钥派生复用原逻辑；浏览器 CENC/AES-CTR 解密实现与原 Python 算法做逐字节比对。运行模块按固定提交纳入本仓库，来源与文件校验值见 `vendor/hongguo/UPSTREAM.md` 和 `manifest.json`，不包含下载器的账号、设备池或配置。

1. 官网分类使用原项目的 SSR 页面解析器；搜索、榜单、剧集目录走签名 API。
2. 默认 `POST /api/play/source?item_id=...&definition=720p&hevc=true` 只返回该集的源站地址、播放密钥和实际清晰度；`hevc` 由浏览器检测结果决定。分享页使用 `/api/shared/{token}/source`，先检查链接有效期和剧集范围，解析完成再检查有效期。
3. 浏览器 Worker 直接向 HTTPS 视频 CDN 取片，通过 WebCrypto 解密，原生播放器读取本地 Blob；拖动不经过网站服务器。切集终止旧 Worker 并释放 Blob。该模式不会调用服务器预下载接口。
4. 仅当观众手动选择「兼容模式」，才调用原 `/api/play/stream` 或 `/api/shared/{token}/stream`：服务器下载、解密、FFmpeg 转成 H.264/AAC，浏览器通过 MediaSource 边转换边播放。不支持 MediaSource 时使用完整文件接口。
5. 兼容模式转换成功后发布前置索引 MP4 缓存；下一集预下载、共享任务和 HTTP Range / 206 均沿用原流程。

直连需要 HTTPS（本机 localhost 例外）、允许跨域读取的源站，以及设备可解码的 H.264 或 HEVC 视频；ByteVC2 不在浏览器直连支持范围。当前直连先完整获取一集再解密播放，单集上限 256 MiB，速度取决于观众到源站的网络，重开会重新取片。没有匹配格式、跨域失败或文件过大时会提示手动选择兼容模式，不自动回退。网站静态文件、目录和登录请求仍产生少量服务器流量，不能称为零流量。

分享撤销/到期会阻止获取新的播放描述，分享页每 15 秒检查状态并停止播放。但已发送给浏览器的地址、密钥和视频字节不能收回，直连源站也不会逐字节经过网站的地区检查。

所有运行数据在本项目 `.data/`：SQLite 账号/会话/分享/测试记录、独立设备池、诊断日志和视频缓存，不改动原下载器的数据。可用 `HONGGUO_WEB_DATA_DIR` 指定数据目录。缓存按最近使用时间清理，目标上限 2 GB，24 小时未使用的文件在启动或准备下一集时清理；正在准备的单个大文件会被保留。停止服务后可删除 `.data/videos/` 清空视频。不要公开提交 `.data/`，删除整个目录会同时删除账号与分享。

本地默认只监听 `127.0.0.1`，所以本地生成的分享链接只能在这台机器上访问。服务器使用 Docker Compose + Caddy HTTPS 部署后，链接会自动使用访问时的域名。浏览和首次播放需要网络，不能仅双击 HTML 文件运行，也不是完整离线片库。

## 验证

```bash
python3 -m unittest discover -s tests -v
bash -n deploy.sh
node --input-type=module --check < static/app.js
```

启动时会补齐旧缓存的索引；只重新封装，不重新编码。

自动化用例覆盖安装菜单、更新保护、在线备份、大陆访问限制、默认登录、初始化一次性、密码/会话、管理权限、停用与重置、分享范围/过期/撤销、直连不下载视频、JS/Python 解密一致性、视频 Range、播放记录、缓存与取消。解密一致性用例需要 Node.js。真实上游/浏览器检查见 `VALIDATION.md`。
