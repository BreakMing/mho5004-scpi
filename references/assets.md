# 三项产物模板

这三项资源由包内脚本自动读取，用来统一输出的展示方式。修改模板不改变 CSV 原始数据、选点范围或仪器操作；已生成的文件保留原样，下次运行才使用新样式。

## 生成结果保存到哪里

运行时技能目录只读。Agent 选择当前工作项目或用户指定的输出位置，并传给脚本；变量定义见[运行准备](setup.md)。

| 产物 | 本次工作项目中的位置 |
|---|---|
| CSV 原件、窗口数据、波形图和摘要表 | `$WorkRoot/output/oscilloscope_csv/$TaskId/`；分别为 `original.csv`、`window.csv`、`waveform.png`、`summary.md` 和 `summary.json` |
| 每次原始截图及记录 | `$WorkRoot/output/oscilloscope_screenshots/<北京时间戳>/` |
| 截图浏览页和 CSV 索引 | `$WorkRoot/output/oscilloscope_screenshots/index.html` 和 `index.csv` |

CSV 任务使用 `--output`，截图及重建浏览页使用 `--output-root`，均必须是技能外的绝对路径。上述 `output` 属于本次工作项目，技能文件夹中不创建运行输出。`assets/previews/` 是随包的固定历史示例，正常使用只查看；试建历史截图页时先复制记录到项目目录。

| 资源 | 控制什么 | 使用它的入口 |
|---|---|---|
| [波形图样式](../assets/waveform-style.json) | 图幅、绘图区、颜色、字体、线宽、刻度和标题文字；全图和局部图共用 | `scope_csv.py analyze`，以及成功保存 CSV 后的分析 |
| [数据摘要表](../assets/data-summary.md.tpl) | `summary.md` 的表格、说明和产物链接 | 同上；点数、时间、电压、索引及哈希由真实输入填入 |
| [截图浏览页](../assets/screenshot-gallery.html.tpl) | 截图归档 `index.html` 的布局和配色 | `capture_scope.py` 完成截图后，或本地 `--reindex` |

## 查看效果

以下均为历史资料生成的预览，未操作当前仪器：

- [CSV 全图](../assets/previews/waveform-full.png)：历史 CH1 文件的全部 180000 点。
- [CSV 局部图](../assets/previews/csv-window/waveform.png)：原时间轴 −1～+1 µs，共 3001 点。
- [数据摘要表](../assets/previews/csv-window/summary.md)：原文件与所选窗口分列，并链接数据和图片。
- [截图浏览页](../assets/previews/screenshot-gallery/index.html)：一张历史原始截图，可打开原图及脱敏采集记录。

来源、脱敏说明和哈希见[预览来源](../assets/previews/provenance.json)。CSV 仅替换文件头序列号，全部时间/电压样点保留；截图像素及原字节保留，身份记录中的序列号替换为 `REDACTED`。预览为包内相对链接，整体搬移后仍可查看；没有附带旧工作区的网络日志。截图记录中的 `historical_example: true` 会在卡片上标明历史示例，重新建立索引后仍保留提示。后续任务的原件保留规则不变，公开前另查真实证据中的地址和设备信息。

## 修改时注意

波形样式使用 JSON。`canvas` 中的尺寸和坐标需一起调整；字体不可用时脚本使用 Pillow 后备字体。`ticks` 仅控制显示，不会改变样点；标题中的 `{source}`、时间标签的 `{unit}`、页脚的 `{count:,}` 是脚本填入的字段。

摘要表和截图页使用 Python `string.Template`。保留现有 `${字段名}`；普通美元符号写作 `$$`。摘要表的各字段见模板本身，`summary.json` 继续提供完整数值和哈希；截图页的 `${total}`、`${empty_notice}`、`${cards}` 由索引逻辑生成，图片路径和文字仍由脚本转义。

截图模板只负责页面外观，原始 PNG 不加水印、不改色。CSV 图仍直接采用文件时间和电压，不平移零点或重复施加倍率。图中展示全部选取点，像素分辨率不等于逐点可辨识程度；文件完整也不证明覆盖全部采集内存或绝对精度。
