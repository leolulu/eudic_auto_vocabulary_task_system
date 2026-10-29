> 历史快照：1.0.0 鉴权版本。当前公开无鉴权版本见 [DEPLOYMENT](../DEPLOYMENT.md)。旧密钥配置已清理。

# 正式部署及父项目接入：2026-10-29

## 当前服务

| 项目 | 值 |
| --- | --- |
| 正式查询地址 | `https://baidu-word-video-service-c7e3.netlify.app/v1/video` |
| 健康检查 | [healthz](https://baidu-word-video-service-c7e3.netlify.app/healthz) |
| Netlify Project | [baidu-word-video-service-c7e3](https://app.netlify.com/projects/baidu-word-video-service-c7e3) |
| Site ID | `bc8085ef-8257-46a8-a572-28a79ffc6157` |
| Deploy ID | `6ae2aabcab9097cc97d3b20f` |
| 部署详情 | [Deploy details](https://app.netlify.com/projects/baidu-word-video-service-c7e3/deploys/6ae2aabcab9097cc97d3b20f) |
| 发布时间 | `2026-10-29T02:11:00.614Z`，北京时间 10:11:00 |
| 产物 | 471,065 字节，SHA-256 `3b47ddde6a5527a3c3b2a609dbfffcfc6f227bc7ac9079987cfae5c0619225e5` |

本次通过用户自己的 Chrome MCP 和现有 Netlify 登录态上传本地已构建的产物。先创建草稿、验收云端签名与真实百度查询，再通过发布接口将该部署设置为正式版本。Netlify 保留其来源 `context: deploy-preview`，但 `published_at` 和站点 `published_deploy` 已确认正式发布。

没有创建 Netlify PAT，没有读取浏览器 Cookie / localStorage，没有 Git 提交或推送。当前项目使用手工产物部署，尚未关联 Git 持续部署；后续源码改动需要重新构建并发布，或按 README 配置 Git 部署。

## 访问与密钥

查询必须带 `Authorization: Bearer <API_KEY>`。本轮生成新的随机密钥，写入该 Netlify 项目的 `BAIDU_VIDEO_API_KEY` 环境变量；密钥没有进入源码、静态目录或验证报告。

当前团队为 Free 套餐，API 拒绝指定细分 scopes；不可读取的 `is_secret` 标记与默认 `post_processing` scope 组合也被拒绝。因此最终使用套餐默认的普通服务端环境变量，范围包含 `builds`、`functions`、`post_processing`、`runtime`，context 为 `all`。没有升级套餐。这种变量可被有权限的项目管理员读取，不能将其误记为 Netlify 的不可读取 Secret 类型。[Netlify API 说明](https://open-api.netlify.com/#tag/Environment-variables/operation/createEnvVars)

新项目最初继承了 Netlify 登录保护。为允许 Python 机器调用，本轮只关闭了**新正式项目**的 SSO 登录门槛，服务自身 Bearer 鉴权保持启用。历史 POC 的 `sso_login` 和 `account_sso_login` 复查仍为 true。

本机配置位置：

- 父项目 `config.yaml`：追加 `baidu_video_service_url`、`baidu_video_api_key`，原有配置文本未重排或改写；该文件由父仓库忽略。
- 子项目 `.cache/production/service-key.json`：本轮生成的密钥副本，目录已忽略，勿上传或分享。
- 子项目 `.cache/production/site.json`：部署目标元数据。

生产服务不读取这些本机文件，运行时只读取 Netlify 环境变量。密钥轮换后应重新部署并更新调用方配置。

## 父项目变更

主项目新增 `utils/baidu_video_client.py`，`agent/dida365.py` 的导入已切换到该客户端。原 `utils/phonetic_util.py` 中的 Node 视频查询和 Playwright 视频回退已移除，音标逻辑保持原样。旧 13 MB Node bundle 已原样归档到 `poc/netlify-baidu-video/source/baidu-video.original.mjs`，POC 生成脚本已调整，不再依赖父项目 `vendor/`。

HTTP 客户端保持原来的 `list[str]` 返回接口，正常无视频为 `[]`；鉴权、网络、上游业务、HTML 登录页和 JSON / 字段错误抛出 `BaiduVideoServiceError`。已有调度层继续记录失败，避免把失败记为无视频。下载视频时不转发服务 API Key。

配置支持 `config.yaml` 和同名大写环境变量覆盖；服务 URL 可以是 origin 或完整接口路径。远端要求 HTTPS，本地回环地址允许 HTTP。未配置时明确失败，不再启动本地 Node 或视频浏览器。

## 验证与接手

见 [VALIDATION](../VALIDATION.md) 和 `validation/2026-10-29-*.json`。Python 客户端真实联调已经验证正式域名下的有视频、无视频、错误密钥和 CDN MP4 读取；云端预览另验证同词并发合并和缓存命中。随后原附件流程完整下载 `evidence` 视频，84,512,943 字节，约 41.6 秒，只存内存，没有上传滴答。原项目离线测试 172 项通过、1 项跳过；服务端 27 项通过。

继续检查可运行：

```powershell
$env:PYTHONUTF8 = '1'
uv run --python .venv/Scripts/python.exe test/manual_baidu_video_service.py
```

从父项目根目录运行，脚本使用已配置的正式服务，只做查询和 CDN 读取，不写入欧路或滴答。日志按原约定写到父项目 `logs/media.log` 与 `logs/errors.log`；密钥通过原日志脱敏机制处理。

长期可靠性、跨地区网络和真实 CPU / 套餐用量仍需持续观察。不要用本次成功测试推断长期可用性或全局缓存能力。
