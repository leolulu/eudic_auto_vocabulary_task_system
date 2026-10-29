# 验证记录：2026-10-29

## 当前 1.1.0：公开无鉴权

用户明确要求公开、无需鉴权后，1.1.0 最初于北京时间 12:20:09 发布，部署 ID 为 `6ae2c91fe0342fb995d9d601`。后续首页精简已于 13:50:19 发布到 `production`，当前部署 ID 为 `6ae2de8e28a89d08b9a2e24f`，详见 [DEPLOYMENT](DEPLOYMENT.md)。两次复用同一份 Edge 产物，470,195 字节，SHA-256 为 `b00f1a8340dba849b40ff47a5f06977e15b210aef7dbf6d20283c7e0c6e2c4c5`。

服务端 28 项测试通过；Python 回归共 174 项，173 项通过、1 项跳过。新增验证覆盖：不设置任何密钥仍可查询、旧鉴权头被忽略、客户端不发送 Authorization、默认跨域 GET 与 OPTIONS 预检。

Chrome 从 `app.netlify.com` 使用 `credentials: omit` 跨域读取了预览接口的 JSON，evidence / feature 有视频、apricot 无视频、缺参数返回 400，自定义 `X-Client` 头触发的真实浏览器预检通过。正式域名下 Python 客户端未带鉴权信息，健康检查返回版本 1.1.0 和 CORS `*`；evidence 651 ms、feature 633 ms、apricot 624 ms，CDN 返回 206 和有效 MP4 文件头。

证据：

- [公开版本部署与清理记录](validation/2026-10-29-public-production-deploy.json)
- [匿名跨域云端验收](validation/2026-10-29-public-netlify-preview.json)
- [无需密钥的本地 Python 联调](validation/2026-10-29-public-python-local.json)
- [无需密钥的正式域名 Python 联调](validation/2026-10-29-public-python-production.json)

一次浏览器探针尝试直接读取 `Access-Control-Allow-Origin`，得到 null；跨域 JSON 已可读取，该响应头本身未列在暴露给 JS 的响应头中。最终通过真实自定义头请求验证浏览器自动预检，并由 Python 读取到响应头 `*`。没有为探针添加额外产品行为。

以下保留 1.0.0 阶段的历史测试，包括当时的 401 记录和完整视频下载证据；当前接口以本节及 [DEPLOYMENT](DEPLOYMENT.md) 为准。

## 最终提交前复核

源码整理后再次通过主项目 174 项回归（173 通过、1 跳过）、子项目 28 项测试、完整性和导入边界检查、Edge 打包。新生成 ESZIP 的大小与 SHA-256 均与已发布的 1.1.0 一致，本轮无需重新部署。

北京时间 13:38 的公开接口复查通过：健康检查 200、CORS `*`、evidence / feature 各返回一个视频、apricot 无视频；CDN Range 返回 206，读取 1024 字节并确认 MP4 文件头。POC 历史输入恢复校验、调研摘要原件哈希、当前文档链接和 `git diff --check` 均通过，配置、会话及数据库文件的大小和修改时间未变。

精简证据见 [最终复核记录](validation/2026-10-29-final-readiness.json)。当时原始抓取、旧大包、重复产物和测试缓存集中到父项目已忽略的 `.cleanup-review/2026-10-29-baidu-video/`，供本机人工审阅；随后用户在最终清理中移除了该目录、旧 POC 和调研目录。最终交付保留本项目源码、锁文件、说明及本目录中的精简验证历史，历史 JSON 中的旧路径仅作当时记录。上述复核阶段没有 Git 写操作，也没有创建欧路或滴答记录。

用户确认线上状态并授权提交后，再次通过主项目 174 项离线回归（173 通过、1 跳过）和子项目 28 项测试及完整性检查。清理了当前文档中对已删除 POC 路径的引用，并通过 `.gitattributes` 固定冻结脚本的 LF 检出格式；只读模拟 Git 检出后的字节哈希与完整性清单一致。无需修改接口或重新部署。

## 首页精简与 Production 更新

按用户要求，`public/index.html` 已删除服务名称标题和公开查询说明，仅保留 `/healthz` 链接，浏览器标题改为“健康检查”。本地页面结构检查通过，直接打开正式域名的浏览器可见文本也仅有健康检查，无标题或查询链接。

新部署 `6ae2de8e28a89d08b9a2e24f` 的类型为 `production`；上传时只需要新首页，Edge 产物由平台复用。匿名健康检查和 evidence 查询均返回 200，API 的 CORS 为 `*`，接口版本仍为 1.1.0。见 [本轮精简证据](validation/2026-10-29-minimal-landing.json)。此次未改运行代码，沿用已通过的回归及产物校验。

