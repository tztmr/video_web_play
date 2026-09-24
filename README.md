# TACO小剧场

基于「红果视频下载」API 与播放算法的短剧网站。所需 Python 运行模块已随仓库提供，克隆本项目即可独立运行。默认登录后浏览和观看，管理员可创建观众账号、生成免登录分享链接。无需启动原桌面 App。

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

**默认禁止大陆 IP 访问所有网页、API 和视频，分享链接也受限制。** 港澳台不在屏蔽范围内，本机 `127.0.0.1` / `::1` 可继续本地观看。地址库随部署下载，按公网出口 IP 判断；说明与运维命令见 [DEPLOYMENT.md](DEPLOYMENT.md)。

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

本机启动时自动读取 v2rayN 当前选中节点，启动网站专用的本地 Xray SOCKS 入口，将网站上游 API、视频请求全部路由到该节点；不会修改 v2rayN 的绕过大陆规则。节点配置仅通过子进程标准输入传递，不另存凭据文件。更换节点后重启网站生效。

其他环境需设置 `HONGGUO_UPSTREAM_PROXY=socks5://代理地址:端口`（应为明确的海外线路）。服务本身部署在海外主机时，可明确设置 `HONGGUO_NETWORK_MODE=direct` 使用该主机出口。没有可用海外配置时启动会报错，不自动回退。默认代理仅作用于后端请求；浏览器封面请求仍遵循浏览器自己的网络设置。

## 已实现

- 统一入口拦截大陆 IPv4/IPv6；地址库异常或无法判定地区时拒绝公网访问。
- 默认登录；密码使用 scrypt 加盐哈希，Cookie 会话可撤销；登录失败次数限制。
- 管理中心创建/停用观众、重置密码；停用或重置会撤销原会话。
- 管理员在视频下方设置分享天数并生成链接，默认 0 天永久有效；只允许观看指定短剧，可随时撤销。
- 页面、列表 API、普通视频文件均验证登录；分享视频每次请求单独验证范围及有效期。
- 真人短剧、漫剧、AI 剧场浏览，题材筛选与分页。
- 真人短剧、漫剧关键词搜索；各类型人气榜单及翻页。
- 原生视频播放器、完整剧集目录、上一集/下一集、自动连播。
- 清晰度切换、倍速、全屏、进度拖动；清晰度不足时显示实际播放档位。
- 收藏、观看记录、继续观看，按账号隔离保存在当前浏览器的 localStorage。
- 深色页面，适配桌面和手机尺寸。
- 当前集处理完成并开始播放后，后台只提前准备下一集；切入尚在准备的同一集可复用已产出的流。
- 前台切集优先，自动取消无观众使用的其他预加载；同一集的多个观看请求复用一次下载和转码。
- 海外取片采用 4 个互不重叠的 Range 分段并行下载，严格校验后重组；上游不支持或分段异常时回退原 CDN 下载流程。
- 视频连接保留 120 秒；鼠标停留在剧目上时预取目录和播放地址，节省点击后的串行等待。省流量模式不做预加载。
- 默认使用海外线路；播放器与管理中心不展示播放体验测试。

## API 与播放流程

网站导入 `vendor/hongguo/` 中的 `core.http_client.PureSignedClient`、`endpoints.duanju`、`endpoints.web_catalog` 和兼容转码模块。签名、设备注册、解析、密钥派生、解密、编码选择均复用原逻辑；运行模块按固定提交纳入本仓库，来源与文件校验值见 `vendor/hongguo/UPSTREAM.md` 和 `manifest.json`，不包含下载器的账号、设备池或配置。

1. 官网分类使用原项目的 SSR 页面解析器；搜索、榜单、剧集目录走签名 API。
2. `POST /api/play/stream?item_id=...&definition=720p` 加入按剧集/清晰度复用的播放任务，复用原项目解析、选源、密钥派生和解密算法。
3. 原算法解析播放模型、选择兼容编码、派生密钥、下载并解密视频，FFmpeg 生成 H.264/AAC MP4。
4. 兼容转换输出 fragmented MP4，任务写入临时文件并通知流式读取者，浏览器通过 MediaSource 逐段追加；完整成功后只复制音视频流、生成前置索引的 MP4，再原子发布缓存；避免原生播放器对流式缓存反复扫描。
5. 后台 `/api/play/prefetch` 提前准备下一集，分享页使用单独的 `/api/shared/{token}/prefetch` 并检查剧集范围和链接有效期。
6. 文件接口支持 HTTP Range / 206，因此浏览器可以拖动进度。

首播仍需下载并解密当前一集，但无需等待整集转码结束即可开始播放。不支持 MediaSource 的浏览器自动使用完整文件接口 `/api/play`。重复观看复用缓存，切换清晰度会单独准备。上游无可解码的视频源或接口失效时，会显示原始诊断并允许重试。

所有运行数据在本项目 `.data/`：SQLite 账号/会话/分享/测试记录、独立设备池、诊断日志和视频缓存，不改动原下载器的数据。可用 `HONGGUO_WEB_DATA_DIR` 指定数据目录。缓存按最近使用时间清理，目标上限 2 GB，24 小时未使用的文件在启动或准备下一集时清理；正在准备的单个大文件会被保留。停止服务后可删除 `.data/videos/` 清空视频。不要公开提交 `.data/`，删除整个目录会同时删除账号与分享。

本地默认只监听 `127.0.0.1`，所以本地生成的分享链接只能在这台机器上访问。服务器使用 Docker Compose + Caddy HTTPS 部署后，链接会自动使用访问时的域名。浏览和首次播放需要网络，不能仅双击 HTML 文件运行，也不是完整离线片库。

## 验证

```bash
python3 -m unittest discover -s tests -v
bash -n deploy.sh
node --input-type=module --check < static/app.js
```

启动时会补齐旧缓存的索引；只重新封装，不重新编码。

自动化用例覆盖安装菜单、更新保护、在线备份、大陆访问限制、默认登录、初始化一次性、密码/会话、管理权限、停用与重置、分享范围/过期/撤销、视频 Range、播放记录、缓存与取消。真实上游/浏览器检查见 `VALIDATION.md`。
