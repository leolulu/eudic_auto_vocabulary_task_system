# eudic_auto_vocabulary_task_system

依托欧路词典的生词本，自动为滴答清单（TickTick / Dida365）生成「单词日报」任务的小工具：把你在欧路词典里新查的生词，自动加工成带 AI 词源讲解、音标、发音音频与讲解视频的单词卡，并按遗忘曲线维护复习节奏。

## 功能特性

- **自动取词**：定时从欧路词典生词本拉取最近新增的生词，并带本地去重，避免重复处理。
- **AI 词源 / 释义讲解**：调用（自建的）豆包讲解服务，为每个词生成词源、释义等讲解正文。
- **多源音标**：自动获取美式音标（汇总有道 / 必应 / 百度 / 金山词霸等词典来源）。
- **发音音频 + 讲解视频**：自动下载并作为附件挂到单词任务上。
- **滴答清单建卡**：在「背单词」项目下，克隆「模板任务一」生成单词任务，并把音频置于正文顶部、即点即听。
- **遗忘曲线续期**：每天自动把到期的遗忘曲线任务顺延到当天，维持复习节奏。
- **任务内问答**：在任务正文里用 `::你的问题::` 标记提问，系统会自动用豆包作答并把答案回写进正文。
- **手动发布生词**：用 `--add-word` 把单词及可选笔记发布到欧路，再由常驻流程同步到滴答清单。
- **欧路笔记图片**：读取欧路 App 笔记中的图片并上传为滴答任务附件；一次性命令也可通过本机欧路桌面 App 登录态发布图片笔记。
- 代码中也保留了把生词推送到 **Anki** 的方法（与滴答流并行的另一套实现）。

## 运行环境

