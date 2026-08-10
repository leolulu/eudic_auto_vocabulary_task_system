# 欧路词典桌面端 Frida 动态分析与图片笔记同步调查

本文档沉淀 2026-08-09 至 2026-08-10 围绕欧路词典 Windows 桌面端开展的一次动态分析。调查目标很具体：确认欧路 App 如何上传 Note 图片、如何把图片与单词笔记关联、脚本需要哪些同步凭据，以及后端如何通过官方 OpenAPI 回读图片并转入滴答清单。

全文以 Frida 为主线，记录实际采用的方法、逐步收缩问题的思考过程、有效结论、错误路径、安全边界和后续复查要点。文中的凭据、完整 Authorization、Cookie、用户 ID、附件 URL 和测试数据都经过省略或符号化处理。

> 结论先行：Frida 属于动态插桩和动态分析工具，广义上属于逆向工程方法。本次工作的合适定性是“在明确授权范围内，为本人账户数据互操作而进行的受控动态分析”。允许开展的依据来自授权、目的、范围和操作边界，不能靠把它排除在“逆向”概念之外来论证。

---

## 1. 调查背景

欧路官方 OpenAPI 已经能够完成以下工作：

- 添加和查询生词；
- 保存、读取和删除文字 Note；
- 读取欧路 App 已经写入的 Note 元数据；
- 使用 OpenAPI 密钥认证下载 Note 元数据中指向的图片。

缺口在“上传图片并把图片写入 Note”。公开文档没有提供相应上传接口，但欧路 Windows App 自身能够完成这件事。因此调查方向从“继续猜 OpenAPI 参数”转向“观察桌面 App 在用户主动保存图片笔记时实际做了什么”。

这一转向很重要。仅凭 Note 最终长成下面的形式，无法证明图片已经成功上传：

```text
<!--meta files { ... "image_list": [...] ... } -->用户正文
```

`image_list` 依赖服务端真实附件对象。只拼一段元数据字符串，或者只把本地文件名写进去，服务端没有对应图片，最终只会得到原样文本、无效引用或类似“乱码”的显示。最初的实测截图已经证明这一点，也促使后续调查严格区分：

1. 图片二进制上传；
2. 服务端返回附件元数据；
3. Note 同步写入；
4. OpenAPI 回读验证；
5. 滴答附件上传与正文内联显示。

只有五层全部通过，才能称为端到端成功。

---

## 2. Frida 算不算“逆向”：准确结论与允许边界

### 2.1 准确的术语判断

Frida 可以在目标进程运行期间枚举模块、定位函数、拦截调用、读取参数和返回值，也可以观察调用栈。这些能力属于动态二进制插桩。广义的逆向工程包含静态反汇编、反编译、协议分析和动态插桩，因此本次工作不能严谨地表述成“完全不属于逆向”。

本次方法与常见的破解型活动存在明显边界：

- 没有修改磁盘上的欧路程序文件；
- 没有制作补丁、破解许可证或绕过付费限制；
- 没有禁用登录校验、TLS 证书校验或服务端权限校验；
- 没有获取其他用户的账号、token、Note 或图片；
- 没有扩大当前账户原本拥有的访问能力；
- 没有把探针做成常驻注入器或分发给无授权目标；
- 只观察本人在客户端界面主动执行“保存自己的图片笔记”时产生的数据。

更精确的描述是：这是一次面向互操作性的黑盒加灰盒动态分析，其中 Frida 用于观察本机进程中已经解密、即将发送或刚刚接收的数据。

### 2.2 为什么当时可以协助开展

判断依据有以下几层：

1. **明确授权**：用户明确要求调查并允许在自己的电脑、自己的欧路账户上进行真实测试。
2. **合法用途特征**：目标是把本人创建的 Note 图片同步到本人使用的滴答清单，属于数据互操作和自动化。
3. **目标边界清楚**：目标进程限定为本机 `eudic.exe`，目标请求限定为欧路图片上传和 Note 同步端点。
4. **数据边界清楚**：只使用专门选择的测试词、测试文字和测试图片；不触碰其他用户的数据。
5. **权限没有扩大**：脚本最终仍向欧路服务器提交正常认证请求，服务器保留完整的权限判断权。
6. **操作可逆且可审计**：Frida 使用 `attach` 临时挂接；会话结束即卸载探针，不修改安装文件。测试 Note 可以通过官方接口删除，测试任务也有明确识别边界。
7. **真实写入单独授权**：观察和离线分析先行；创建欧路记录、生成滴答任务、删除测试任务均在用户明确授权后执行。

