# 公开接口部署及父项目接入：2026-10-29

## 当前服务：1.1.0

| 项目 | 值 |
| --- | --- |
| 直接查询示例 | [查询 evidence](https://baidu-word-video-service-c7e3.netlify.app/v1/video?word=evidence) |
| 查询地址 | `https://baidu-word-video-service-c7e3.netlify.app/v1/video` |
| 健康检查 | [healthz](https://baidu-word-video-service-c7e3.netlify.app/healthz) |
| 鉴权 | 无；不需要 API Key、Authorization 或 Cookie |
| 默认跨域 | `Access-Control-Allow-Origin: *` |
| Netlify Project | [baidu-word-video-service-c7e3](https://app.netlify.com/projects/baidu-word-video-service-c7e3) |
| Site ID | `bc8085ef-8257-46a8-a572-28a79ffc6157` |
| Deploy ID | `6ae2de8e28a89d08b9a2e24f` |
| 部署详情 | [Deploy details](https://app.netlify.com/projects/baidu-word-video-service-c7e3/deploys/6ae2de8e28a89d08b9a2e24f) |
| 部署类型 | `production` |
| 发布时间 | `2026-10-29T05:50:19.673Z`，北京时间 13:50:19 |
| 产物 | 470,195 字节，SHA-256 `b00f1a8340dba849b40ff47a5f06977e15b210aef7dbf6d20283c7e0c6e2c4c5` |

当前部署通过用户自己的 Chrome MCP 和现有 Netlify 登录态，以 `draft:false` 创建并发布，类型为 `production`。本次只更新静态首页，复用经过校验的 1.1.0 Edge 产物；API 版本、无鉴权、跨域和业务逻辑均保持原样。首页及浏览器标题只保留健康检查，不再展示服务名称或查询用法。

此前北京时间 12:20:09 发布的部署 `6ae2c91fe0342fb995d9d601` 采用先草稿验收再发布的流程，其 `context` 仍为 `deploy-preview`，现已由上表的新部署替换。带旧部署 ID 的专属 URL 是版本快照，仍保留旧首页；本次未删除历史部署。查看最新页面应使用 [正式首页](https://baidu-word-video-service-c7e3.netlify.app/)。本次页面更新及匿名验收见 [精简记录](validation/2026-10-29-minimal-landing.json)。

当前项目使用手工产物部署，尚未关联 Git 持续部署。后续源码改动需要重新构建并发布，或按 README 配置 Git 部署。部署阶段未创建 Netlify PAT；Git 提交与线上发布分别处理。

## 调用方式

直接 GET 即可：

```powershell
curl.exe "https://baidu-word-video-service-c7e3.netlify.app/v1/video?word=evidence"
```

普通网页可跨域调用：

```javascript
const data = await fetch(
  "https://baidu-word-video-service-c7e3.netlify.app/v1/video?word=evidence",
  { credentials: "omit" }
).then(response => response.json());
```

正常无视频返回 HTTP 200 和 `ok:true, hasVideo:false, video:null`；网络、上游、参数和格式错误仍明确区分。百度签名脚本签名、超时、实例内并发限制和缓存逻辑保持原样。

## 配置与已完成的清理

服务端使用默认配置即可启动。Netlify 项目的网页登录门槛保持关闭，浏览器无需登录。可选的缓存、超时及 CORS 配置见 [README](../README.md)。

父项目只需在忽略的 `config.yaml` 中保留 `baidu_video_service_url`，或使用 `BAIDU_VIDEO_SERVICE_URL` 环境变量覆盖。客户端 `utils/baidu_video_client.py` 不读取密钥，也不发送 Authorization。

按用户明确要求，旧鉴权配置已清理：

- 删除 Netlify 此站点的 `BAIDU_VIDEO_API_KEY` 环境变量，API 返回 204。
- 删除父项目 `config.yaml` 中新增的 `baidu_video_api_key`，服务地址和其他配置保留。
- 删除项目缓存 `.cache/production/service-key.json`。
- 删除源码模板和新建配置模板中的密钥要求，更新所有实际执行的验收脚本。

初始 1.0.0 的鉴权由实现者自行加入。用户明确要求公开无鉴权后，1.1.0 已移除此限制。原部署背景、套餐范围限制和旧版本验证保存在 [1.0.0 历史快照](history/DEPLOYMENT-1.0.0.md)及旧 JSON 记录中，接手时以本文件的当前状态为准。

## 验证结果

- 服务端 28 项测试通过，百度签名脚本完整性检查和 Netlify 打包通过。
- Python 回归共 174 项：173 项通过、1 项跳过。
- Chrome 从 Netlify 管理页跨域请求草稿，所有业务请求使用 `credentials: omit`，无鉴权头；有视频、无视频、参数错误和自定义请求头触发的浏览器预检均通过。
- 父项目 Python 客户端直接读取仅有地址的正常配置，对正式域名完成无鉴权联调：evidence / feature 返回视频，apricot 返回空列表。
- Python 读取到公开 CORS 响应头 `*`；CDN 返回 206、有效 MP4 文件头和前 1024 字节。

本轮没有重复完整视频下载。1.0.0 接入阶段已通过原附件流程完整下载 84,512,943 字节的 evidence MP4；此次视频下载代码保持原样。

证据见 [VALIDATION](VALIDATION.md) 的 1.1.0 章节和 `validation/2026-10-29-public-*.json`。联调只执行查询和视频读取，没有创建欧路或滴答任务。父项目不再运行本地 Node 视频查询或视频 Playwright 回退；历史 CLI 保留在 Git 提交 `277444abbfd5266e9d4c32c31bdb65368ace2dc9` 的 `vendor/baidu-video.mjs`，当前服务不依赖旧大包。
