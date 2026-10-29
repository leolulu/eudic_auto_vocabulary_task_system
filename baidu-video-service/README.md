# 百度单词讲解视频服务

面向 Netlify Edge Functions 的独立 HTTP 服务。输入英文单词或短语，返回百度讲解视频的 CDN 地址及封面、时长等元数据。视频下载由调用方负责。

**当前版本：1.1.0；2026-10-29 已发布为公开、无需鉴权的接口，并完成云端与父项目 Python 客户端联调。** 可直接打开 [查询 evidence](https://baidu-word-video-service-c7e3.netlify.app/v1/video?word=evidence)。部署身份及接入记录见 [DEPLOYMENT](docs/DEPLOYMENT.md)，测试证据见 [验证记录](docs/VALIDATION.md)。

正式首页仅显示健康检查链接，服务名称和接口用法保留在源码文档中。2026-10-29 的首页调整已作为 `production` 部署发布，API 仍为 1.1.0；带部署 ID 的历史快照页面保留原内容。

整个目录可以单独取出作为仓库。运行和构建均不读取父项目、POC、Python 配置或数据库。父项目已通过 `utils/baidu_video_client.py` 调用本服务，视频下载仍由父项目执行。

## 背景与范围

父项目负责把欧路生词加工成滴答单词任务。原来的讲解视频查询在本机启动 Node CLI，失败时回退 Playwright；视频地址查到后，再由 Python 下载并上传附件。本子项目把“输入单词、查询讲解视频元数据”抽成独立 HTTP 服务，前端和其他程序都可以调用。

服务内部仍使用父项目已有的 ACS / ABCLite 百度网页签名脚本，为发往百度的请求生成签名。调用者只需提供单词，服务内部的签名流程不需要调用者配置密钥。服务返回百度 CDN 地址和元数据，视频播放、下载、转存及任务上传由调用方处理。

候选平台曾包括 Fastly Compute 和 Netlify Edge Functions。最终选择 Netlify，是因为已在其真实边缘环境完成动态代码执行、`node:vm`、签名生成和百度查询验证；Fastly 尚未完成这项业务的实际部署。初期直接打包完整 jsdom / Node 依赖，通过浏览器管理 API 上传时遇到 413；保留原签名脚本、换用轻量浏览器环境后上传及查询成功。研究过程和失败尝试见 [PROVENANCE](docs/PROVENANCE.md)。

## 快速调用

无需登录、密钥或后端代理，直接 GET：

```powershell
curl.exe "https://baidu-word-video-service-c7e3.netlify.app/v1/video?word=evidence"
```

可将 `word` 换成自己的英文单词或短语；程序应使用 `URLSearchParams`、`params` 等方式编码参数，避免手工拼接。完整前端示例见下文“接口”。有视频时读取 `result.video.videoUrl`，正常无视频时读取到 `hasVideo:false`，请求失败则按 HTTP 状态和 `ok` 判断。

## 接手阅读路线

| 文档 | 内容 |
| --- | --- |
| 本 README | 当前调用方式、输出约定、关键决策、本地运行与部署配置 |
| [DEPLOYMENT](docs/DEPLOYMENT.md) | 当前站点、Deploy ID、发布时间、公开访问状态和父项目接入 |
| [PROVENANCE](docs/PROVENANCE.md) | 原项目背景、平台选择、POC 尝试、签名脚本来源和演进过程 |
| [VALIDATION](docs/VALIDATION.md) | 已完成的验证、原始 JSON 证据及尚未验证的范围 |
| [OPERATIONS](docs/OPERATIONS.md) | 上线验收、日志定位、脚本维护、回滚和持续观察事项 |

当前版本为 **1.1.0 公开接口**。`docs/history/` 及旧版本 JSON 保留了 1.0.0 的鉴权历史，接手时以当前 README 和 DEPLOYMENT 为准。

## 在 Netlify 部署

现有正式站点已经部署，目前采用手工产物上传，尚未关联 Git 持续部署。**本地保存或修改源码不会自动更新线上服务。** 更新现有站点时先核对 DEPLOYMENT 中的项目身份，重新构建、验收和发布；下表用于新建部署或配置后续 Git 构建。

推荐在 Netlify 新建正式 Project，通过 Git 连接部署。将完整子项目源码及 `pnpm-lock.yaml` 放到部署仓库；Git 提交和推送按仓库现行授权流程单独进行。

| 设置 | 保留当前父仓库时 | 此目录成为独立仓库时 |
| --- | --- | --- |
| Base directory | `baidu-video-service` | 留空，使用仓库根目录 |
| Package directory | 留空，与 Base 相同 | 留空 |
| Build command | `pnpm run build` | `pnpm run build` |
| Publish directory | `public`，相对 Base | `public` |
| 配置文件 | 子项目内 `netlify.toml` | 根目录 `netlify.toml` |

构建命令执行测试和完整性检查，Netlify 随后从 `netlify/edge-functions/` 自动打包边缘函数。源代码和百度签名脚本位于发布目录之外。**仅把 `public/` 拖入网页上传区不会部署这项 API。** 官方支持 Git 持续部署或 Netlify CLI 手工部署边缘函数，见 [部署文档](https://docs.netlify.com/build/edge-functions/get-started/#deploy)；Base、Package、Publish 的含义见 [Monorepo 文档](https://docs.netlify.com/build/configure-builds/monorepos/)。

服务使用默认配置即可运行，无需设置密钥。调整可选参数时，在 Netlify UI 中设置包含 Functions scope 的环境变量，随后重新部署；`netlify.toml` 中的变量无法供 Edge Functions 运行时读取。[官方环境变量说明](https://docs.netlify.com/build/edge-functions/environment-variables/)

正式项目的 Netlify 网页登录保护应关闭，使匿名请求能到达函数。当前项目已关闭这一门槛，查询默认允许所有浏览器来源跨域访问。

部署完成后按 [运行与上线步骤](docs/OPERATIONS.md) 做真实请求验收，再让词汇系统切换服务地址。

本项目另提供 `scripts/prepare-browser-deploy.mjs <site-uuid>`，从 `check:edge` 的产物准备 `.cache/browser-deploy/deploy.browser.js` 和上传清单。该 Node 命令只生成本地文件；浏览器端脚本需在已登录的 Netlify 管理页执行，才会创建并上传草稿，生产发布另行进行。当前部署使用了这一方式。脚本调用 Netlify 管理 API，不属于业务接口；常规 Git 部署无需使用它。

## 接口

```http
GET /healthz
GET /v1/video?word=evidence
```

所有接口均无需鉴权。`/healthz` 检查可选配置是否合法，不调用百度或初始化签名器；使用默认配置返回 200，可选参数无效时返回 503。

查询成功的结构示例；为便于阅读，可选图片地址以 `null` 示意，实际值以查询结果为准：

```json
{
  "ok": true,
  "word": "evidence",
  "hasVideo": true,
  "video": {
    "videoUrl": "https://fanyiapp.cdn.bcebos.com/app/video/query/E204-evidence.mp4",
    "coverUrl": null,
    "thumbUrl": null,
    "feedUrl": null,
    "videoTime": 77,
    "videoType": null,
    "tag": "讲解"
  },
  "cache": "miss",
  "failure": null,
  "errno": null,
  "errmsg": null,
  "requestId": "..."
}
```

无视频时仍返回 HTTP 200，`ok:true, hasVideo:false, video:null`。上游失败返回非 2xx，`ok:false` 和明确的 `failure`，调用方应据此触发重试或原有回退。

查询响应的顶层是**单个对象**。`video` 有以下固定字段：

| 字段 | 含义 / 类型 |
| --- | --- |
| `videoUrl` | HTTPS 视频地址；`hasVideo:true` 时存在 |
| `coverUrl`、`thumbUrl`、`feedUrl` | 百度返回的图片地址，保留原字段名；HTTPS 字符串或 `null` |
| `videoTime` | 时长，单位秒；数值或 `null` |
| `videoType` | 百度返回的视频类型；字符串、数值或 `null` |
| `tag` | 标签，例如“讲解”；字符串或 `null` |

`cache` 只出现在成功查询中；`requestId` 用于关联日志，通常也位于 `X-Request-Id` 响应头。成功时 `failure`、`errno`、`errmsg` 均为 `null`。失败时 `ok:false`、`video:null`，`failure` 表示错误类别；`errno` 在百度返回业务错误码时保留该数值。百度 HTTP 错误还可能附带 `upstreamStatus`。

`word` 必须只传一次；去除首尾空格、转小写、合并空白后长 1–80 字符，以英文字母开头，可含数字和词间空格、连字符、英文单引号。例：`evidence`、`ice cream`、`can't`、`covid-19`。其他参数被拒绝。

| HTTP | 含义 / 常见 failure |
| --- | --- |
| 200 | 查询成功，包括正常无视频 |
| 400 | `INVALID_WORD`、`INVALID_PREFLIGHT` |
| 403 | `ORIGIN_NOT_ALLOWED` |
| 404 / 405 | 未知 `/v1/` 接口 / 不支持的方法 |
| 499 | 调用方已取消请求 |
| 502 | 百度网络、HTTP、业务、格式或签名失败 |
| 503 | `CONFIGURATION_ERROR`、实例繁忙 `BUSY` |
| 504 | 总时限 `TIMEOUT`、`UPSTREAM_TIMEOUT`、`SIGNER_TIMEOUT` |

业务错误码 995、10001、1709 分别映射为 `ACS_REJECTED`、`VALIDATION_FAILED`、`QUOTA_EXCEEDED`，响应中的 `errno` 保留数值。错误文案经过清理，不回传原始上游正文、令牌、堆栈。仅签名被拒绝时最多重试一次；配额耗尽不会通过切换匿名 ID 重试。

所有 API 响应设置 `no-store`。`cache` 为 `miss`、`hit` 或同词并发合并的 `coalesced`。响应头 `X-Request-Id` 可用于查日志。默认 CORS 响应为 `Access-Control-Allow-Origin: *`，支持跨站点匿名 GET 和 OPTIONS 预检。

```javascript
async function queryWordVideo(word) {
  const url = new URL("https://baidu-word-video-service-c7e3.netlify.app/v1/video");
  url.searchParams.set("word", word);
  const response = await fetch(url, { credentials: "omit" });
  const result = await response.json();
  if (!response.ok || result.ok !== true) {
    throw new Error(result.errmsg || `HTTP ${response.status}`);
  }
  return result;
}

queryWordVideo("evidence")
  .then(result => console.log(result.hasVideo ? result.video.videoUrl : null))
  .catch(console.error);
```

默认开放跨域 GET 和 OPTIONS 预检，前端使用 `credentials: "omit"` 即可。若运维人员显式设置了 `BAIDU_VIDEO_ALLOWED_ORIGINS`，浏览器来源会按该配置检查；当前部署使用默认 `*`。

## 输出兼容性与关键决策

百度原始 SSE 响应的 `queryExplain` 是整份讲解信息对象，视频链接是其中的 `queryExplain.videoUrl`。原 CLI 会从它整理出 `video`，然后同时返回 `queryExplain` 和 `video`，所以视频地址、封面和时长等核心信息在两处重复出现。当前接口保留整理后的 `video`，有视频时对应关系为：

```text
百度 queryExplain.videoUrl → 当前接口 video.videoUrl
```

| 变化 / 决策 | 原因与兼容性影响 |
| --- | --- |
| 旧 CLI 的 `[result]` 改为 HTTP 的 `result` | CLI 支持一次查询多个词，当前 HTTP 每次只接收一个 `word`，因此直接返回对象；直接消费旧 CLI JSON 的程序需调整外层解析 |
| 保留 `video`，不透传 `queryExplain` | 原父项目只使用 `video.videoUrl`，核心字段又有重复；固定视频字段便于调用，百度原对象中未映射的扩展字段不会自动透传 |
| 新增 `cache` | 区分本次查询、实例缓存命中和同词并发合并，便于观察结果来源 |
| 新增 `requestId` | 将一次响应与 Netlify 日志对应，方便排查失败或延迟 |
| 耗时写入日志 | 原 CLI 本身没有 `elapsedMs`；该字段由 POC 的 `query-local.mjs` 包装脚本额外加入，当前接口不返回这一测试字段 |
| 完整 SSE 和字段校验 | 仅在识别到正常结束事件后接受无视频结果；坏 JSON、截断流、异常字段等明确报错，避免误入无视频缓存；URL 和字段类型检查比旧实现更严格 |
| 签名脚本实例复用、串行签名和实例缓存 | 减少重复初始化，避免同时调用同一签名实例；缓存与并发限制仅在单个运行实例内生效 |
| 仅对签名拒绝做一次重试 | 对签名重新生成后再试一次；网络、格式和配额失败直接报告，避免无界重复调用 |
| 移除本地视频 Playwright 回退 | 父项目已按用户要求直接调用独立服务；失败抛出异常，由原调度层记录并重试 |

外层对象、移除 `queryExplain` 和新增响应元数据属于本服务的接口设计取舍，迁移到 Netlify 本身不要求改变这些字段。当前接口保留了业务所需的视频字段，原 CLI 的完整 JSON 与当前 HTTP JSON 并非逐字段兼容。

父项目的解析代码已经同步调整：

```text
旧：Node stdout → JSON 数组 → results[0] → video.videoUrl → [URL] 或 []
新：HTTP 响应 → JSON 对象 → video.videoUrl → [URL] 或 []
```

新的 `utils/baidu_video_client.py` 检查 HTTP 状态、JSON 类型、返回单词和视频地址，再保留原 `query_word_explanation_video()` 的 `list[str]` 成功返回接口。`agent/dida365.py` 切换了导入位置，后续视频下载和附件上传逻辑未改；失败路径不再启动本地视频浏览器。实测 evidence 的视频地址及固定元数据与原结果一致，详细对照和联调证据见 [VALIDATION](docs/VALIDATION.md)。

父项目只需在自己的 `config.yaml` 中配置 `baidu_video_service_url`，值为本服务完整地址，或使用环境变量 `BAIDU_VIDEO_SERVICE_URL` 覆盖。调用方不需要配置密钥。Python 联调及可选完整下载命令见 [OPERATIONS](docs/OPERATIONS.md) 的“父项目当前接入”。

## 本地使用

需要 Node 22.18.0、pnpm 10.30.3。Python、Playwright 和浏览器均无需安装。Windows PowerShell 中，从本目录运行：

```powershell
. .\scripts\local-env.ps1
pnpm install --frozen-lockfile
pnpm run build
```

`local-env.ps1` 只修改当前 PowerShell 进程的环境变量，将工具缓存和临时文件放在子项目 `.cache/`，不改系统设置。`.npmrc` 同时限定 pnpm 和底层元数据请求的缓存目录。

启动本地 HTTP API：

```powershell
pnpm dev
```

默认只监听 `127.0.0.1:8899`。在另一个位于本目录的终端中，可运行 `pnpm smoke`，它读取存在的本地 `.env` 并实际查询百度。`.env` 已忽略，勿提交。

一键本地验证：

```powershell
pnpm run smoke:local
```

该命令临时启动本地服务，完成匿名健康检查、参数、真实查询、并发、公开跨域和缓存验证，然后停止服务。`pnpm test` 只使用离线样例和模拟依赖，不访问百度；`pnpm run smoke:runtime` 则在当前 Node 进程中运行真实签名和查询。只有调整可选参数时才需要创建 `.env`。

用 Netlify 官方打包器检查可部署产物：

```powershell
pnpm run check:edge
```

此项开发依赖固定为已在 POC 验证过的 `@netlify/edge-bundler@16.1.3`。它会使用已有 Deno 或在项目缓存目录下载 Deno，输出到 `.netlify/edge-functions/`。正常 Netlify Git 构建由平台执行边缘打包，不需要把这些本地产物提交，也不必把 `check:edge` 加进平台 Build command。

从本目录验收现有线上服务：

```powershell
pnpm smoke https://baidu-word-video-service-c7e3.netlify.app
```

该命令只查询接口和检查响应，不发布代码。父项目的 Python 联调脚本及完整下载验证方法见 [OPERATIONS](docs/OPERATIONS.md)，当前部署身份和已完成结果见 [DEPLOYMENT](docs/DEPLOYMENT.md)。

## 可选环境变量

以下是 Functions 运行时变量；省略即用默认值。完整示例见 [.env.example](.env.example)。

| 名称 | 默认 | 有效范围 / 说明 |
| --- | --- | --- |
| `BAIDU_VIDEO_TIMEOUT_MS` | `20000` | 1000–30000；单次完整查询时限，含导入、百度签名脚本等待及重试 |
| `BAIDU_VIDEO_UPSTREAM_TIMEOUT_MS` | `10000` | 500–15000；每次百度 HTTP 请求，含读取流 |
| `BAIDU_VIDEO_CACHE_TTL_SECONDS` | `3600` | 0–86400；有视频的缓存时间 |
| `BAIDU_VIDEO_NEGATIVE_CACHE_TTL_SECONDS` | `300` | 0–3600；正常无视频缓存时间 |
| `BAIDU_VIDEO_CACHE_MAX_ENTRIES` | `256` | 0–2048；实例内 LRU 容量，0 关闭缓存 |
| `BAIDU_VIDEO_MAX_INFLIGHT` | `4` | 1–16；每实例同时处理的不同单词数 |
| `BAIDU_VIDEO_ALLOWED_ORIGINS` | `*` | 默认允许所有浏览器来源；也可显式配置逗号分隔的完整 origin 列表 |

## 文件地图与实现边界

| 路径 | 用途 |
| --- | --- |
| `netlify/edge-functions/api.js` | Netlify 标准入口和路由 |
| `src/api.mjs`、`src/config.mjs` | HTTP、配置、公开 CORS、参数和错误处理 |
| `src/service.mjs` | 总超时、缓存、同词合并、实例负载限制 |
| `src/baidu/signer.mjs`、`runtime.mjs` | 百度签名脚本懒加载、串行签名、实例复用及失败重建 |
| `src/baidu/vendor-sdk.mjs` | 冻结的原始 ACS / ABCLite 脚本字符串 |
| `src/baidu/browser-window.mjs`、`sandbox.mjs` | 轻量浏览器环境；百度签名脚本网络访问关闭，定时器可清理 |
| `src/baidu/client.mjs` | 固定百度端点、有限 SSE 读取、完整性判断和元数据归一化 |
| `test/` | 离线回归测试及最小 SSE 样例 |
| `scripts/` | 本地服务、验证和打包工具 |
| `docs/PROVENANCE.md`、`docs/sdk-integrity.json` | 来源与百度签名脚本校验 |

缓存、并发限制和百度签名脚本复用均限于单个运行实例，实例回收后丢失，跨地区不共享。这里没有全局限流、数据库或视频代理。调用方中断时，与其他请求共享的查询会继续到完成或总时限。

百度端点来自现有网页流程，协议或百度签名脚本更新可能导致失败。现有验证证明短期功能可用，长期稳定性及真实 Netlify CPU 用量还需上线验收和观察。[运行说明](docs/OPERATIONS.md)列出了识别方式和后续接入边界。

## 源码提交范围

本目录 `.gitignore` 排除 `node_modules/`、`.cache/`、`.netlify/`、构建/覆盖率目录、本地 `.env` 和临时文件，只保留 `.env.example` 配置模板。源码、脚本、pnpm 锁文件、Netlify 配置、文档和 `docs/validation/` 中的精简冻结证据均应随项目提交。

最终整理已移除父项目中的旧 POC、原始调研和临时审阅目录，迁移背景与精简验证记录保留在本项目 `docs/`。本机依赖、虚拟环境和工具缓存均不属于交付源码，按本文步骤准备即可。旧 CLI 保留在父仓库 Git 历史中，当前构建只依赖本项目源码及声明的开发依赖。

`.gitattributes` 将冻结的 `src/baidu/vendor-sdk.mjs` 固定为 LF 换行，确保 Windows 检出后仍与 `docs/sdk-integrity.json` 的文件哈希一致；SSE 测试样例保留协议必需的末尾空行。