因此，允许开展的核心依据是“获得授权的有限动态分析”，与“Frida 是否算逆向”这个名词分类没有直接因果关系。

### 2.3 这份判断不能自动迁移到其他场景

未来若复用本文方法，需要重新确认：

- 操作者是否拥有目标设备、账户和数据的授权；
- 是否仅为兼容性、可访问性、调试或本人数据迁移；
- 是否会绕过登录、付费、DRM、验证码、设备绑定或服务端访问控制；
- 是否可能收集第三方凭据或与目标无关的数据；
- 软件许可协议、服务条款和当地法律是否允许相应操作。

本文是工程调查记录，不构成法律意见。授权不清、目标扩大或出现访问控制绕过需求时，应停止并重新确认。

---

## 3. 为什么选择 Frida

### 3.1 普通网络抓包面临的问题

桌面 App 通过 HTTPS 与 `api.frdic.com` 通信。仅观察网卡流量只能看到 TLS 密文。安装代理证书并做中间人抓包还会引入额外变量：

- App 可能不读取系统代理；
- App 可能有证书固定或自定义 TLS 行为；
- 代理可能改写请求；
- 为抓包临时改变系统网络环境，会污染复现实验；
- 即使看到请求，也未必能继续定位签名由哪个函数生成。

本次最终实现明确设置 `requests.Session.trust_env = False`，也说明桌面 App 的已验证链路与系统代理行为需要隔离看待。

### 3.2 Frida 提供了更靠近事实的观察点

欧路 Windows App 使用 Qt 5。请求进入 TLS 之前，明文会经过 `Qt5Network.dll` 的 `QSslSocket::writeData`；响应完成 TLS 解密后，会经过 `QSslSocket::readData`。在这两个函数上做临时 hook，可以直接看到：

- 完整 HTTP 请求行和请求头；
- multipart 边界和每个 part；
- `application/x-www-form-urlencoded` 请求体；
- HTTP 响应状态和响应体；
- 同一个 socket 上分多段写入或读取的数据。

这个观察点不会关闭 TLS，也不需要伪造证书。网络传输仍然由欧路 App 按原方式加密，Frida 只在本机进程的明文边界复制一份数据给调查脚本。

### 3.3 先 hook 通用库，再碰应用内部地址

调查顺序遵循“稳定观察点优先”：

1. 先 hook Qt 导出函数，确认真实端点和请求结构；
2. 再 hook Qt 的哈希/HMAC 函数，确认鉴权签名算法；
3. 最后才使用 `eudic.exe + 模块相对偏移` 观察同步消息构造结果。

Qt 导出函数名和 ABI 在同一大版本下相对稳定。应用内部偏移高度依赖具体构建，升级后很容易失效，因此它只适合做一次性证据补全，不能成为生产实现的依赖。

---

## 4. 实际运行环境与准备方式

### 4.1 当时的目标环境

- Windows 桌面版欧路词典；
- 进程名：`eudic.exe`；
- 抓到的 User-Agent：`/eusoft_eudic_en_win32/13.6.8/E1482340/`；
- 网络库：`Qt5Network.dll`；
- 基础库：`Qt5Core.dll`；
- Python 通过 `uv` 运行；
- Frida Python binding 只用于临时调查，没有加入后端生产依赖。

欧路 App 需处于已登录状态，并能够在界面里正常保存一条带图片的 Note。调查期间采用 attach 到现有进程的方式，保留用户当前的登录态和界面状态。

### 4.2 最小运行方式

临时探针可按以下形式运行：

```powershell
uv run --with frida python .\eudic_network_probe.py --duration 120
```

探针输出 `hook ready` 后，在欧路 App 内对一个专用测试词执行：

1. 打开 Note 编辑器；
2. 输入容易识别的测试文字；
3. 选择一张专用测试图片；
4. 点击保存；
5. 等待同步请求完成；
6. 结束探针并检查脱敏日志。

本次采用 attach，而没有 spawn：

```python
device = frida.get_local_device()
process = next(
    process
    for process in device.enumerate_processes()
    if process.name.lower() == "eudic.exe"
)
session = device.attach(process.pid)
script = session.create_script(SCRIPT)
script.on("message", on_message)
script.load()
```

若 Frida 因进程权限不同而无法 attach，应先确认本机权限和授权边界。本文没有采用驱动、内核组件或反反调试绕过手段。

### 4.3 本次临时探针分工

调查期间在项目外的临时工作目录中逐步形成三份探针。它们用于取证，没有进入生产依赖：