Netlify 会在静态响应末尾追加自身的 HUD 脚本，因此最终响应哈希与本地 HTML 不同；已核对本地内容完整匹配响应前缀，额外内容只包含该平台脚本。静态首页不提供跨域读取头，从管理页面跨域 fetch 首页会失败；验证改用直接导航及 Python，API 跨域健康检查仍成功。旧部署专属 URL 仍返回旧首页，本次未删除历史部署。

## 输出兼容性核对

根据原 CLI 归档源码、POC 原始查询记录以及当前公开接口的 JSON，确认如下：

| 核对项 | 结果 |
| --- | --- |
| 原 CLI 顶层 | 数组；每个单词对应一个结果对象 |
| 当前 HTTP 顶层 | 单个对象；每次请求接收一个 `word` |
| 原 CLI 业务字段 | `ok`、`word`、`hasVideo`、`video`、`queryExplain`、`failure`、`errno`、`errmsg` |
| 当前成功响应变化 | 移除 `queryExplain`，新增 `cache`、`requestId`；核心视频字段保留 |
| `queryExplain` 与 `video` | 原 CLI 从前者提取、整理出后者，并同时返回两者；`queryExplain.videoUrl` 对应 `video.videoUrl` |
| `elapsedMs` 的来源 | POC 的 `query-local.mjs` 包装脚本额外加入，原 CLI 本身没有这个字段 |
| Python 调用方成功返回值 | 原来与现在均为视频 URL 列表，有视频 `[URL]`，正常无视频 `[]` |
| 失败处理 | 当前 HTTP 客户端抛出服务异常；原本地视频浏览器回退已移除 |

evidence 的七个固定视频字段已做过值对照，视频 URL、各图片 URL、77 秒时长、类型和标签均与原记录一致。新解析器对完整 SSE、字段类型和 URL 的要求更严格，异常响应的处理具有行为差异。字段提取与原始对象的取舍见 [README](../README.md) 的“输出兼容性与关键决策”。

## 1.0.0 历史记录

工作环境为 Windows / PowerShell，Node 22.18.0、pnpm 10.30.3；本地 Deno 为 2.4.2，位于本子项目缓存目录。以下时长均为 wall-clock，不能换算成 Netlify 的 CPU 计数。

## 已完成的检查

| 检查 | 结果 |
| --- | --- |
| `pnpm install --frozen-lockfile` | 锁文件一致，安装成功 |
| `pnpm run build` | 27 项测试全部通过；百度签名脚本字符串/模块摘要及 12 个运行模块的导入边界通过 |
| `pnpm run smoke:runtime` | Node 中的正式 handler 调用真实百度成功，9 个验收场景通过 |
| Deno 执行 `scripts/smoke-runtime.mjs` | 真实百度签名脚本 / node:vm / fetch / SSE 解析成功，同样 9 个场景通过 |
| `pnpm run smoke:local` | 临时 localhost HTTP 服务通过 9 个场景；进程已退出，测试密钥仅在内存中 |
| `pnpm run check:edge` | Netlify 官方 edge-bundler 16.1.3 输出 ESZIP 与两个路由，退出码 0 |
| 独立副本构建 | 仅复制子项目源码到 `.cache/standalone-check`，不复制父项目、POC、node_modules 或构建缓存；再次通过 27 项测试和完整性检查 |
| Git 忽略检查 | `src/`、百度签名脚本、环境模板和锁文件均列为可收集源码；依赖、缓存、产物被忽略 |

离线测试验证鉴权与参数、CORS、错误脱敏、无视频语义、SSE 完整性和格式变化、流大小上限、响应体超时、总超时、缓存 TTL 与 LRU、同词合并、调用方取消、实例繁忙、签名有限重试、并发串行与失败后恢复。测试不访问百度。

独立副本构建证明了所需源文件均已随子项目收集；开发检查用的 `@netlify/edge-bundler` 仍是声明在锁文件中的可安装开发依赖。正式运行源码只导入本地文件和 `node:vm`，无需父目录生成脚本。

## 真实查询结果

下表为同轮并发查询的单个请求时长；包括真实签名和百度响应。