- 本机通过 [uv](https://docs.astral.sh/uv/) 管理 Python 与依赖（无需全局 Python）。
- 依赖见 `requirements.txt`（requests、schedule、arrow、PyYAML、playwright 等）。
- 百度音标与讲解视频的抓取依赖 Playwright（Chromium），首次使用需安装浏览器内核：`uv run playwright install chromium`。

## 配置

首次运行若没有 `config.yaml`，程序会在项目根目录自动生成一份模板并提示补全。配置项如下：

| 配置键 | 说明 |
| --- | --- |
| `eudic_api_key` | 欧路词典开放 API 密钥（用于读写生词本和笔记） |
| `eudic_sync_token` / `eudic_sync_user_id` | 可选；仅在欧路桌面 App 配置不可用时，供 `--note-image` 回退使用 |
| `doubao_webserver_endpoint` | 自建豆包讲解服务地址 |
| `dida365_username` / `dida365_password` | 滴答清单账号密码 |
| `anki_push_endpoint` | Anki 推送后端地址（用到 Anki 流时才需要） |

新生成的模板会包含两个空白的欧路私有同步配置项；它们不参与常规启动的必填校验。已有用户的旧配置不会被自动补写，也不会因为缺少这两项而停止运行。

滴答清单登录成功后返回的 `t` Cookie 会保存在项目根目录的 `dida365.session` 中。该文件和登录冷却状态文件均已被 Git 忽略；不要手工提交、打印或分享其中内容。欧路私有同步凭据同样应按密码级凭据保管。

## 使用

### 自动模式（定时调度）

```bash
python main.py
```

无参数运行即进入定时调度：

- **每分钟**：抓取欧路新词 → 生成滴答单词卡。
- **每 10 秒**：扫描任务正文中的 `::提问::` → 豆包作答并回写。
- **每天 00:01**：按遗忘曲线续期到期任务。

> 当前主循环约运行 1 小时后退出，通常配合系统计划任务 / 常驻守护方式重复拉起。

### 手动发布单个单词

```bash
uv run python main.py --add-word "<单词>"
uv run python main.py --add-word "<单词>" --note "<简短笔记>"
uv run python main.py --add-word "<单词>" --note-file "<UTF-8 Markdown 文件>"
uv run python main.py --add-word "<单词>" --note "<笔记>" --note-image "<图片路径>"
uv run python main.py --add-word "<单词>" --note-file "<UTF-8 Markdown 文件>" --note-image "<图片一>" --note-image "<图片二>"
```

一次性命令只负责把完整记录发布到欧路，确认成功后退出，不进入定时循环。常驻服务会在后续调度中读取该记录并创建滴答任务。

- **词组**：用英文双引号括起来，例如 `uv run python main.py --add-word "be in a fix"`。
- **短笔记**：用 `--note` 直接传入字符串。
- **多行笔记**：用 `--note-file` 读取 UTF-8 文件；内部 Markdown 和换行会被保留。
- **图片笔记**：用 `--note-image` 指定图片；该参数可重复使用，也允许创建只有图片、没有文字的笔记。
- `--note` 与 `--note-file` 互斥，并且只能与 `--add-word` 同时使用。
- `--note-image` 使用从欧路 Windows 桌面 App 真实抓包复刻的私有同步协议。程序优先从 `%APPDATA%\Francochinois\eudic\config.ini` 读取同一账户的 `SyncToken` 与 `SyncUserId`；该文件不可用或缺少任一项时，再从项目 `config.yaml` 读取 `eudic_sync_token` 与 `eudic_sync_user_id`。两项必须在同一个来源中同时存在，不会跨来源拼接。
- 若两处均无完整凭据，命令会在发送任何写请求前明确报错并列出两个配置位置。请求一旦发出，不会因响应失败而换用另一套凭据重试，避免无法判断第一次写入是否已经生效。
- 私有同步鉴权中的时间戳和请求签名由程序动态生成，持久配置只需要上述 token 与 user ID。该能力不属于官方 OpenAPI；协议变化时应优先重新抓包验证 `agent/eudic_app_sync.py`，不要调整常驻服务的 OpenAPI 密钥。
- 有笔记时严格执行“保存 note → 读取并校验 note → 添加单词”；单词进入生词本代表整组数据已经就绪。
- 图片使用内容哈希生成稳定文件名；重复执行同一命令时，相同文字和图片会被复用，不同内容会报告冲突，不会静默覆盖或重复上传。
- 写请求响应不确定时会先读取欧路最终状态，确认失败后才提示使用相同命令重试。
- 该功能只负责未来的新数据，不扫描或修复历史上仅存在于滴答清单的旧任务。

需要用全新测试词验收完整图片链路时，先省略 `--execute` 检查目标，再执行真实写入：

```bash
uv run test/manual_eudic_note_image_to_dida.py --word "<全新测试词>" --note "<测试语境>" --image "<图片路径>"
uv run test/manual_eudic_note_image_to_dida.py --word "<全新测试词>" --note "<测试语境>" --image "<图片路径>" --execute
```

脚本依次验证欧路图片 Note、认证下载、滴答附件、正文图片引用、占位符清理、`&nbsp;` 清理，全部读回通过后才写入本地历史。若滴答登录处于冷却，欧路记录会保留就绪状态并以退出码 75 停止；冷却结束后用完全相同的命令重试即可。

### 安全导入滴答清单会话

当用户名密码登录受到风控、需要复用浏览器里已有的有效会话时，可以运行：

```bash
python main.py --set-dida-t
```

程序会通过隐藏输入读取 `t`，先调用只读附件配额接口验证，再写入 `dida365.session`。输入值不会显示在终端，也不会进入 shell 历史。此命令不会初始化其他服务、创建任务或修改任务。

新环境中如果还没有 `dida365.session`，直接运行自动模式会按设计尝试一次用户名密码 signon。`--add-word` 只初始化欧路客户端，不依赖滴答登录。账号正处于 429 风控时，应先执行 `--set-dida-t` 导入浏览器现有会话；导入成功会自动清除 `dida365.auth-state.json` 中的登录冷却，无需手工删除状态文件。

获取 `t` 的方法：

1. 在 Chrome 中打开已经登录的滴答清单网页。
2. 打开开发者工具，进入 `Application` → `Cookies`。
3. 找到名称为 `t` 的 Cookie，复制其 Value。
4. 运行上面的命令，在隐藏输入提示后粘贴并回车。

安全注意事项：

- `t` 等同于当前滴答清单登录会话，应按密码级别保护。
- 禁止把 `t` 放在命令行参数、聊天消息、Issue、日志或 Git 提交中。
- 验证返回 401 时，程序拒绝保存该值。
- 验证遇到 429、5xx 或网络错误时，程序不覆盖已有会话文件。

## 滴答清单私有 API 与 2026 年 7 月 429 事故调查记录

本节记录 2026 年 7 月对滴答清单接口、登录会话和 systemd 重启行为的完整调查。它既是事故复盘，也是以后决定是否迁移 V3、OpenAPI 或其他认证方式时的事实依据。

### 1. 项目为什么长期使用私有 API

本项目需要的能力超出常规任务 CRUD：

- 使用 `ERULE:NAME=FORGETTINGCURVE;CYCLE=0;COUNT=6` 创建复杂遗忘曲线任务。
- 上传发音音频、讲解视频和图片，并把附件放入任务正文的指定位置。
- 读取附件元数据、下载附件、把附件标记为失活。
- 维护父子任务、模板任务和较完整的任务底层字段。

截至 2026 年 7 月的调查结果：

- 官方 OpenAPI 已支持 `repeatFlag`，真实测试可以创建并读回上述 `ERULE`。
- 官方 OpenAPI 文档仍未提供附件上传、下载或附件失活接口。
- 网页生成的 API 口令可以访问 OpenAPI 和官方 MCP，但不能直接访问依赖 `t` Cookie 的私有 V1/V2/V3 接口。
- 官方 MCP 主要覆盖任务、清单、习惯、专注和纪念日等基础操作，未发现附件工具。
- 如果迁移到官方 Token，仍需为附件保留私有 Cookie 通道，会形成两套客户端和两套凭证，改动范围与回归风险明显增加。

相关官方资料：

- [滴答清单 OpenAPI](https://developer.dida365.com/docs#/openapi)
- [滴答清单 MCP](https://help.dida365.com/articles/7438132116019216384)

因此本轮继续采用已经完整走通的私有 HTTP API。滴答清单认证和 API 调用链路不依赖网页自动化、Chrome MCP、Playwright 登录或邮件通道；浏览器只在人工调查和一次性取得现有 `t` 时使用。项目中为百度音标和讲解视频保留的 Playwright 抓取属于另一条数据来源链路，不参与滴答清单认证。

### 2. 429 事故现象

旧版本执行：

```bash
python main.py --add-word carousel
```

程序在初始化 `Dida365` 时调用：

```text
POST /api/v2/user/signon?wc=true&remember=true
```

signon 返回 429，程序尚未进入任务同步、查重、建卡或附件阶段就已经退出。

服务器日志同时显示：

- systemd 的重启计数不断增加。
- 失败进程退出后会被立即重新拉起。
- 每个新进程再次执行 signon。
- 最后数次重启在几秒内连续收到 429。

### 3. 重复登录会话与 systemd 的对应关系

设备管理中发现了 2026 年 7 月 17 日至 21 日的大量重复 Web 登录记录。调查把登录记录与 systemd 日志逐条对齐后得到：

- 第一条重复会话：7 月 17 日 12:46，对应 systemd restart counter 68。
- 最后一条重复会话：7 月 21 日 19:15，对应 counter 182。
- `182 - 68 + 1 = 115`，与清理掉的 115 条重复会话完全对应。
- counter 183 在 7 月 21 日 20:16 启动后首次收到 signon 429。
- counter 184～188 随后在约 3 秒内连续失败。

主程序末尾存在明确的一小时生命周期：

```python
for _ in range(3600):
    schedule.run_pending()
    time.sleep(1)
```

进程运行约一小时后正常退出，systemd 又把它拉起。旧代码在每次 `Dida365` 初始化时无条件 signon，因此每次正常重启都会产生一条新登录会话。

重启日志的统计特征也支持这一结论：

- restart counter 54～188 共 135 次重启事件。
- 108 个间隔集中在 50～70 分钟，符合一小时主动退出。
- 另有少量 10 秒至 10 分钟的异常重启。
- 最后的 5 个间隔小于 10 秒，对应 signon 429 后的快速失败循环。

### 4. 调查中发现的临时服务错误

日志里确实出现过私有接口和其他服务的临时异常：

- 7 月 17 日：`/api/v2/batch/check/0` 返回 504。
- 7 月 18 日：欧路词典接口返回 503。
- 7 月 21 日：`/api/v2/batch/check/0` 返回 502。

这些错误会让当次进程异常退出，进而产生额外重启。它们在日志中是离散事件，没有形成“从某一时间起 V2 持续不可用”的证据。最终稳定复测时，V2 同步接口仍然返回 200。

### 5. 最终根因

429 的因果链为：

```text
一小时主动退出 / 少量临时异常
        ↓
systemd 重新启动进程
        ↓
每次初始化都无条件 signon
        ↓
积累大量重复登录会话
        ↓
signon 触发风控并返回 429
        ↓
失败进程再次被 systemd 拉起
        ↓
几秒内继续 signon，形成重试风暴
```

当时版本中，`--add-word carousel` 的失败点位于 signon；当时还没有执行 V2 全量同步。因此该次 429 不能归因于任务同步版本。当前 `--add-word` 已调整为只发布到欧路，不再初始化滴答客户端。

### 6. `t` Cookie 的含义

`t` 是滴答清单私有 API 使用的登录会话 Cookie。浏览器登录、用户名密码 signon 或其他受支持的登录流程成功后，服务端会返回它。

关键性质：

- `t` 可以跨进程复用，不需要每次启动重新登录。
- 私有 V1/V2/V3 接口依赖 `t`，官方 API 口令无法替代它。
- 只有明确的 401 才能判定当前 `t` 已失效。
- 429、5xx、超时和网络错误都不足以证明 `t` 失效。
- `t` 必须保存在 Git 忽略、权限受控的本地文件中。

本项目采用以下认证决策：

1. 启动时优先加载 `dida365.session`。
2. 使用只读的 `/api/v1/attachment/isUnderQuota` 验证会话。
3. 验证成功后直接复用。
4. 没有 `t` 或验证明确返回 401 时，只执行一次 signon。
5. signon 成功后立即原子保存新 `t`。
6. 429、5xx 和网络错误保留原 `t`，禁止转入登录。

### 7. 登录冷却与防重启风暴

单纯限制“一次进程只登录一次”仍不足以抵御 systemd 快速重启。因此项目使用 `dida365.auth-state.json` 持久化登录失败状态，内容只包含失败次数、下次允许登录时间和脱敏状态码，不包含用户名、密码或 Cookie。

退避规则：

- 优先遵守服务端 `Retry-After`。
- signon 429 首次冷却 1 小时。
- 连续 429 按 1、2、4、8 小时递增，最长 24 小时。
- signon 的 5xx 或网络错误从 5 分钟开始退避，最长 1 小时。
- CAPTCHA、凭证错误或 `access_forbidden` 按较长登录冷却处理。
- 冷却期内的新进程不会向 signon 发出请求。
- `--set-dida-t` 成功后立即清除冷却状态。

该设计首先保护账号和登录接口。即使 systemd 仍然重启进程，冷却文件也能阻断跨进程的 signon 风暴。

### 8. 网页端当前使用的接口版本

2026 年 7 月 22 日通过已登录网页的真实网络请求确认，滴答网页采用混合版本：

| 功能 | 网页端接口 |
| --- | --- |
| 首次全量同步 | `GET /api/v3/batch/check/0` |
| 后续增量同步 | `GET /api/v3/batch/check/{checkpoint}` |
| 创建、修改任务 | `POST /api/v2/batch/task` |
| 调整父子任务 | `POST /api/v2/batch/taskParent` |
| 搜索 | `GET /api/v2/search/all` |
| 项目、列、习惯、设置、登录会话 | 多数仍为 V2 |
| 附件配额、上传、下载 | V1 |

网页前端同时创建 V1、V2、V3、V4 HTTP 客户端。任务写入函数仍明确使用 V2 客户端调用 `/batch/task`。V3 当前主要承担同步协议，没有全面替换 V2 业务接口。

### 9. `X-Device` 调查及误判纠正

私有 API 请求带有 `X-Device`。项目当前使用：

```text
platform=web
os=Windows 10
device=Chrome 109.0.0.0
version=4411
id=63b0fb54363a786fba71cc80
```

调查早期曾在浏览器控制台直接请求 V2 同步接口，但漏传了 `X-Device`，得到 500 `access_forbidden`。随后通过变量隔离重新测试：

| 请求条件 | `/api/v2/batch/check/0` |
| --- | --- |
| 不带 `X-Device` | 500 `access_forbidden` |
| 项目原有 Chrome 109 `X-Device` | 200 |
| 当前网页 Chrome 150 `X-Device` | 200 |

随后又用项目原有 Chrome 109 `X-Device` 对 `/api/v2/batch/task` 发送所有数组均为空的无副作用批次，结果为 200，响应包含正常的 `id2etag` 和 `id2error`。

因此：

- 私有 API 仍要求正确携带 `X-Device`。
- 项目现有 Chrome 109 设备信息当前仍有效。
- 本次事故与 `X-Device` 版本过旧无关。
- 以后调查 `access_forbidden` 时必须先核对请求头，不能用漏传 `X-Device` 的结果判断接口退役。

### 10. V2 与 V3 全量同步的真实对比

2026 年 7 月 22 日，在同一有效 `t`、同一 `X-Device`、同一时刻下，并行请求：

```text
GET /api/v2/batch/check/0
GET /api/v3/batch/check/0
```

两者均返回 200。脱敏逐字段比较结果：

- V2：77 个任务、15 个项目。
- V3：77 个任务、15 个项目。
- 任务 ID 集合完全一致。
- 项目 ID 集合完全一致。
- 所有顶层数据块完全一致，包括 `checkPoint`、`syncTaskBean`、`projectProfiles`、`syncTaskOrderBean`、`syncOrderBean`、`syncOrderBeanV3`、`projectGroups`、`filters`、`tags`、`checks` 和 `remindChanges`。
- 当前任务数据中没有任何 V2 独有或 V3 独有字段。
- `content`、`repeatFlag`、`repeatTaskId`、`repeatFirstDate`、`attachments`、`parentId`、`childIds`、`tags`、`items`、日期、状态等业务字段逐项相等，差异数均为 0。

当前代码只依赖：

```python
data["syncTaskBean"]["update"]
data["projectProfiles"]
```

这两个结构在 V2、V3 中相同。

### 11. 为什么当前继续使用 V2 同步

本轮保持 `GET /api/v2/batch/check/0`，理由如下：

1. V2 目前稳定返回 200。
2. V2 与 V3 在当前账号数据上逐字段完全相同。
3. 切换 V3 不会增加任务、项目、附件或遗忘曲线能力。
4. 任务写入、搜索、父子任务和附件本来就继续使用 V2/V1。
5. 429 的发生点是 signon，与同步版本无关。
6. 本轮目标是用最小改动修复认证生命周期，减少额外变量。

网页已经把同步主路径切到 V3，这说明 V3 是当前前端采用的同步版本。未来若出现以下任一情况，应重新评估切换：

- 有效 `t` 和完整 `X-Device` 下，V2 持续失败而 V3 稳定成功。
- V3 开始返回项目需要的新字段，V2 不再返回。
- 滴答公开宣布停止 V2 同步。
- 回归测试确认 V2/V3 响应出现实际语义差异。

如果未来决定切换，现有解析逻辑可以保留，只需把同步 URL 从 `/api/v2/batch/check/0` 改为 `/api/v3/batch/check/0` 并重新完成回归测试。

### 12. 当前接口能力矩阵

| 能力 | 接口 | 2026-07 实测/既有验证 |
| --- | --- | --- |
| 全量同步任务与项目 | `GET /api/v2/batch/check/0` | 200，可用 |
| V3 全量同步 | `GET /api/v3/batch/check/0` | 200，与 V2 当前响应相同 |
| 搜索任务 | `GET /api/v2/search/all` | 200，可用 |
| 创建、更新任务 | `POST /api/v2/batch/task` | 空批次 200；项目既有真实写入链路可用 |
| 调整父子任务 | `POST /api/v2/batch/taskParent` | 既有项目链路保留 |
| 附件配额验证 | `GET /api/v1/attachment/isUnderQuota` | 200，可用 |
| 上传附件 | `POST /api/v1/attachment/upload/{projectId}/{taskId}/{uuid}` | 项目既有真实验证可用 |
| 下载附件 | `GET /api/v1/attachment/{projectId}/{taskId}/{attachmentId}?action=download` | 项目既有真实验证可用 |
| 附件软失活 | V2 `/batch/task` 的 `update + updateAttachments` | 项目迁移文档已有真实验证 |
| 用户名密码登录 | `POST /api/v2/user/signon` | 能返回 `t`；高频请求会触发 429，部分情形可能要求 CAPTCHA |

附件迁移、正文引用和软失活的详细证据见 [`doc/滴答清单词源图片迁移链路沉淀.md`](doc/滴答清单词源图片迁移链路沉淀.md)。

### 13. 故障判断速查

| 现象 | 应先检查 | 禁止动作 |
| --- | --- | --- |
| signon 429 | `dida365.auth-state.json`、重复启动次数、`Retry-After` | 删除有效 `t` 后立即循环登录 |
| 会话验证 401 | `t` 是否被注销或过期 | 把 401 当成临时网络错误无限重试 |
| V2 `access_forbidden` | 是否携带完整 `X-Device` | 立即断言 V2 已退役 |
| V2/V3 502、503、504 | 服务端临时异常和网络状态 | 清除 `t` 或转入 signon |
| 会话验证出现 `getaddrinfo failed` / `NameResolutionError` | 本机 DNS、网络和代理；恢复后重试 | 删除 `t` 或改用用户名密码登录 |
| 附件接口 401 | 当前 `t` 是否有效 | 使用官方 API 口令替代私有 Cookie |
| systemd 快速重启 | Python traceback、登录冷却是否生效 | 让每个新进程再次 signon |

会话验证阶段的 DNS、超时、429 或 5xx 均按临时故障处理：程序保留已经保存的 `t`，不调用 signon，并用中文提示网络、DNS 或代理检查方向。只有验证接口明确返回 401 时，才会清除旧会话并尝试重新登录一次。

### 14. 真实写入测试边界

本次调查阶段只执行了读取请求和所有任务数组均为空的 V2 批次探针，没有创建、更新或删除任务。完成代码修改和本地测试后，真实的“创建临时任务 → 上传附件 → V3/V2 读回 → 删除临时任务”闭环仍需用户再次明确确认，不能自动执行。

## 单词加工管线

自动取词和手动命令都以欧路生词本作为滴答建卡的数据入口：

1. **发布**：用户通过欧路 App、播放器或 `--add-word` 将单词及可选 note 写入欧路。
2. **取词**：常驻服务读取欧路生词本和对应 note。
3. **生成正文**：豆包词源 / 释义讲解 + 多源音标 + note + Anki 链接。
4. **建卡**：克隆「模板任务一」，在「背单词」项目下创建任务。
5. **挂附件**：使用欧路 OpenAPI 密钥下载 Note 图片，并与发音音频、讲解视频一起上传到滴答。
6. **重排**：把音频和视频置于正文顶部，把欧路 Note 图片放在对应“生词语境”之后。

欧路 App 图片信息位于 Note 开头的 `<!--meta files {...} -->` 私有元数据中。`agent/eudic.py` 负责同时提取正文和 `image_list`，`main.py` 与 `agent/dida365.py` 负责下载、上传和正文定位；修改任一环节时必须同步检查另外两处。图片上传或正文更新未完成时不会写入 `word_his.db`，下轮会接续带有“欧路笔记图片同步中”占位符的半成品任务，并跳过已经存在的同名附件。

跨项目约定：播放器以 `**来源：**《`（标签后允许空格）作为已排版 Note 的稳定前缀；本项目据此原样保留播放器格式，并把其他 Note 包装为“生词语境”引用块。修改该前缀或识别规则时，需要同步检查两个项目。

## 目录结构（简要）

| 路径 | 作用 |
| --- | --- |
| `main.py` | 入口：定时调度 + `--add-word` 手动发布到欧路 |
| `agent/` | 各服务客户端封装（滴答 / 豆包 / 欧路 / Anki） |
| `agent/eudic_app_sync.py` | 一次性命令使用的欧路桌面 App 私有图片笔记同步客户端 |
| `dida365_project/` | 滴答清单 API、数据模型与工具 |
| `constants/` | 配置键与各类常量 |
| `utils/` | 音标、Markdown、历史去重等工具 |
| `config.yaml` | 运行配置（首次运行自动生成模板） |