| 临时脚本 | 主要 hook | 解决的问题 |
| --- | --- | --- |
| `eudic_network_probe.py` | `QSslSocket::writeData/readData` | 还原目标 HTTP 请求、响应、multipart、QYN 形状和调用时序 |
| `eudic_crypto_probe.py` | `QCryptographicHash::hash`、`QMessageAuthenticationCode::hash`、`QNetworkRequest::setRawHeader` | 验证 `urlsign` 的输入、算法、静态客户端键和 Authorization 构造调用栈 |
| `eudic_sync_probe.py` | `qCompress`、`QNetworkAccessManager::post`、应用内部消息构造函数 | 确认 `msgz` 来源、压缩边界和同步 POST 调用链 |

三份脚本体现的是调查的渐进过程，不能直接当成稳定 SDK：网络探针最通用；加密探针依赖 Qt 导出符号；同步探针还依赖特定欧路构建的模块相对偏移。本文保留了足够的关键片段和结论，未来复查应根据新版模块重新定位，而不应盲目运行旧偏移。

---

## 5. 第一阶段：从 TLS 明文边界找出真实请求

### 5.1 Hook 位置

网络探针定位下面两个 Qt 导出函数：

```javascript
const network = Process.getModuleByName('Qt5Network.dll');
const writeData = network.getExportByName(
  '?writeData@QSslSocket@@MEAA_JPEBD_J@Z'
);
const readData = network.getExportByName(
  '?readData@QSslSocket@@MEAA_JPEAD_J@Z'
);
```

请求侧在 `onEnter` 读取 `args[1]` 指向的缓冲区和 `args[2]` 给出的长度；响应侧在 `onEnter` 保存输出缓冲区地址，在 `onLeave` 使用返回值确定实际读取长度。

```javascript
Interceptor.attach(writeData, {
  onEnter(args) {
    const length = args[2].toUInt32();
    if (length <= 0 || length > 16 * 1024 * 1024) return;
    send(
      { event: 'write', socket: args[0].toString(), length },
      args[1].readByteArray(length)
    );
  }
});
```

### 5.2 为什么需要按 socket 重组

一次 HTTP 请求不保证对应一次 `writeData`。请求头、multipart 文件和尾部可能分成多个 chunk；同样，响应也可能分段、使用 chunked encoding 或 gzip。探针因此按 `args[0]` 的 socket 地址维护缓冲区：

- 找到 `\r\n\r\n` 后解析请求头；
- 根据 `Content-Length` 等待完整 body；
- 响应支持 `Transfer-Encoding: chunked`；
- 响应支持 `Content-Encoding: gzip`；
- 单 socket 缓冲区设定 16 MB 上限，防止无限增长。

这个细节直接决定抓包是否可靠。只打印每次 `writeData` 的开头，很容易把一个 multipart 请求误判成多条无关数据，或者漏掉真正的 `body` part。

### 5.3 尽早缩小捕获范围

欧路查词时会产生大量前缀搜索、词典正文、发音和翻译请求。探针只对以下路径做完整解析：

```text
/api/v2/appsupport/storeAttachments
/api/v2/dicts/DictNote
/api/v2/customize/sync
```

其他请求最多记录请求行，用于确认时间顺序。这样既减少日志噪声，也降低无意收集与目标无关数据的风险。

### 5.4 第一阶段确认的两个核心端点

手动保存图片 Note 时，观察到的关键顺序是：

```text
POST /api/v2/appsupport/storeAttachments
POST /api/v2/customize/sync
```

前一个端点上传图片二进制并返回服务端附件元数据；后一个端点把包含该元数据的 Note 写入同步数据。

这说明图片 Note 是一个两阶段协议。直接跳到第二阶段无法凭空生成有效图片附件。

---

## 6. 第二阶段：还原图片上传 multipart

### 6.1 已抓到的请求结构

图片上传使用：

```text
POST https://api.frdic.com/api/v2/appsupport/storeAttachments
Content-Type: multipart/form-data; boundary=...
Authorization: QYN <base64-json>
```

multipart 至少包含两类 part：

1. 图片 part
   - form field 名：原始文件名；
   - `filename`：原始文件名；
   - `Content-Type: application/octet-stream`；
   - payload：图片原始字节。
2. `body` part
   - 无单独 MIME 类型；
   - 内容为 JSON。

脱敏后的 `body` 结构：

```json
{
  "attachment": [
    {
      "id": "<与 multipart field 同名的文件名>",
      "type": "image"
    }
  ]
}
```

