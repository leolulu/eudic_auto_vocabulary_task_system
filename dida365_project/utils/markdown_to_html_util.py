import re


def markdown_to_html(markdown_text: str) -> str:
    """
    一个经过彻底重构和优化的 Python 函数，用于将 Markdown 文本转换为 HTML。
    此版本采用消费式解析器，确保转换的完整性，并完美支持多级列表和段落。

    它不依赖任何第三方库，可以处理以下 Markdown 语法：
    - 标题 (h1 到 h6)
    - 加粗 (**), 斜体 (*), 加粗并斜体 (***)
    - 有序列表和无序列表（支持健壮的多级嵌套）
    - 表格 (并附带现代化 CSS 样式)
    - 链接和图片
    - 删除线 (~~)
    - 代码块 (多行和行内)
    - 引用块（支持内部空行）
    - 正确的段落和换行 (<br>)
    """
    # 预处理行
    lines = markdown_text.strip().replace("\r\n", "\n").split("\n")
    html_output = []

    i = 0
    while i < len(lines):
        line = lines[i]

        # 1. 标题 (h1-h6)
        if re.match(r"^#+\s", line):
            match = re.match(r"^(#+)\s+(.*)", line)
            level = len(match.group(1))
            content = _parse_inline(match.group(2).strip())
            html_output.append(f"<h{level}>{content}</h{level}>")
            i += 1
            continue

        # 2. 代码块 (```...```)
        if line.strip().startswith("```"):
            block_lines = []
            lang_match = re.match(r"```(\w*)", line.strip())
            lang = lang_match.group(1) if lang_match else ""
            i += 1
            while i < len(lines) and not lines[i].strip() == "```":
                block_lines.append(lines[i])
                i += 1
            i += 1  # 跳过结束的 ```
            code_content = "\n".join(block_lines)
            escaped_code = code_content.replace("&", "&").replace("<", "<").replace(">", ">")
            class_attr = f' class="language-{lang}"' if lang else ""
            html_output.append(f"<pre><code{class_attr}>{escaped_code}</code></pre>")
            continue

        # 3. 引用块 (>)
        if line.strip().startswith(">"):
            block_lines = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                # 移除 '>' 和可选的前导空格
                block_lines.append(re.sub(r"^>\s?", "", lines[i]))
                i += 1
            # 递归解析引用块内部的内容，这允许引用内包含列表、标题等
            inner_content = markdown_to_html("\n".join(block_lines))
            html_output.append(f"<blockquote>{inner_content}</blockquote>")
            continue

        # 4. 列表 (*, -, +, 1.)
        if re.match(r"^\s*(\*|\-|\+|\d+\.)\s", line):
            block_lines = []
            while i < len(lines) and re.match(r"^\s*(\*|\-|\+|\d+\.)\s", lines[i]):
                block_lines.append(lines[i])
                i += 1
            html_output.append(_render_list(block_lines))
            continue

        # 5. 水平线
        if re.match(r"^\s*(---|___|\*\*\*)\s*$", line):
            html_output.append("<hr>")
            i += 1
            continue

        # 6. 表格
        if "|" in line and (i + 1) < len(lines) and re.match(r"^\s*\|?(:?-+:?\|)+:?-+:?\s*\|?\s*$", lines[i + 1]):
            block_lines = []
            # 添加表头和分隔行
            block_lines.append(lines[i])
            i += 1
            block_lines.append(lines[i])
            i += 1
            # 添加表体
            while i < len(lines) and "|" in lines[i]:
                block_lines.append(lines[i])
                i += 1
            html_output.append(_render_table(block_lines))
            continue

        # 7. 段落 (默认情况)
        if line.strip():
            block_lines = [line]
            i += 1
            # 持续收集行，直到遇到空行或另一个块元素的开始
            while (
                i < len(lines)
                and lines[i].strip()
                and not re.match(r"^#+\s", lines[i])
                and not lines[i].strip().startswith("```")
                and not lines[i].strip().startswith(">")
                and not re.match(r"^\s*(\*|\-|\+|\d+\.)\s", lines[i])
                and not re.match(r"^\s*(---|___|\*\*\*)\s*$", lines[i])
                and not ("|" in lines[i] and (i + 1) < len(lines) and re.match(r"^\s*\|?(:?-+:?\|)+:?-+:?\s*\|?\s*$", lines[i + 1]))
            ):
                block_lines.append(lines[i])
                i += 1

            paragraph_content = " ".join(ln.strip() for ln in block_lines)
            # 处理行尾双空格的硬换行
            paragraph_content = re.sub(r" {2,}", "<br>", paragraph_content)
            html_output.append(f"<p>{_parse_inline(paragraph_content)}</p>")
            continue

        # 如果是空行，直接跳过
        i += 1

    # 添加全局样式（只添加一次）
    styles = _get_global_styles()
    final_html = styles + "\n".join(part for part in html_output if part)
    return final_html.replace("\n", "").strip()


