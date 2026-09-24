# 第三方运行模块与数据

## DB-IP Lite 国家地址库

IP Geolocation by [DB-IP](https://db-ip.com)。

- 数据来源：[IP to Country Lite](https://db-ip.com/db/download/ip-to-country-lite)。
- 数据许可：[Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/)。
- 部署时从提供商 HTTPS 地址下载当月 MMDB，不把本机地址库提交到 Git。下载器保存提供商、版本月份、下载时间和 SHA-256 到同目录 JSON 文件。
- 地址库原样使用；网站根据国家代码 `CN` 拒绝访问，不改变数据内容。各页面保留提供商署名链接。
- Lite 数据每月更新，覆盖和精度有限；不能据此识别使用代理的访客实际所在地。

## 红果播放模块

固定版本、来源与文件校验记录见 [vendor/hongguo/UPSTREAM.md](vendor/hongguo/UPSTREAM.md) 和同目录 `manifest.json`。本次仅增加网站入口限制与部署管理，没有修改这些模块。