服务端响应会为每张图片返回一组后续 Note 所需的字段，实际观察和 OpenAPI 回读涉及：

```json
{
  "id": "<server-image-id>",
  "type": "image",
  "url": "<authenticated-download-url>",
  "thumb": "<thumbnail-url>",
  "orgfilename": "<original-filename>"
}
```

### 6.2 生产实现中的稳定文件名

命令行上传不能依赖用户本地文件名来判断重试是否重复。最终实现以图片内容的 SHA-256 前 16 个十六进制字符生成稳定名称：

```text
eudic-cli-<16-hex-digest>.<suffix>
```

好处包括：

- 相同内容重复执行时得到相同名称；
- 同一次命令中内容相同的图片可以精确去重；
- OpenAPI 回读时可用 `orgfilename` 与预期图片逐一对齐；
- 写请求结果不确定时，可以先读最终状态，再决定是否需要重试。

---

## 7. 第三阶段：拆解 QYN 鉴权

### 7.1 先观察“形状”，不打印秘密

网络探针没有直接打印 Authorization。它只记录：

- scheme 是否为 `QYN`；
- 总长度；
- Base64 解码后是否为 JSON；
- JSON 字段名、字段类型和字符串长度；
- `token` 是否等于本机配置的 `SyncToken`；
- `userid` 是否等于本机配置的 `SyncUserId`；
- 签名公式候选是否匹配。

抓到的 QYN 在脱敏后可表示为：

```json
{
  "fl": "<captured-client-state>",
  "lc": "<captured-client-state>",
  "t": "ABI<encoded-time>",
  "token": "<SyncToken>",
  "urlsign": "<request-signature>",
  "userid": "<SyncUserId>",
  "v_dict": true
}
```

外层格式为：

```text
Authorization: QYN Base64(compact-json)
```

### 7.2 为什么先做候选公式，再 hook 加密函数

从网络数据可以看到 `urlsign`，也知道当前请求 path、token 和若干客户端常量。第一步用受限候选集尝试常见组合：

- SHA-1；
- HMAC-SHA1；
- path、完整 URL、时间字段、token 等有限输入；
- 空串、冒号、竖线、`&` 等少量分隔符；
- Base64、URL-safe Base64 和 hex 输出。

候选搜索得到：签名与“以客户端静态键对请求 path 做 HMAC-SHA1，再 Base64”一致。

候选搜索只能提供强线索，仍可能存在偶然匹配或输入理解错误。下一步用 Frida 直接 hook Qt 加密函数进行确认。

### 7.3 Hook Qt 哈希与 HMAC

加密探针定位 `Qt5Core.dll` 的两个导出函数：

```javascript
const core = Process.getModuleByName('Qt5Core.dll');

const staticHash = core.getExportByName(
  '?hash@QCryptographicHash@@SA?AVQByteArray@@AEBV2@W4Algorithm@1@@Z'
);

const staticHmac = core.getExportByName(
  '?hash@QMessageAuthenticationCode@@SA?AVQByteArray@@AEBV2@0W4Algorithm@QCryptographicHash@@@Z'
);
```

探针分别复制：

- 输入字节；
- HMAC key；
- Qt 算法枚举值；
- 返回的 `QByteArray`；
- 设置 Authorization 时的调用栈。

Python 侧把真实 `SyncToken` 和 `SyncUserId` 立刻替换成 `<SyncToken>`、`<SyncUserId>`，再允许输出。随后把每个 HMAC 结果的 Base64 与当前 QYN 的 `urlsign` 比较，确认匹配的调用。

最终公式写成抽象形式如下：

```text
urlsign = Base64(HMAC-SHA1(K_client, request_path))
```

静态客户端键没有在本文重复。当前实现可在 `agent/eudic_app_sync.py` 中找到；欧路升级后应重新验证，不能假定永久不变。

### 7.4 时间字段

实测得到的生成方式为：

```text
t = "ABI" + Base64(decimal_string(unix_time + 0x12E70A7))
```

因此 `t` 每次请求动态生成，不属于需要保存的登录凭据。

### 7.5 `fl` 和 `lc` 是否必需

桌面 App 原始请求带有 `fl`、`lc`。仅凭抓包无法判断它们是认证必需字段、设备状态，还是兼容性残留。为避免把不必要、来源不明的客户端状态写入配置，后续做了四组空同步实验：

1. 完整字段；
2. 省略 `fl`；
3. 省略 `lc`；
4. 同时省略 `fl` 和 `lc`。

四组请求都返回 HTTP 200、有效 `EudicSync` XML 和服务端时间戳。随后又用同时省略两项的 QYN 完成一次真实图片 Note：