def _parse_inline(text: str) -> str:
    # 行内代码: `code` (需要先处理，避免其内容被其他规则匹配)
    text = re.sub(r"`(.*?)`", lambda m: f"<code>{m.group(1).replace('&', '&').replace('<', '<').replace('>', '>')}</code>", text)
    # 图片: ![alt](src)
    text = re.sub(r"!\[(.*?)\]\((.*?)\)", r'<img src="\2" alt="\1">', text)
    # 链接: [text](url)
    text = re.sub(r"\[(.*?)\]\((.*?)\)", r'<a href="\2">\1</a>', text)
    # 加粗并斜体: ***text***
    text = re.sub(r"\*\*\*(.*?)\*\*\*", r"<b><i>\1</i></b>", text)
    # 加粗: **text**
    text = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", text)
    # 斜体: *text*
    text = re.sub(r"\*(.*?)\*", r"<i>\1</i>", text)
    # 删除线: ~~text~~
    text = re.sub(r"~~(.*?)~~", r"<del>\1</del>", text)
    return text


def _render_list(lines: list) -> str:
    """使用堆栈渲染可能存在嵌套的列表"""
    html = ""
    list_stack = []  # 堆栈存储元组: (indent_level, list_type)

    for line in lines:
        if not line.strip():
            continue

        match = re.match(r"^(\s*)(\*|\-|\+|\d+\.)\s+(.*)", line)
        if not match:
            continue

        indent_str, marker, content = match.groups()
        indent_level = len(indent_str)
        list_type = "ol" if marker[-1] == "." else "ul"

        while list_stack and indent_level < list_stack[-1][0]:
            html += f"</{list_stack.pop()[1]}>\n</li>\n"

        if not list_stack or indent_level > list_stack[-1][0]:
            if list_stack:
                html = html.rstrip() + f"\n<{list_type}>\n"
            else:
                html += f"<{list_type}>\n"
            list_stack.append((indent_level, list_type))
        elif list_type != list_stack[-1][1]:
            html += f"</li>\n</{list_stack.pop()[1]}>\n"
            list_stack.pop()
            html += f"<{list_type}>\n"
            list_stack.append((indent_level, list_type))
        else:
            html += "</li>\n"

        html += f"<li>{_parse_inline(content.strip())}"

    while list_stack:
        html += f"</li>\n</{list_stack.pop()[1]}>\n"

    return html.strip()


