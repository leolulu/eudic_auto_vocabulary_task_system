# 部署、验收与维护

## 当前阶段与待办

2026-10-29：1.1.0 已作为公开无鉴权接口发布，匿名跨域请求与父项目 Python HTTP 客户端联调均已通过。当前地址和部署 ID 见 [DEPLOYMENT](DEPLOYMENT.md)。1.0.0 的鉴权版本保留为历史记录。

- [x] 从 POC 提取自包含百度签名脚本和轻量运行环境，消除父目录生成依赖。
- [x] 建立公开 HTTP 接口、配置检查、CORS、结构化错误与日志。
- [x] 串行签名、复用百度签名脚本；失败后重建；限制实例内不同单词的在途查询。
- [x] 完整 SSE 检查、读取上限、总超时、有限重试、正常有/无视频缓存。
- [x] 固定 pnpm 锁文件，提供标准 Netlify 目录和部署说明。
- [x] 完成离线与真实本地验证，保留验证记录。
- [x] 选择正式 Netlify Project、域名和目标部署环境。
- [x] 按用户要求移除鉴权，并清理服务端与调用方的旧密钥配置。
- [x] 部署并完成云端验收，记录部署 ID 和请求 ID。
- [x] 验证百度签名脚本首次加载、同词并发合并、不同词查询及缓存；父项目改用 HTTP 客户端并完成联调。
- [ ] 持续观察错误率、实际 CPU 和套餐用量，验证长期稳定性。

用户随后明确授权了正式部署和父项目接入，本轮已执行。没有执行 Git add / commit / push 或其他 Git 写操作，也没有创建欧路或滴答记录。

## 首次部署

按 [README](../README.md) 的 Base / Build / Publish 设置创建项目。推荐新建正式 Project，避免把历史 POC 和正式发布混在一起。代码中未绑定任何 Netlify site ID，也没有部署时自动选站点的脚本。