- 图片上传成功；
- Note 写入成功；
- 官方 OpenAPI 回读文字一致；
- 图片数量、文件名和元数据一致；
- 测试 Note 最后通过官方 DELETE 删除并确认消失；
- 测试期间没有把该词加入生词本，也没有生成滴答任务。

由此确认，脚本持久化只需要：

```text
SyncToken
SyncUserId
```

`t`、`urlsign` 和 `v_dict` 由请求端生成；`fl`、`lc` 不进入当前最小实现。

---

## 8. 第四阶段：还原 customize/sync 与 msgz

### 8.1 请求外层

Note 同步端点为：

```text
POST https://api.frdic.com/api/v2/customize/sync
Content-Type: application/x-www-form-urlencoded
Authorization: QYN <base64-json>
```

表单字段为：

```text
productid=23
langid=3
msgz=<encoded-sync-message>
```

### 8.2 Frida 如何定位消息构造

这一阶段使用了三种 hook：

1. `QNetworkAccessManager::post`：在 body 出现 `productid=23&langid=3&msgz=` 时记录调用栈；
2. `Qt5Core.dll!qCompress`：当输入里出现专用测试词时，复制压缩前数据；
3. `eudic.exe + 0x1f8ef0`：读取当前构建中负责返回 `msgz` 源字符串的内部函数结果。

第三种方式最脆弱。偏移 `0x1f8ef0` 只对当时抓取的欧路构建有效，升级、重编译或 ASLR 之外的布局变化都可能使它失效。日志里部分应用符号还显示成 OpenCC 相关名称，这属于符号信息不足时的错误归因，不能据此理解业务函数。

### 8.3 QByteArray 与 std::string 读取

Frida 不能直接把 C++ 对象当字符串读取。探针针对当时的 Qt/MSVC ABI 实现了两个临时解析器：

- `QByteArray`：读取共享数据指针、长度和数据偏移；
- `std::string`：读取 size、capacity，并根据 small-string optimization 判断数据位于对象内还是堆上。

这些内存布局都属于版本相关假设。探针设置了合理长度上限，并在异常时返回空缓冲区，避免错误地址导致进程崩溃。未来升级时，必须先用无害数据验证布局，再读取真实请求。

### 8.4 msgz 的最终编码

结合 Frida 观察、离线解码和最小请求复现，最终确认：

```python
compressor = zlib.compressobj(level=8, wbits=-15)
raw_deflate = compressor.compress(xml.encode("utf-8")) + compressor.flush()
msgz = base64.b64encode(b"QY" + raw_deflate).decode("ascii")
```

解码公式对应为：

```python
decoded = base64.b64decode(msgz)
assert decoded[:2] == b"QY"
xml = zlib.decompress(decoded[2:], wbits=-15).decode("utf-8")
```

关键细节：

- Base64 解码后有两字节 `QY` 前缀；
- 压缩部分使用 raw DEFLATE；
- `wbits=-15`，没有常规 zlib header；
- 直接对整段 `QY...` 调用普通 `zlib.decompress()` 会失败。

---

## 9. 第五阶段：Note XML 与图片元数据

### 9.1 先取得服务端时间戳

客户端先发送空 `EudicSync`，根节点带当前时间作为 `lastSyncTimestamp`，并包含空的同步分类：

```xml
<EudicSync version="1.0" lastSyncTimestamp="<utc-time>">
  <StudyCategory />
  <StudyLists />
  <Annotations />
  <WordCards />
  <Sentences />
  <UserMemory />
  <Histories />
</EudicSync>
```

服务端响应根节点中的 `serverTimestamp` 用于后续 Note 写入。

### 9.2 Note 写入节点

图片上传完成后，在 `Annotations` 下写入：

```xml
<CustomizeListItem
  word="<word>"
  itemType="-9999"
  note="<serialized-note>"
  hl=""
  addTimeP="<utc-time>"
  deleted="0"
  serverTimestamp="<server-timestamp>"
  localTimestamp="<utc-time>"
  meta=""
/>
```

`serialized-note` 的逻辑结构为：

```text
<!--meta files {
  "font_style":"normal",
  "image_list":[<storeAttachments 返回的附件对象>],
  "public_status":0
} -->用户正文
```

欧路 App 会把普通空格保存成 `&nbsp;`。因此上传端按 App 行为序列化，读取端统一兼容以下形式并还原为空格：

```text
&nbsp;
&#160;
&#xA0;
U+00A0
```