def _render_table(table_lines: list) -> str:
    """将 Markdown 表格文本块转换为带样式的、响应式的 HTML 表格"""
    header_line = table_lines[0]
    alignment_line = table_lines[1]
    body_lines = table_lines[2:]

    alignments = []
    for align_cell in alignment_line.strip().strip("|").split("|"):
        align_cell = align_cell.strip()
        if align_cell.startswith(":") and align_cell.endswith(":"):
            alignments.append("center")
        elif align_cell.endswith(":"):
            alignments.append("right")
        else:
            alignments.append("left")

    headers = [h.strip() for h in header_line.strip().strip("|").split("|")]

    # <<< 第 1 处修改：在表格开始前，添加 div 容器
    html = '<div class="table-wrapper"><table class="md-table">'

    html += "<thead><tr>"
    for i, header in enumerate(headers):
        align_style = f'style="text-align: {alignments[i]}"' if i < len(alignments) else ""
        html += f"<th {align_style}>{_parse_inline(header)}</th>"
    html += "</tr></thead>"

    html += "<tbody>"
    for line in body_lines:
        if not line.strip() or "|" not in line:
            continue
        html += "<tr>"
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        for i in range(len(headers)):
            cell_content = cells[i] if i < len(cells) else ""
            align_style = f'style="text-align: {alignments[i]}"' if i < len(alignments) else ""
            html += f"<td {align_style}>{_parse_inline(cell_content)}</td>"
        html += "</tr>"
    html += "</tbody></table>"

    # <<< 第 2 处修改：在最后闭合 div 容器
    html += "</div>"

    return html


def _get_global_styles() -> str:
    """
    返回一个经过美化的、“Fancy”风格的全局内联CSS样式。
    - 主题色: 森林绿 (#228B22)
    - 设计感: 使用卡片式设计、阴影和渐变，增强视觉区分度。
    """
    # 森林绿: #228B22
    # 辅助绿 (用于链接): #28a745
    # 柔和背景: #fbfbfb
    # 主要文本: #333333
    # 边框/分割线: #e0e0e0
    # 引用块背景: #f0fff0 (非常浅的薄荷绿)

    return (
        "<style>"
        # --- 全局和基础样式 ---
        "body { "
        "background-color: #fbfbfb; "
        'font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; '
        "line-height: 1.7; "
        "color: #333333; "
        "} "
        "a { "
        "color: #28a745; "
        "text-decoration: none; "
        "border-bottom: 1px dotted #28a745; "
        "transition: all 0.2s ease-in-out; "
        "} "
        "a:hover { "
        "color: #228B22; "
        "border-bottom: 1px solid #228B22; "
        "} "
        # --- 标题样式 ---
        "h1, h2, h3, h4, h5, h6 { "
        "color: #228B22; "  # 标题使用主题色
        "margin-top: 2em; "
        "margin-bottom: 0.8em; "
        "font-weight: 600; "
        "} "
        "h1 { border-bottom: 2px solid #e0e0e0; padding-bottom: 0.3em; }"
        "h2 { border-bottom: 1px solid #e0e0e0; padding-bottom: 0.3em; }"
        # --- 表格wrapper样式 ---
        ".table-wrapper { "
        "overflow-x: auto; "  # 这是核心！当内容溢出时，在水平方向显示滚动条
        "margin: 2em 0; "  # 将外边距从表格移到这里
        "-webkit-overflow-scrolling: touch; "  # 在 iOS 上提供更平滑的滚动体验
        "} "
        # --- 表格样式 (已更新为森林绿表头) ---
        ".md-table { "
        "box-sizing: border-box; "
        "width: 100%; "
        "border-collapse: collapse; "
        "font-family: inherit; "
        "box-shadow: 0 4px 15px rgba(0, 0, 0, 0.08); "  # 增强阴影
        "border-radius: 10px; "
        "overflow: hidden; "
        "border: 1px solid #e0e0e0; "
        "} "
        ".md-table thead tr { "
        "background-color: #228B22; "  # <<< 这里是您指定的森林绿表头
        "color: #ffffff; "
        "text-align: left; "
        "font-weight: bold; "
        "} "
        ".md-table th, .md-table td { "
        "padding: 14px 18px; "
        "border: none; "  # 移除内部边框，更现代
        "border-bottom: 1px solid #e0e0e0; "  # 使用底部边框分割行
        "} "
        ".md-table tbody tr { "
        "background-color: #ffffff; "
        "transition: background-color 0.2s ease; "
        "} "
        ".md-table tbody tr:last-of-type { border-bottom: none; }"  # 最后一行无边框
        ".md-table tbody tr:hover { "
        "background-color: #f0fff0; "  # 悬停时变为非常浅的绿色
        "} "
        # --- 引用块样式 ---
        "blockquote { "
        "border-left: 5px solid #228B22; "  # 边框使用主题色
        "padding: 15px 25px; "
        "margin: 2em 0; "
        "background-color: #f0fff0; "  # 背景使用极浅的薄荷绿
        "color: #555555; "
        "font-style: italic; "
        "border-radius: 0 8px 8px 0; "
        "} "
        "blockquote p { margin: 0; }"
        # --- 水平分割线样式 ---
        "hr { "
        "border: 0; "
        "height: 2px; "
        "background-image: linear-gradient(to right, transparent, #228B22, transparent); "  # 渐变效果
        "} "
        # --- 行内代码样式 ---
        "p code, li code { "
        "background-color: #e8e8e8; "
        "border-radius: 4px; "
        "padding: 3px 6px; "
        'font-family: "Fira Code", "Courier New", monospace; '
        "font-size: 0.9em; "
        "color: #c7254e; "  # 保留了经典的洋红色以示区分
        "} "
        "</style>\n"
    )


