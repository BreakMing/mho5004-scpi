# 按任务读取

先看[运行准备](setup.md)确定解释器、连接及 `$SkillRoot`、`$WorkRoot`、`$TaskId`。从本次工作项目调用技能脚本；脚本只读技能资源，所有运行产物使用明确传入的技能外绝对路径。只读当前任务对应章节。

## 标准流程与采集状态

1. **识别任务**：确定操作、通道及当前波形／重新采集。信息充分直接执行，关键意图不清且影响结果才问，复用仍适用的确认。
2. **必要检查**：本地 CSV 只检查文件。实机先只读 `*IDN?`、`:TRIGger:STATus?` 和任务相关参数，不重复全量预检。受测软件 `1.611.171`；型号/版本不符则停止依赖该兼容结论的动作。实际接线和探头物理倍率用仍适用的用户确认，设备倍率单独回读。
3. **选择采集**：单独截图保持原状态。已有 STOP 使用当前波形；CSV 导出或同次关联需要固定运行波形时，发送一次 `:MENU:STOP` 并查询确认。仅任务需要时重新采集。CSV 入口不会发送 RUN/STOP，要求 STOP、非分段、目标 CH1/CH2 可见、1 MΩ 和 FTP 可用；不擅自 RUN/AUTO/SINGLE 或改变采集配置绕过前提。
4. **执行留证**：用下文固定入口，保留原始截图/CSV/测量响应、时间、来源、通道、关键设置及命令顺序，唯一名称不覆盖。参数修改前记原值，区分用户要求保留和临时修改。同次截图/读数/CSV需保持冻结并不改相关设置；本地标签不是设备采集计数，时间相近不能独自证明同次。
5. **核验产物**：打开截图；检查CSV来源、完整性、点数、时间轴和单位；检查测量有效性及当时条件。发送或回读成功不独自证明实际效果、全部点数或精度。
6. **核对末态**：用户结束要求优先，否则恢复入口采集意图和临时参数，保留用户要求的调整。RUN用`:MENU:RUN`并查询，可得到RUN/WAIT，不为复刻字符串修改触发。未指定末态且入口STOP，或明确要求STOP时保持STOP。报告实际确认状态、恢复失败/无法核对项，不用旧日志补当前状态。

## 截图

固定入口：

```powershell
python -B "$SkillRoot/scripts/capture_scope.py" --host "$InstrumentHost" --output-root "$WorkRoot/output/oscilloscope_screenshots"
```

`$InstrumentHost` 为本次确认的地址，见[运行准备](setup.md)；需要覆盖 FTP 端口时在同一次调用添加 `--ftp-port 3721`，不要因此再截一次。入口检查身份、发一次 `:STORage:CAPTure:STARt`、按设备截图目录差异寻找唯一完成PNG、FTP只读取回并检查PNG。不会修改RUN/STOP。设备目录为 `/Pictures/Screenshots`；完整图片、来源元数据、哈希和证据在传入归档目录的 `<北京时间戳>/` 下，浏览入口为该归档根目录的 `index.html`／`index.csv`。

截图脚本内置身份检查只核对厂商/型号前缀；软件版本适用性由任务层按上文必要检查确认，不能把脚本自身的身份通过当作版本核验。

打开本次原始PNG，检查通道、坐标、菜单遮挡和用户要求的内容，再展示实际截图及绝对路径。随包验证的预期图片为1920×1200 RGB PNG；内容可判读需另查。CSV绘图与设备截图分别命名。

失败先查看本次输出目录中的 `failure.json`、`evidence/`、`events.jsonl` 及设备新增文件；截图动作已发出或不明时不直接重跑入口。图片取得与索引更新分别判断，索引失败不再截一次。仅重建本地索引用：

```powershell
python -B "$SkillRoot/scripts/capture_scope.py" --reindex --output-root "$WorkRoot/output/oscilloscope_screenshots"
```

此模式不连接仪器。`--output-root` 必须显式指定技能外的截图归档根目录；索引只包含该目录下哈希和PNG校验通过的记录。页面布局取自包内[截图模板](assets.md)，不修改原始图片。随包历史预览只读；试建索引应先复制历史记录到项目中的测试目录。

## CSV 保存、续取及分析