### 9.3 为什么 Note 在前、单词在后

一次性命令采用：

```text
保存图片 Note
→ OpenAPI 回读并核对文字/图片
→ 添加生词
```

常驻服务以“生词进入在线生词本”为可抓取信号。如果先添加生词，常驻服务可能在图片 Note 尚未就绪时创建一条缺少笔记的滴答任务。Note 先行并经过回读校验后再加词，可以把“生词可见”作为整组数据已经准备完成的提交点。

若 Note 已成功、添加生词失败，使用完全相同的命令重试时会先回读 Note：

- 文字和图片完全一致：复用已有 Note，继续加词；
- 任何内容不一致：报告冲突，不覆盖；
- 单词已经存在：按既有历史生词规则停止，不借此给历史数据补写 Note。

---

## 10. 凭据来源与回退设计

### 10.1 桌面 App 配置

默认位置：

```text
%APPDATA%\Francochinois\eudic\config.ini
```

只读取 `[COMMON]` 下完整的一对：

```ini
SyncToken=<redacted>
SyncUserId=<redacted>
```

启动时间、启动次数、用户名、邮箱和抓包中出现的其他客户端状态都不参与当前最小鉴权。

### 10.2 项目配置回退

读取顺序：

1. 欧路 App 配置中存在完整 `SyncToken + SyncUserId`：使用 App 配置；
2. App 文件不存在、不可读或缺任一项：读取项目 `config.yaml`；
3. `config.yaml` 中存在完整 `eudic_sync_token + eudic_sync_user_id`：使用 YAML；
4. 两处都没有完整的一对：在发出写请求前报错，并说明两个提供位置。

两项凭据不会跨来源拼接。请求一旦发出，也不会因响应失败而切换到另一来源自动重试，因为第一次请求可能已经在服务端生效。

新生成的 `config.yaml` 模板带两个空白可选键；旧用户配置不强制补写，也不参加常规服务启动的必填检查。只有调用 `--note-image` 且 App 配置不可用时才需要填写。

---

## 11. Frida 调查中的思考方法

### 11.1 先证明“发生了什么”，再解释“怎么生成”

开始阶段只关心三个问题：

- 保存图片时访问了哪个 endpoint；
- 请求体有几个阶段；
- 图片对象如何进入 Note。

确认端点后才分析 Authorization 和 `msgz`。这种顺序避免一开始就在整个二进制里搜索“图片上传函数”，也避免被大量无关词典请求带偏。

### 11.2 每次只增加一个观察层

探针演进顺序为：

```text
QSslSocket 明文
→ HTTP 重组和目标路径过滤
→ QYN 结构化脱敏
→ 有限签名公式候选
→ Qt HMAC 直接确认
→ 同步 POST 调用栈
→ 压缩/消息构造观察
→ 独立 Python 最小复现
→ 官方 OpenAPI 回读
```

每一层都对上一层的推断做验证。只有 Frida 观察而没有独立请求复现，仍然可能把客户端偶然状态误当成协议必需项；只有请求复现而没有 Frida 观察，又容易靠猜测遗漏真实格式。

### 11.3 用唯一测试标记降低噪声

同步探针只在缓冲区包含专用测试词时输出压缩数据。这样可以：

- 从后台自动同步中识别本次人工操作；
- 避免记录大量既有生词和 Note；
- 让调用栈、请求体和 UI 操作形成一一对应；
- 便于操作后删除测试数据。

### 11.4 把“200”降级为中间证据

本次多次遇到“请求成功”与“用户看到正确结果”并不等价：

- HTTP 200 可能只表示服务器接受了格式；
- Note 里出现 `meta files` 不代表附件存在；
- 滴答出现附件卡片不代表它按图片内联渲染；
- 本地函数返回不代表云端最终状态一致。

最终采用的验收链为：

```text
欧路上传响应
→ 欧路同步响应
→ 官方 OpenAPI 回读 Note
→ 认证下载并检查 Content-Type/大小
→ 滴答上传
→ 滴答重新同步读回附件
→ 正文中存在该附件的图片引用
→ 用户界面人工验收
```

### 11.5 最小条件必须通过消融实验确认

原始 QYN 出现某字段，只能证明桌面 App 当时发送了它。要判断脚本是否需要长期保存该字段，应逐项省略并观察：

- 空同步能否完成；
- 服务端时间戳是否有效；
- 真实图片 Note 能否保存；
- OpenAPI 能否读回一致状态。

`fl`、`lc` 的四组实验就是这一思路的直接产物。它显著简化了部署配置，也减少了对不透明客户端状态的依赖。