# 测试示例
if __name__ == "__main__":
    # 测试代码
    test_markdown = """
### 一、词源与构成分析（核心重点）
“granularity”的词源可追溯至**拉丁语**，其构成遵循“词根+词缀”的典型派生逻辑，逐步从“颗粒”的具体概念延伸至“细节层次”的抽象含义：


#### 1. **核心词根：`gran-`（表示“颗粒/谷物”）**
词根`gran-`来自拉丁语**`granum`**（意为“grain 谷物/颗粒”），是“颗粒”概念的核心载体。拉丁语中，`granum`的指小形式为**`granulum`**（意为“small grain 小颗粒”），进一步强化了“细小颗粒”的语义。


#### 2. **词缀演变路径**
“granularity”是通过**三次词缀添加**从词根`gran-`衍生而来的名词，具体步骤如下：
| 步骤 | 词形 | 词缀 | 作用 | 含义 |
|------|------|------|------|------|
| 1 | **granule** | `-ule`（指小后缀） | 将“granum”（颗粒）缩小为“小颗粒” | 名词，小颗粒（如“a granule of sugar”一粒糖） |
| 2 | **granular** | `-ar`（形容词后缀） | 将“granule”（小颗粒）转化为形容词，描述“有颗粒的状态” | 形容词，颗粒状的；有细节的（如“granular soil”颗粒状土壤） |
| 3 | **granularity** | `-ity`（名词后缀） | 将“granular”（颗粒状的）转化为名词，表“性质/状态” | 名词，颗粒度；细节层次（如“data granularity”数据颗粒度） |


#### 3. **词根`gran-`的衍生词（加深记忆）**
`gran-`是现代英语中**高频词根**，以下是其常见衍生词及语义关联：
- **granite**（名词，花岗岩）：由颗粒状矿物（如石英、长石）组成的岩石，直接关联“颗粒”的具体形态；
- **granary**（名词，谷仓）：储存颗粒状谷物（如小麦、玉米）的建筑，延伸“颗粒”的实用场景；
- **granulate**（动词，使成颗粒）：将物质加工成颗粒状（如“granulate coffee beans”将咖啡豆磨成颗粒），动作化“颗粒”的形成过程；
- **agranular**（形容词，无颗粒的）：通过否定前缀`a-`反转“颗粒”的状态（如“agranular cells”无颗粒细胞，医学术语）；
- **granulocyte**（名词，粒细胞）：医学术语，指细胞质中含有颗粒的白细胞（如中性粒细胞），专业领域的延伸。


### 二、美式发音与重音
- **音标**：/ɡrænjəˈlærəti/（注：`gr`发浊辅音/ɡr/，`a`发短元音/æ/，`n`发鼻音/n/，`jə`是/yjə/的简化（类似“约”的轻读），`lær`发/lær/（重点重音），`ə`是弱读，`ti`发/ti/）；
- **重音位置**：**第三个音节**（即`lær`所在的音节，标注为/ɡrænjəˈlærəti/中的“ˈlær”）。


### 三、语义色彩与情感倾向
- **语义色彩**：**中性**（无固有褒贬，含义取决于上下文）；
- **语境偏好**：
    - **专业/学术**：计算机科学（数据管理，如“data granularity”数据颗粒度）、统计学（分析精度，如“statistical granularity”统计颗粒度）、材料科学（物质结构，如“powder granularity”粉末颗粒度）、地理学（地图细节，如“map granularity”地图颗粒度）；
    - **正式/科技**：常用于技术报告、学术论文、专业文档，**极少用于日常口语**（日常中更可能说“level of detail”细节层次）；
- **语域特征**：
    - **字面用法**：指物质的颗粒大小或结构（如“the granularity of sand”沙子的颗粒度）；
    - **比喻用法**：指信息、分析或系统的**细节层次**（核心抽象义，如“the granularity of a budget”预算的颗粒度——即预算细分到多少具体项目）；
- **“感觉”/个性**：给人**精确、理性、注重细节**的 vibe。例如：
- “high granularity”（高颗粒度）：让人联想到“细致、具体、可追溯”（如“high granularity data”能识别到每个用户的具体行为）；
- “low granularity”（低颗粒度）：让人联想到“笼统、概括、缺乏细节”（如“low granularity report”只能看到整体趋势，无法定位具体问题）。


### 四、词性与常用用法
- **词性**：**不可数名词**（无复数形式，除非特殊语境指“多种颗粒度”，但极少用“granularities”）；
- **常用搭配**：
- **描述级别**：`level of granularity`（颗粒度级别）、`degree of granularity`（颗粒度程度）；
- **描述粗细**：`fine granularity`（细颗粒度/高细节）、`coarse granularity`（粗颗粒度/低细节）、`high granularity`（高颗粒度）、`low granularity`（低颗粒度）；
- **动作关联**：`adjust/change the granularity`（调整/改变颗粒度）、`increase/decrease granularity`（增加/减少颗粒度）、`analyze at a granularity`（以某颗粒度分析）；
- **介词搭配**：`at a... granularity`（以...颗粒度，如“at a daily granularity”以每日颗粒度）、`with... granularity`（具有...颗粒度，如“with fine granularity”具有细颗粒度）、`of... granularity`（...的颗粒度，如“a dataset of high granularity”高颗粒度数据集）。


### 五、地道例句（覆盖不同语境）
1. **计算机科学**：
*“The database allows users to query data at various levels of granularity, from entire databases to individual rows.”*（这个数据库允许用户以不同颗粒度查询数据，从整个数据库到单个行。）
2. **统计学**：
*“To improve the accuracy of our forecast, we need to increase the granularity of our sales data by including hourly transactions.”*（为了提高预测的准确性，我们需要通过包含每小时的交易来增加销售数据的颗粒度。）
3. **材料科学**：
*“The researcher found that the granularity of the metal powder directly affects the durability of the 3D-printed part.”*（研究人员发现，金属粉末的颗粒度直接影响3D打印零件的耐用性。）
4. **地理学**：
*“The new weather map has a higher granularity, showing precipitation levels for each neighborhood instead of just the city.”*（新的天气地图具有更高的颗粒度，显示每个街区的降水量，而不仅仅是城市的。）
5. **商业/管理**：
*“The CEO criticized the budget for its low granularity, saying it didn’t explain how money was being spent on specific projects.”*（CEO批评预算颗粒度低，称其没有解释资金如何用于具体项目。）
6. **学术/研究**：
*“The study’s granularity enabled researchers to detect subtle differences in language use between native and non-native speakers.”*（研究的颗粒度使研究人员能够检测到母语者和非母语者在语言使用上的细微差异。）


总结：“granularity”是一个**专业领域高频词**，其核心逻辑是通过“颗粒”的具体概念隐喻“细节层次”的抽象含义。理解其词根`gran-`的衍生规律（如`granule`→`granular`→`granularity`），能帮助快速记忆相关词汇；掌握其“中性、正式”的语义色彩，能准确应用于专业语境。
""".strip()

    result = markdown_to_html(test_markdown)
    print(result)