```powershell
python -B "$SkillRoot/scripts/scope_csv.py" save --host "$InstrumentHost" --source CH1 --output "$WorkRoot/output/oscilloscope_csv/$TaskId"
```

已有 CSV 只做本地分析，用新的 `$TaskId` 和独立输出目录：

```powershell
python -B "$SkillRoot/scripts/scope_csv.py" analyze "已有CSV绝对路径" --source CH1 --start-s=-0.000001 --end-s=0.000001 --output "$WorkRoot/output/oscilloscope_csv/$TaskId"
```

省略时间边界会分析文件全部点；示例中的保存与本地分析是不同任务入口，按需求选择，不为分析再次调用保存。

`save` 默认CH1，接受CH2、`--host`、`--ftp-port`；CH2完整导出未实机验证。保存入口本轮重组后未实机运行，不能把历史底层路径成功等同于新入口通过。当前任务确需时按授权单次调用并核验，不先重跑整套测试。

- 先FTP只读记录 `/smart/csvwave`、`/smart/binwave`、`/smart/refwave` 的保存前目录，再核对设备身份和前提；设置本地／CSV／单路／非全部分段／唯一字母数字名称。持久化保存意图后，只发送一次`:STORage:SAVE:START`。
- 默认观察60秒，`--timeout`可选20～300秒，通信另有超时。候选文件须符合唯一名称/目录变化/保存时间，再待尺寸稳定、完整下载并核内容。设置回读、BIN/MWAV提示或暂时不变尺寸都不独自证明成功。
- 设备 `/Internal shared storage/smart/csvwave` 在FTP根下对应 `/smart/csvwave`。实际新增完整路径记入`export.json`；设备可能同时生成BIN/MWAV，记录而不解码，不凭同名推断CSV已经生成。
- 文件核验包含FTP完成响应、下载前后SIZE、本地尺寸/哈希、CSV型号/序列号及来源、单位、声明/实际行数、末行、有限值、时间递增及采样间隔。64 MiB为当前入口单文件上限，不等于示波器能力上限。
- `export.json`分别记录`file_verified`、`storage_restored`、`observed_state_unchanged`和错误。退出非零不等于无有效CSV。入口尽力恢复五项存储参数，不改变采集状态；任务层仍需核对最终采集意图，设备新增文件保留。
- 结果不明先读本次输出目录中的`failure.json`、`export.json`、`save-intent.json`、`events.jsonl`及`evidence/`。不要再次运行`save`。若文件已生成且归属可靠，仅列目录/SIZE/RETR续取；当前CLI没有自动续取命令。续取另建目录，保留部分文件和前次证据，不覆盖或删除旧文件。不可靠即停止该项。

`analyze`纯本地，不导入通信入口或检查现场。它保留原始字节副本，验证后按文件时间列包含两端取窗口，不平移零点、不插值补点、不重复乘探头倍率或加偏置。工具取请求区间与文件区间的交集，成功退出不代表覆盖完整请求；查看`summary.json`的实际首末时间、样点索引和点数，说明部分覆盖或端点落在样点间隙。

打开`waveform.png`检查轴、单位、来源；图画出全部所选样点，但显示像素有限。`summary.md` 提供原文件和窗口的可读摘要，`summary.json` 保留结构化数值、产物哈希及所用模板哈希。保留`original.csv`、派生两列`window.csv`、图片和摘要。绘图和摘要外观使用包内[产物模板](assets.md)。文件完整不证明全部采集内存、无抽取或绝对精度。

两种动作均必须传 `--output`，只接受技能外尚不存在的绝对目录。缺失路径、相对路径或指向技能的路径在创建目录及仪器连接前拒绝。失败的`original.csv`可能是部分文件，不能仅凭名字当成有效原件。

## 测量与参数操作

尚无通用测量CLI。按[命令索引](command-index.md)定位精确形式，按[厂商资料说明](../THIRD_PARTY_NOTICES.md)取得 SCPI／用户手册，核对语法、操作条件及[能力限制](capabilities.md)。不从设置命令任意加`?`推导查询，不把索引中的历史响应当成实际功能通过，也不执行历史批次。