---

## 12. 日志脱敏与安全设计

### 12.1 在采集端脱敏

探针优先输出结构，而不输出秘密：

- Authorization 仅输出 scheme、长度和字段形状；
- token/user ID 仅输出“是否与本机配置匹配”的布尔值；
- 原始 header preview 用正则替换 Authorization、Cookie、Set-Cookie 和 token/auth 类自定义头；
- 加密探针在 Python 回调入口立即把已知凭据替换成符号名；
- 图片仅记录长度、文件名和 MIME，不把二进制写入日志；
- 目标路径以外的响应体不记录。

这是比“先完整保存、事后再清洗”更安全的策略。原始日志一旦落盘或进入终端历史，就可能被备份、索引或误提交。

### 12.2 仍需注意的风险

Frida hook 的是明文边界，理论上能够看到目标进程中的敏感数据。未来探针应继续遵守：

- 目标路径白名单；
- 运行时长上限；
- 单次专用测试词；
- 不打印完整 Authorization；
- 不把配置文件内容整体读入日志；
- 不提交抓包日志；
- 调查结束后卸载脚本并检查临时文件。

本文也没有收录真实附件 URL。部分欧路图片 URL 需要认证，仍应视为敏感资源定位信息。

---

## 13. 失败路径与纠正

### 13.1 把元数据文字当成图片上传成功

早期测试任务只显示 `&nbsp;` 等文本，没有图片。根因是把“Note 文本已写入”误当成“图片附件已上传并关联”。纠正方式是把 `storeAttachments`、同步 Note、OpenAPI 回读图片列表和真实图片下载拆成四个独立检查点。

### 13.2 只看最终 Note，猜不到上传协议

OpenAPI 回读只能看到 `image_list` 的结果，无法知道 multipart field 如何命名、图片 part 使用什么 MIME、还有没有额外的 `body` part。Frida 在 `QSslSocket` 明文边界给出了这些缺失信息。

### 13.3 盲目保存所有 QYN 字段

直接照抄原始 QYN 会把 `fl`、`lc` 和动态字段一并变成部署负担。消融实验确认它们可以省略后，配置缩减到 `SyncToken + SyncUserId`。

### 13.4 依赖应用内部固定偏移

内部偏移适合一次性确认某个构造函数的输出，不适合作为生产方案。最终后端只实现已经通过网络和服务端回读确认的协议，不运行 Frida，也不注入欧路进程。

### 13.5 图片进入滴答后只显示成附件卡片

欧路链路跑通后，第一次滴答展示仍把图片当作普通附件。最终验收要求进一步确认：

- 滴答附件保留正确图片文件名；
- 下载内容的真实 `Content-Type` 为 `image/*`；
- 任务正文使用附件模型生成的图片引用；
- 图片引用放在“生词语境”之后；
- 重新同步后引用和附件 ID 一致。

这说明端到端调查不能在供应端成功后提前结束，消费端渲染语义同样需要读回验证。

---

## 14. 最终生产架构

Frida 只存在于调查阶段。生产运行路径如下：

```text
一次性 CLI --note-image
  ↓
读取欧路 App 配置，必要时回退 config.yaml
  ↓
私有端点上传图片
  ↓
私有同步端点保存 Note
  ↓
官方 OpenAPI 回读并精确核对
  ↓
官方 OpenAPI 添加生词
  ↓
常驻服务抓取生词和 Note
  ↓
官方 OpenAPI 认证下载图片
  ↓
上传为滴答图片附件并写入正文引用
  ↓
重新同步读回，成功后写入 word_his.db
```

关键代码入口：

| 文件 | 作用 |
| --- | --- |
| `agent/eudic_app_sync.py` | 私有图片上传、QYN 鉴权、EudicSync 编码和图片 Note 写入 |
| `agent/eudic.py` | 官方 OpenAPI 读取 Note、解析 `meta files`、认证下载图片 |
| `main.py` | 一次性发布顺序、冲突处理、常驻抓取与滴答任务构造 |
| `agent/dida365.py` | 图片附件上传、断点接续、正文定位和完成状态校验 |
| `test/test_eudic_app_sync.py` | 私有同步协议、凭据优先级和重试语义测试 |
| `test/test_eudic_note_images.py` | Note 图片解析、下载与滴答展示测试 |
| `test/manual_eudic_note_image_to_dida.py` | 带 dry-run 的真实端到端验收脚本 |

---

## 15. 已验证结论、版本相关项与未知项

### 15.1 已验证