| 运行方式 | evidence | feature | apricot | 同词请求 | 后续 evidence 缓存命中 |
| --- | --- | --- | --- | --- | --- |
| Node handler | 200，有视频，796 ms | 200，有视频，795 ms | 200，无视频，995 ms | `coalesced`，792 ms | `hit`，1 ms |
| Deno handler | 200，有视频，1617 ms | 200，有视频，1338 ms | 200，无视频，1231 ms | `coalesced`，1614 ms | `hit`，1 ms |
| 本地 HTTP | 200，有视频，1251 ms | 200，有视频，1258 ms | 200，无视频，1238 ms | `coalesced`，1251 ms | `hit`，7 ms |

evidence 返回 `E204-evidence.mp4`、77 秒；feature 返回百度 CDN 视频、62 秒。没有下载完整视频。上述词的覆盖和时间只是测试当时的观察。

各轮还验证了 `/healthz` 200、缺密钥 401、缺参数 400、POST 查询 405。冻结结果已去除临时测试密钥和签名令牌：

- [Node 真实 handler 结果](validation/2026-10-29-node.json)
- [Deno 真实 handler 结果](validation/2026-10-29-deno.json)
- [本地 HTTP 控制台结果整理](validation/2026-10-29-local-http.json)
- [Edge 构建、manifest 与警告](validation/2026-10-29-edge-build.json)

Deno 检查的可重复命令，从本子项目执行：

```powershell
. .\scripts\local-env.ps1
& .\.cache\deno-cli\deno.exe run --allow-net=fanyi.baidu.com --allow-env --allow-read=. --allow-write=.cache scripts/smoke-runtime.mjs
```

本地已验证的 Deno 可执行文件从 POC 的项目内缓存复制到本项目缓存。缓存未进入部署源码；新环境可由 `check:edge` 的工具链获取本地 Deno，或在项目内准备兼容版本。

## Netlify 构建产物

- ESZIP：**471,065 字节**。
- SHA-256：`3b47ddde6a5527a3c3b2a609dbfffcfc6f227bc7ac9079987cfae5c0619225e5`。
- 函数：`api`；路由：`/healthz`、`/v1/*`；`on_error: fail`。
- 百度签名脚本模块及源码均位于 `public/` 之外，静态发布目录只有说明页。

打包时 `edge.netlify.com` 在线类型版本检查出现 ConnectTimeout，工具记录警告后继续，最终成功生成上述产物，退出码为 0。没有屏蔽或修改打包器。日志中的 “Using global installation of Deno CLI” 指工具从 PATH 找到可执行文件；当次 PATH 优先指向本子项目的 `.cache/deno-cli/deno.exe`，并未安装全局 Deno。

## 正式部署与 Python 接入补充

上述首轮本地检查后，用户进一步授权正式部署。1.0.0 已发布到 `https://baidu-word-video-service-c7e3.netlify.app`，发布时间为 2026-10-29 10:11:00 北京时间；部署 ID 为 `6ae2aabcab9097cc97d3b20f`。

云端预览测试确认：配置就绪、有视频、无视频、同词并发合并、缓存命中和缺密钥 401 均正常；首轮并发中的业务响应约 1.29–1.49 秒。Python 客户端随后从真实父项目配置读取地址与密钥，使用正式域名完成鉴权和真实查询；evidence 1232 ms、feature 1072 ms、apricot 892 ms，CDN 返回 206 和有效 MP4 文件头。

新增证据：

- [正式部署记录](validation/2026-10-29-production-deploy.json)
- [云端预览验收](validation/2026-10-29-netlify-preview.json)
- [Python 客户端对本地服务联调](validation/2026-10-29-python-local.json)
- [Python 客户端对正式域名联调](validation/2026-10-29-python-production.json)
- [原附件流程完整视频下载](validation/2026-10-29-python-production-full.json)

原附件流程完整下载了 `E204-evidence.mp4`，84,512,943 字节，耗时 41,642 ms，SHA-256 为 `f878f4e95e3a1e84f8a4faa5fcc17d1bb33a2fce15c1e9d4183055e2d026c36d`，MP4 文件头有效。视频只保留在测试内存中，没有上传滴答。

父项目全量离线回归共 173 项，172 项通过、1 项跳过；服务端 27 项测试及构建检查通过。具体部署方式和套餐差异记录在 [DEPLOYMENT](DEPLOYMENT.md)。

没有进行长期压测、跨地区可靠性、套餐实际用量或独立 CPU 计数验证。本地 Deno 版本不代表云端版本。本轮只修改新正式项目的访问保护，历史 POC 仍保留原登录保护。没有执行 Git add、commit、push 或其他 Git 写操作，也没有写入欧路或滴答。