默认配置无需环境变量。若要调整超时、缓存或 CORS 来源，在 Netlify UI 中配置包含 Functions scope 的运行时变量，再发起部署。来源：[Netlify 环境变量说明](https://docs.netlify.com/build/edge-functions/environment-variables/)。

当前仓库的根目录还有 Python 项目及忽略的用户配置。Netlify 的 Base 应明确设为 `baidu-video-service`；静态发布目录只选 `public`。若将子目录作为单独仓库交付，根目录直接使用子项目现有 `netlify.toml`，无需父仓库文件。

生产 API 的访问策略需允许预期调用方到达边缘函数。Netlify 的项目登录保护是另一层入口控制；如果 HTTP 401 的正文是 HTML 登录页，并且没有本服务的 `X-Request-Id` 和 JSON，先检查平台访问保护。不要把这种结果归为百度或签名故障。历史 POC 使用过登录保护，本次没有变更它。

## 云端验收

从本地子项目直接执行：

```powershell
pnpm smoke https://<your-project>.netlify.app
```

该命令向指定 origin 发送不带鉴权头、忽略 Cookie 的真实请求。远端强制 HTTPS。脚本默认验收服务的默认缓存配置；若主动关闭缓存，缓存命中断言会失败，需要按实际部署配置判断。

验收包含：匿名健康检查 200、缺参数 400、不支持方法 405、同词并发请求、`evidence` / `feature` / `apricot` 真实查询、公开跨域及缓存命中。示例词的视频覆盖会随百度变化；`evidence` 的“应有视频”断言如失败，应核对业务结果，不能直接推断部署失败。

记录验收日期、正式项目和部署 ID、默认域名/自定义域名、请求 ID、结果与 wall-clock 时长。测试没有下载完整视频，调用方若要播放或下载，应另行验证返回 CDN 地址在自己的网络可用。

上线前至少检查一次新部署的首个业务请求和少量不同单词并发。实例内缓存与复用不能保证跨请求始终存在，验收时不应把某次命中当作全局缓存承诺。

## 日志和故障判断

每个正常进入响应收尾的请求写一条 `baidu_word_video` 结构化日志：`requestId`、HTTP 状态、`failure`、缓存结果和 `elapsedMs`。不记录 API Key、Acs-Token、BAIDUID、单词原文、原始百度流或堆栈。百度签名脚本沙箱的 console 输出被关闭。

| 现象 | 定位与处理 |
| --- | --- |
| `/healthz` 返回 `CONFIGURATION_ERROR` | 核对可选配置的数值范围、CORS 来源格式和部署环境；修改后重新部署 |
| HTML 登录页 / 平台错误页 | 先看 Netlify 访问保护或平台日志，可能尚未进入业务函数 |
| `ACS_REJECTED` / `VALIDATION_FAILED` | 核对同版本百度签名脚本在本地和云端的表现；不要把失败缓存成无视频 |
| `QUOTA_EXCEEDED` | 暂缓调用，观察 `Retry-After`；服务不会通过轮换匿名 ID 再次重试 |
| `FORMAT_ERROR` | 可能是流截断、响应过大、格式变化或字段变化；保留脱敏结构样例后更新解析测试 |
| `BUSY` | 当前实例的不同单词查询达到上限，稍后重试；同词请求仍会合并 |
| `TIMEOUT` / `UPSTREAM_TIMEOUT` / `SIGNER_TIMEOUT` | 结合请求 ID、平台日志及调用方网络时长区分总查询、百度网络与签名器等待 |

`/healthz` 只检查配置，不承诺百度可用。上游业务失败或格式变化与正常无视频具有不同的 HTTP 状态和 `ok` 值，调用方必须保留这个区别。

## 资源与稳定性

截至 2026-10-29 查阅的 [Netlify 官方限制](https://docs.netlify.com/build/edge-functions/limits/)：压缩代码上限 20 MB、每组函数内存 512 MB、请求 CPU 50 ms、响应头超时 40 秒。CPU 不包含资源/网络等待。默认总查询时限设为 20 秒，可配置上限 30 秒。

本地 wall-clock 和 POC 的成功 HTTP 请求没有提供 Netlify CPU 独立计数。1.0.0 本地 ESZIP 约 471 KB，只说明体积充足；真实平台 CPU、冷启动和持续负载仍需在目标部署观察。平台级执行中止可能直接返回平台错误页，不能保证进入本服务的 JSON 错误处理。

同一实例一次只调用一个百度签名脚本签名任务，HTTP 查询可交叠；默认最多四个不同单词在途。缓存最多 256 条，有视频默认一小时，无视频默认五分钟，错误不缓存。同词合并和这些限制均为实例内行为。调用规模增长后，可根据实际用量选择平台或外部的全局限流方案。

## 维护百度签名脚本与回滚

百度签名脚本来自父项目的既有固定版本，校验记录在 [sdk-integrity.json](sdk-integrity.json)。`pnpm build` 会检查原始百度签名脚本字符串、模块摘要和运行代码的本地导入边界。更新百度签名脚本时同步维护来源、摘要和真实查询记录，不能只修改摘要让未知代码通过。

百度签名脚本或环境层变化后，重新运行离线测试、Node 和 Deno 真实签名/查询，再检查 Edge 打包，最后验收目标 Netlify 部署。不要恢复包含完整 jsdom 的 13 MB Node bundle；历史 POC 已验证轻量浏览器环境可用，旧大包上传还遇到过 413。

生产部署异常时，可在 Netlify 部署列表选择已验收的前一个正式部署恢复服务；环境变量与对应部署的配置要一并核对。当前子项目没有自动发布、自动回滚或删除远端资源的行为。

## 父项目当前接入

父项目入口现为 `utils/baidu_video_client.py` 的 `query_word_explanation_video()`，`agent/dida365.py` 已改为导入此入口。只需通过父项目 `config.yaml` 的 `baidu_video_service_url` 配置地址，或通过 `BAIDU_VIDEO_SERVICE_URL` 环境变量覆盖。真实配置中的旧 `baidu_video_api_key` 已移除，客户端不再读取或发送密钥。

`ok:true, hasVideo:false` 返回空列表；网络、非 2xx、JSON 格式或 `ok:false` 抛出明确异常，由原调度层记录并重试。已移除本地 Node 查询和视频 Playwright 回退。旧 POC 已在最终整理时移除，历史 CLI 的 Git 来源见 [迁移说明](PROVENANCE.md)。正式服务不依赖该输入。百度音标仍保留其原有 Playwright 路径。视频下载及滴答附件上传继续由父项目承担。

以下命令从**父项目根目录**执行，使用其已有项目内 Python 环境和已配置的服务地址：

```powershell
$env:PYTHONUTF8 = '1'
$env:UV_CACHE_DIR = Join-Path (Get-Location) '.cache/uv'
uv run --python .venv/Scripts/python.exe test/manual_baidu_video_service.py
```

该脚本检查健康接口、实际单词查询和 CDN 前 1024 字节。将 `--local` 加到命令末尾，会临时启动本地独立服务，完成后停止；将 `--download` 加到命令末尾，会额外走原附件流程，完整下载 evidence 视频到内存。两种模式都不创建欧路或滴答记录，也不上传附件。`--output <项目内路径>` 可指定报告位置，默认写入父项目 `.cache/baidu-video-integration.json`。