- 图片上传端点和 multipart 结构；
- 同步端点、表单字段和 `msgz` 编码；
- QYN 外层结构；
- `SyncToken`、`SyncUserId` 与 QYN 字段的对应关系；
- path HMAC-SHA1 签名关系；
- 时间字段动态生成方式；
- `fl`、`lc` 可同时省略；
- `CustomizeListItem` 的图片 Note 写入结构；
- `meta files.image_list` 可被官方 OpenAPI 回读；
- 图片 URL 需使用欧路 OpenAPI 密钥认证下载；
- 图片能够作为滴答图片附件内联显示；
- 文字加图片的真实端到端链路通过用户界面验收；
- 凭据只需 `SyncToken + SyncUserId`，App 配置优先、YAML 回退可行。

### 15.2 版本相关

- 欧路 User-Agent 版本号；
- Qt 导出函数修饰名；
- `QByteArray` 和 `std::string` 内存布局；
- `eudic.exe + 0x1f8ef0` 内部偏移；
- 客户端静态签名键；
- 私有端点、XML 字段和响应 JSON 结构。

### 15.3 未覆盖

- macOS、Android、iOS 版欧路的实现；
- 多账户同时登录和账号切换时的配置行为；
- 欧路未来版本是否更换签名算法或私有端点；
- 超过当前 50 MB 本地保护上限的图片；
- 视频、音频或其他 Note 附件类型；
- 私有同步协议的官方兼容承诺。

---

## 16. 欧路升级后的复查顺序

出现 401、403、响应格式异常、Note 回读无图或 App 升级后，按以下顺序排查：

1. 用官方 OpenAPI 验证 API key 和普通 Note 能否正常读取；
2. 在欧路 App 中人工保存一个专用测试图片 Note，确认官方客户端自身正常；
3. 运行最小 Frida 网络探针，只观察两个目标 POST；
4. 对比 endpoint、User-Agent、multipart part 和 QYN 字段形状；
5. 若仅签名失败，再启用 Qt HMAC 探针；
6. 若 `msgz` 无法解析，再检查 `QY + raw DEFLATE`；
7. 只有前面都无法解释时，才使用新的应用内部偏移探针；
8. 更新独立 Python 复现代码和单元测试；
9. 使用全新测试词执行 OpenAPI 回读；
10. 获得真实写入授权后再做完整滴答端到端验收。

复查时不要默认复制旧日志里的动态 Authorization，也不要把失败请求自动换凭据重放。每一步都要以当前 App 的真实行为和服务端最终状态为准。

---

## 17. 本次调查最值得复用的经验

1. **选择正确观察层比扩大工具范围更重要。** Qt 的 TLS 明文边界一次解决了 HTTPS、代理和请求定位问题。
2. **先通用库、后内部偏移。** 先拿稳定事实，再用脆弱 hook 补证据。
3. **结构化脱敏要发生在采集阶段。** 只输出字段形状和匹配布尔值，能满足分析，又不制造凭据副本。
4. **抓到字段不代表字段必需。** 用消融实验寻找最小协议，能显著降低后续配置和兼容负担。
5. **动态观察与独立复现必须互相校验。** Frida 告诉我们 App 做了什么；最小 Python 客户端证明我们理解了什么。
6. **HTTP 成功只是中间状态。** 最终验收必须依赖官方读取接口、真实附件下载和消费端重新同步。
7. **写入顺序是业务一致性的一部分。** Note 先行、回读校验、最后加词，把生词出现变成完整记录的提交信号。
8. **私有协议应被隔离。** 常驻读取继续使用官方 OpenAPI；私有同步仅服务于 `--note-image`，故障不会污染普通生词同步。
9. **调查权限来自授权和边界。** 工具名称无法替代权限判断；Frida 可以用于正当互操作，也可能被滥用，关键在目标、数据、权限和操作方式。

---

## 18. 相关提交与测试结果

本次功能落地提交：

```text
097202a 支持欧路图片笔记同步与鉴权回退
```

提交前验证结果：

```text
专项测试：62 passed
全量测试：106 passed, 1 skipped
真实无写入空同步：成功取得 serverTimestamp
真实图片 Note：上传、OpenAPI 回读、认证下载均通过
真实欧路 → 滴答任务：文字与图片内联展示通过用户验收
```

Frida 探针属于调查工具，没有加入生产依赖。生产代码不要求安装 Frida，也不要求运行欧路进程。一次性图片上传需要一套完整同步凭据：程序优先读取本机欧路 App 配置文件，文件不可用时回退到项目 `config.yaml`。