| 任务 | 常用精确形式 | 判定边界 |
|---|---|---|
| 身份/状态 | `*IDN?`、`:TRIGger:STATus?` | 本次响应，不证明新采集 |
| 时基/采样 | `:TIMebase:EXTent?`、`:TIMebase:POSition?`、`:ACQuire:SRATe?` | 参数回读，不当作完整记录点数 |
| CH1/CH2 | `:CHANnel1:DISPlay?`、`:CHANnel1:PROBe?`、`:CHANnel1:INPutres?`、`:CHANnel1:SCALe?`、`:CHANnel1:POSition?`、`:CHANnel1:COUPle?`；CH2替换通道号 | 物理倍率和精度需另有依据 |
| 频率/周期 | `:MEASure:OPEN FREQ,CH1`、`:MEASure:FREQ? CH1`、`:MEASure:CLOSe FREQ,CH1`；周期用`PERiod` | 查询先满足测量打开条件，不证明绝对准确度 |

测量打开/查询/关闭见SCPI PDF第61～64页。先确认项目是否已打开，已打开只查询，不关闭用户原项目；新增时记录并仅关闭本次新增项。无法确认显示原状时报告限制，不凭查询成功推断原先不存在。不要为了读数偷偷RUN。

先保存原始响应再解释单位。空值、`NONE`、错误文本、非有限数不能当0；频率计`1.00000 kHz`按单位换算；历史占空比0～1转百分数须标明所用语义。未知枚举或单位不猜转换。

Runtime会建立独立通信证据，但不会替调用者恢复普通参数。下面仅为任务会话组织示例，不应为本地分析运行它：

```python
from pathlib import Path
from uuid import uuid4
import sys

# 将调用代码保存在当前工作项目，以 python -B 运行；填写本次绝对路径。
skill_root = Path('技能文件夹绝对路径')
work_root = Path('本次工作项目绝对路径')
assert (skill_root / 'SKILL.md').is_file()
sys.path.insert(0, str(skill_root / 'scripts'))
from scope_runtime import Runtime, dump
from scope_paths import external_output

out = external_output(work_root / 'output' / 'scope_tasks' / uuid4().hex)
out.mkdir(parents=True, exist_ok=False)
runtime = Runtime(out, host='本次确认的设备地址')
with runtime.session() as session:
    identity = runtime.identity(session)
    state = runtime.state(session, 'entry')
    dump(out / 'entry.json', {'identity': identity, 'state': state})
# 按任务添加精确查询/设置，并留证、核验和恢复；本例仅查询。
if runtime.cleanup_errors:
    raise RuntimeError(f'通信清理异常，证据：{out}')
```

`Runtime.q(session,label,command)`核验文本响应；`session.write_command(label,command)`只说明写入，调用者检查返回状态并回查/核实际效果。`Runtime.state`当前收集两路常用条件；只需少数参数的任务可用`q`取得最小所需项，勿把全状态收集变成强制全量预检。`Runtime`使用显式 `host`，`ftp_port` 可选，其余协议默认值来自包内配置；构造 Runtime 本身不连接仪器。

## 异常处理

1. 先查响应、日志和产物。明确原因可修正才有限重试；同条件已有明确结果不重复。截图或保存结果不明先查文件及证据，不盲目重发，仍不明暂停该项。
2. 暂停失败依赖步骤；设备状态明确、条件满足时继续已授权独立任务，记录完成/阻塞及原因。
3. 仅关键资料缺失或必须现场操作才请求用户协助；一次说明原因、简短步骤和反馈要求。等待期间暂停依赖项，独立项继续。FTP现场开启步骤见[运行准备](setup.md)。
4. 已有检查无法解决且无新明确方向，或下一步超范围时停止，保留现象、证据、方法、疑点和继续条件，标部分完成或未解决。

## 结果交付

- 先展示用户所需结果及实际文件绝对路径；保留原始和派生关系。截图展示已检查原图，CSV展示波形图并给原件/分析文件，测量列数值/单位/必要条件。简单任务简短回复，复杂分析再出报告。
- 明确已完成、部分完成、未完成，区分实际效果与仅回读、观察与计算和假设；先给当前结论，历史尝试留证，列影响使用的限制。
- 操作过仪器才说明实际确认的RUN/STOP、保留设置/恢复异常，以及设备新增文件和本地位置。纯本地分析只交代本地结果，不报告历史仪器末态为当前状态。
