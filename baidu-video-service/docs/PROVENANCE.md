# 来源、迁移决策与接手说明

## 背景

父项目是欧路生词到滴答单词任务的本地自动化系统。单词讲解视频原本由 `vendor/baidu-video.mjs` 查询，Python 在本地 Node 失败时回退 Playwright，再自行下载 CDN 视频并上传附件。

用户希望把查询部分抽成独立边缘服务，候选为 Fastly Compute 和 Netlify Edge Functions。2026-10-28 已通过用户自己的 Chrome MCP、现有 Netlify 登录态创建独立 POC，证明 Netlify 可以执行原 ACS / ABCLite 百度签名脚本的签名和百度真实查询。Fastly 尚未做该业务的实际部署。

2026-10-29 用户要求在父项目根目录建立可正式部署的子项目，由此建立本目录。随后用户明确要求替换父项目的本地查询并部署服务；当前已完成正式发布和 Python 客户端联调。最新部署状态见 [DEPLOYMENT](DEPLOYMENT.md)。

最终整理时，父项目中的旧 POC、原始调研和本地审阅目录已移除。本文件及同目录的验证、运维文档保留必要背景和精简证据，可供独立取出本子项目后的人员接手。历史 CLI 原件仍位于父仓库提交 `277444abbfd5266e9d4c32c31bdb65368ace2dc9` 的 `vendor/baidu-video.mjs`，当前构建无需恢复它。

## 历史 POC 证据

以下均为 2026-10-28 的历史记录，不能视为此次 1.0.0 的云端验收：

| 项目 | 历史值 |
| --- | --- |
| Project | `baidu-video-edge-poc-20261028-c7e3` |
| Site ID | `f37e74cf-9374-4e01-ba97-d191617bba4a` |
| 成功 Deploy ID | `6ae20a9cab89ff610738f3bc` |
| 成功预览 | [不可变部署地址](https://6ae20a9cab89ff610738f3bc--baidu-video-edge-poc-20261028-c7e3.netlify.app) |
| 业务结果 | evidence 有视频 77 秒；feature 有视频 62 秒；apricot、zzzxqv 正常无视频 |
| 运行时探测 | eval、Function、node:vm 均成功；五个业务请求 HTTP 200 |
| 访问保护 | Netlify 网页登录保护；匿名请求曾收到平台 401 |
| 产物 | 453,143 字节，轻量浏览器环境，移除完整 jsdom |

历史尝试：完整 Node bundle 的 ESZIP 为 13,438,393 字节，上传收到 413；压缩版本 6,730,192 字节仍收到 413。改成保留百度签名脚本和业务、替换 jsdom 后上传成功。压缩 MCP 传输只改善工具传递，不改变实际 HTTP 上传体积。

POC 后续只读记录发现站点 URL 曾指向另一个来源未确认的预览部署，因此没有继续覆盖或清理远端。正式服务应以自己的目标部署 ID 验收；本次没有改动 POC 项目。

## 1.0.0 如何形成

1. 校验父项目原 bundle 的 SHA-256 为 `e289cc21114f4e32f081b48c03f74a1aa36191de73520a129c3e723c9c638b2b`，大小 13,431,343 字节。
2. 从两个 `globalThis.__ACS_BUNDLED_*__` 赋值提取原始百度签名脚本字符串，生成本地 `src/baidu/vendor-sdk.mjs`。没有改百度签名脚本字符串内容，也没有在构建时重新下载百度签名脚本。
3. 保留原百度签名脚本沙箱的 document/canvas/storage/navigator 等环境实现，沿用 POC 的 MinimalJSDOM。增加定时器清理并关闭百度签名脚本 console，环境层位于 `src/baidu/`。
4. 新建 runtime 和串行签名器，避免首次并发重复创建实例；初始化、签名失败后清理并重建，百度签名脚本网络访问继续关闭。
5. 重新整理客户端和 HTTP 接口，保留原百度端点、请求字段及视频字段；增加流大小限制、完整性检查、鉴权、整体时限、有限重试和实例内缓存。

百度签名脚本字符串及导出模块摘要见 [sdk-integrity.json](sdk-integrity.json)，`pnpm build` 自动校验。内置脚本沿用父项目现有版本，本目录未为第三方脚本新增许可声明。

2026-10-29 的脱敏现场结构检查确认：`evidence`、`apricot` 和 `zzzxqv` 均有 `StartTranslation`、`TranslationSucceed`、`Finished`；仅 evidence 有 `GetQueryExplainSucceed`。因此新解析器把完整结束且无视频事件认定为无视频，对截断、坏 JSON、未知格式返回错误。测试 SSE 是根据这些事件构造的最小样例，没有包含原始翻译正文或令牌。

与 POC 的接口区别：正式路径为 `/v1/video?word=...`；没有默认查询单词、签名探针、令牌长度接口或实现计时回显。生产日志仅保留状态、请求 ID、缓存状态和总体时长。原始百度详情不直接透传，视频元数据字段保持固定。初始 1.0.0 曾由实现者自行加入 Bearer 鉴权；用户明确要求公开、无需鉴权后，1.1.0 已移除此限制并开放默认跨域访问。

## 操作边界与接手顺序

所有新工具依赖、缓存、临时产物均限定在当前子项目。开发时从 POC 的项目内缓存复制了已校验的 Deno 2.4.2 可执行文件到本项目 `.cache/deno-cli/`，没有全局安装 Deno。它是本地检查工具，部署源码不依赖该缓存；云端 Deno 运行时由 Netlify 提供。

旧 POC 曾受父项目 `.gitignore` 中 `lib/` 规则影响。正式项目使用 `src/`，必要源码无需依赖 ignore 例外；缓存、产物、依赖和 `.env` 则由本目录忽略。冻结脚本模块的字节哈希依赖 LF 换行，项目 `.gitattributes` 已固定该文件的检出格式。

先读 [README](../README.md) 了解接口与部署，再读 [VALIDATION](VALIDATION.md) 区分已验证和待验证范围，最后按 [OPERATIONS](OPERATIONS.md) 维护服务。可收集的源码与验证材料不包含 Netlify 账户凭据、父项目数据库或用户任务数据。1.0.0 新生成的服务密钥已从 Netlify 环境变量、父项目配置和本机缓存中移除；原部署说明见 [历史快照](history/DEPLOYMENT-1.0.0.md)。该历史快照中的 POC 路径记录当时的归档位置，最终源码范围以当前 README 为准。
