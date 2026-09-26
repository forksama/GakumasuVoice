# GakumasuVoice v2 — 以字幕命名、按时长分桶的语音产出

本文记录 v2 布局的规则、两次迁移的实测结果、**已经验证**的部分与**尚未验证**的部分。

## 状态一览

| 组件 | 状态 | 说明 |
| --- | --- | --- |
| `tools/migrate_items_to_v2.py` | ✅ **已运行并验证** | 第一次迁移：旧布局 → 字幕命名，见 `output\_migration_v2_report.json` |
| `tools/split_items_by_duration.py` | ✅ **已运行并验证** | 第二次迁移：按 3–10 秒分 `keep` / `reject`，见 `output\_split_by_duration_report.json` |
| `v2_audio.py` | ✅ 已验证 | WAV 时长/格式读取 + 分桶规则的唯一定义 |
| `v2_naming.py` | ✅ 已验证 | 命名规则的唯一定义 |
| `verify_outputs_v2.py` | ✅ **已运行并验证** | 19 个 run、1764 个 item、**0 problems** |
| `tests/test_gakumasu_voice_v2.py` | ✅ 32 项通过 | 含 1764 个真实文件名的回放 + 全量分桶复核 |
| `gakumasu_voice_v2.py` | ⚠️ **未验证，从未端到端跑过** | 需要 MuMu 运行；纯函数部分已被测试覆盖 |
| `gakumasu_voice.py`（v1） | 🚫 **产出布局已过期** | 逻辑未改动（AST 比对确认）；v2 复用它的解析 / adb / 缓存层 |
| `verify_outputs.py`（v1） | 🚫 **已过期** | 只认 v1 的 item 文件名 |
| `tools/normalize_output_text.py` | 🚫 **已过期** | 发现逻辑建立在 v1 的 `*_01_subtitle.txt` 上 |

---

## 为什么改成 v2

旧布局一条语音落 3 个文件（`subtitle.txt` + `voice.wav` + `metadata.json`），名字里带序号和 voice cue，
目录里分不清哪条是哪句。v2 把**字幕内容直接作为文件名**，并按**时长**把 3–10 秒的挑出来单独放，
目录本身就是可直接使用的语音清单。

## 布局的三代

v1 有两代，v2 是第三代：

| 代 | 位置 | 结构 |
| --- | --- | --- |
| v1-a（最早，如 `output/rinha/`） | run 根就是角色目录 | `items\<voice_cue>\{subtitle.txt, voice.wav, metadata.json}` |
| v1-b（如 `output/temari/<日期>_limit100/`） | 角色目录 / 日期 run | `items\NNNN_<voice_cue>_01_subtitle.txt` / `_02_voice.wav` / `_03_metadata.json` |
| **v2（当前）** | 角色目录 / 日期 run | `items\keep\<字幕>.wav` 与 `items\reject\<字幕>.wav`，各自配同名 `.json` |

区分两代的硬证据：v1-a 的 `*_lines.tsv` 是 11 列且 coverage 在 `reports\coverage.json`，v1-b 是 13 列。

## v2 布局

```text
output\<character_slug>\<日期>_limit<N>\
├── <slug>_lines.tsv      清单（14 列）：index, speaker, text, voice_cue, bank_name,
│                         subsong_index, duration_seconds, bucket, wav_file,
│                         metadata_file, script_path, line_no, voice_line_no, acb_path
├── coverage.json         本次 run 的统计（layout = v2，含 min/max_seconds、keep/reject 计数）
├── unmatched.json        voice cue 在脚本里但 ACB 里找不到时才写
├── items\
│   ├── keep\                   3 ≤ 时长 ≤ 10 秒
│   │   ├── えっ？.wav
│   │   └── えっ？.json         同名 metadata
│   └── reject\                 其余（太短或太长）
│       ├── ……ことね？.wav
│       └── ……ことね？.json
├── acb\                  与 v1 相同：从 _cache\acb 复制，保持 run 自包含
└── scripts\              与 v1 相同：远端脚本落盘
```

**每条语音一个 metadata.json**，与 wav 同 stem，且与 wav 放在同一个桶目录里。
`--limit` 模式下 `keep\` 之外仍会有 `reject\`：扫描到的短句不会被丢掉，只是分到另一个目录。

## 命名规则（`v2_naming.py`）

1. `base = safe_filename(text)`，截断到 `MAX_BASE_CHARS = 120`。
   - `safe_filename` 把 `<>:"/\|?*` 和控制字符换成 `_`，把连续空白（含换行）压成一个 `_`，
     并去掉首尾的 `._ ` 和空格。
2. 按 `base` 分组；组按 `(casefold(base), base)` 排序。
3. 组内按 `voice_cue` 升序排序。
4. 组内第一条用裸 `base`，其余依次 `base-2`、`base-3`……已占用的候选名跳过，
   比较用 `casefold()`（NTFS 不区分大小写）。

**第 3 步是名字可复现的关键**：`--limit` 模式的扫描顺序来自设备上 `find` 的输出，并不保证稳定。
按 `voice_cue` 而不是按"扫到的先后"编号，才能保证你删掉旧目录重新解包时文件名不漂移。

长度上限目前不会触发：考察的 1362 个脚本、17432 条 message 里**最长字幕 53 字符**。
它只是防止未来某条超长台词生成 Windows 拒绝创建的文件名；截断本身若造成重名，由第 4 步兜底。

## 时长分桶（`v2_audio.py`）

- 时长**从解码后的 WAV 头部读取**（data 块字节数 ÷ 字节率），不采信脚本声明值。
  1648 个文件全部是 48 kHz / 16-bit / 单声道，所以能精确算到时长的毫秒位。
- 边界是**闭区间**：`min ≤ 时长 ≤ max` 进 `keep`，其余进 `reject`。默认 3.0 / 10.0，
  可用 `--min-seconds` / `--max-seconds` 调整；调整后重跑切分脚本会**重新分桶**。
- `--limit N` 在解包时用脚本声明的 `voice_duration` 计数（扫描阶段就能判断，保留提前停止的速度优势），
  含义是"预计进 `keep` 的文件数"；实际分桶仍以解码后的实测时长为准。

### 实测：声明值与实测值一致

对全部 1764 个文件比对「脚本声明的 `voice_duration`」和「实测音频时长」的**分桶结论**：

```text
agree = 1764   disagree = 0   missing = 0
|实测 - 声明| 的偏差：p50 = 0.00s   p90 = 0.00s   p99 = 0.00s   max = 0.24s
```

也就是说两边可以互相印证。但权威仍是文件本身——那才是你实际听到的长度。

### 时长分布（1764 个文件）

| 区间 | 数量 | 占比 |
| --- | --- | --- |
| < 1s | 59 | 3.3% |
| 1–3s | 442 | 25.1% |
| 3–5s | 707 | 40.1% |
| 5–10s | 549 | 31.1% |
| > 10s | 7 | 0.4% |

**keep 1256 个（71.2%）／reject 508 个（28.8%）**，中位数 4.10s，最长 10.75s。

## 两次迁移的实测结果

```text
第一次（字幕命名）
  run 目录       16 个（15 个 v1-b + 1 个 v1-a 即 rinha）
  1787 个 wav  ->  1764 个（折叠 23 个逐字节相同的重复，全在 asari_limit200：200 → 177）
  改名映射：output\_migration_v2_report.json

第二次（时长分桶）
  1764 个 item -> keep 1256 + reject 508
  文件移动 1764 次（第一次执行；后续重跑为 0 次移动，只修 metadata）
  详情：output\_split_by_duration_report.json
```

两处特殊边界，可用于核对分桶是否严谨：

- `keep\` 里最短的正是 3.086s（temari）；
- `reject\` 里最长的是 2.993s（temari），刚好卡在 3.0 下方。

### 折叠是什么、为什么必须折叠

`asari_limit200` 里 12 个 voice cue 各被导出多次（11 个 ×3 次 + 1 个 ×2 次 = 35 个文件代表 12 段音频）。
原因是 `sud_vo_adv_pstory_001_cmmn_world-explanation` 这段"世界观说明"语音，同一个 `[voice]` cue 出现在
**3 个不同脚本**里（第 11、15、19、23… 行都一样），而提取器原本"脚本里出现一次就导出一条"。

三次引用的是同一个 `voice_cue` → 同一个 bank → 同一个 `subsong_index`，所以解出来的音频**逐字节相同**。
v2 只保留一份，把另外两次引用记进 metadata：

```json
"also_referenced_by": [
  {"script_path": "/data/.../octo/v1/400/3/5231353433/6e9b...", "line_no": 11, "voice_line_no": 12}
]
```

删除前脚本会**逐个比对 sha256**，只有确实相同才删；不同则报错中止，不会误删音频。
实际删除 **8,384,736 字节 / 23 个文件**，保留 65,581,526 字节 / 177 个文件。

## 已执行的验证

1. **`verify_outputs_v2.py` 全量通过**：19 个 run、1764 个 item（keep 1256 / reject 508）、**problems = 0**。
   逐项检查：
   - wav 是结构合法的 RIFF/WAVE，且 **data 块长度与文件大小一致**（没有截断）
   - **文件确实放在它实测时长所属的桶里**（用 run 自己记录的 `min_seconds`/`max_seconds` 复核）
   - 同名 `.json` 存在，stem 与 wav 完全一致
   - metadata 的 `duration_seconds` 与实测一致（容差 2ms）、`bucket` 与所在目录一致
   - 文件名等于 `safe_filename(text)` 或 `safe_filename(text)-N`
   - `items\` 根下没有散落文件；每个桶内 wav/json 严格一一对应
   - TSV 含 `duration_seconds` 与 `bucket` 两列、不含 `subtitle_file`；
     行数与实际文件数一致，每行的时长和 bucket 都与文件真实位置吻合
2. **无残留**：全库扫描 `*_01_subtitle.txt`、`subtitle.txt`、`*_02_voice.wav`、`*_03_metadata.json`、
   `voice.wav` 均为 0；`items\` 下只有 `keep\` 和 `reject\`。
3. **字节可对账**：第一次迁移中非折叠 run 的残差恰为"去掉 `subtitle_file` 那一行"的 JSON 体积差
   （≈150 字节/项）；`rinha` 残差为负，因为它原来的 metadata 没有 `output_index`/`metadata_file`
   字段而 v2 补上了。两处符号相反正好互相印证，没有无法解释的体积丢失。
4. **命名规则回放**：测试读取全部 1764 个 metadata，用 `v2_naming.assign_file_stems` 从零重算文件名，
   与磁盘上的真实名字逐一比对通过——这同时证明了迁移脚本与新解包器的命名规则完全一致。
5. **分桶全量复核**：测试逐个重新测量 1764 个 wav，确认都在正确的桶里，且数量恰为 1256 / 508。
6. **列定义一致性**：测试断言解包器与切分脚本的 TSV 列定义完全相同，防止两套工具漂移。
7. **v1 基线未破坏**：原有 19 项测试仍全部通过（合计 51 项）。

**回滚映射**：`output\_migration_v2_report.json` 记录了第一次迁移的全部 1764 条 `from → to` 改名。
另外 `output/` 本身是被 git 跟踪的（`output/_cache/` 被 `.gitignore` 排除），所以 git 也是可用的兜底。

## 尚未验证的部分（重要）

`gakumasu_voice_v2.py` **从未实际运行过**——它需要 MuMu 开着并且游戏 octo 缓存可读，本次不具备条件。
以下路径因此未经真机验证：

- `adb connect` / `su 0 grep` / `find` / `cat` / `pull` 一整条远端链路；
- bank 解析、ACB 缓存命中的实际行为；
- `vgmstream` 逐 subsong 解码，以及"解码到临时名 → 测时长 → 移入桶"这一串。

可以确认的是：这些逻辑 v2 **一行都没有改**（远端链路部分），全部直接调用 v1 里已经用过的函数
（`AndroidClient`、`resolve_bank_path`、`ensure_local_acb`、`build_subsong_map` 等）。
v2 新增的只有"解码产物的测量、命名、分桶、写 TSV"这一层，而这一层已被上面第 1、4、5、6 项覆盖。

真机第一次运行时建议加 `--limit 3` 先小跑，再用 `verify_outputs_v2.py --output <该 run>` 校验。

### 一个已实测过的环境风险

把日文文件名交给 `vgmstream-cli` 是 v2 新引入的变量（v1 传给它的路径全是 ASCII）。
实测本机 `vgmstream-cli`（r2117）可以直接写日文名，包括 `♡`（U+2661，GBK/CP936 里不存在）：

```text
rc=0 ok=True  えっ？.wav
rc=0 ok=True  ……………….wav
rc=0 ok=True  （わぁぁ触れた！ かわいい～～♡ いい子だなぁモフモフ～だぁ～～っ♡）.wav
```

即便如此，v2 **仍然先把音频解码到 ASCII 临时名（`items\_tmp_00042.wav`），测完时长再移入桶**，
让 vgmstream 永远不接触非 ASCII 路径。这样输出就不依赖控制台代码页、也不依赖 vgmstream 的构建方式。

## 缓存与加速：**完全不受影响**

两次迁移只改 `items\` 和 `run\<slug>_lines.tsv`。以下全部原样未动，**不需要做任何修改**：

| 缓存 | 键 | 是否引用 item 文件名 |
| --- | --- | --- |
| `output\_cache\bank_paths.json` | `bank_name` → 设备上的 ACB 路径 | 否（实测 0 处） |
| `output\_cache\acb\<bank>.acb` + `.acb.json` | `bank_name` | 否（实测 0 处） |
| `run\acb\`、`run\scripts\`、`coverage.json`、`unmatched.json` | — | 否 |

原因：加速索引是**按 bank（ACB 级）**建立的，而改名和分桶只发生在 item 层，
`voice_cue` / `bank_name` 不变。分桶也不重新解码——只是移动文件。

- 可以随便删：`items\keep\*`、`items\reject\*`、`run\acb\`、`run\scripts\`、整个 run 目录。
- **不要删**：`output\_cache\bank_paths.json`（否则每个 bank 都要重跑最慢的远端 `grep -R`）、
  `output\_cache\acb\`（否则要重新 `adb pull`，全量一个角色约 350 MB）。

## 已知后果

- `coverage.json` 里的 `exported_count` 是 v1 当时的数字（如 asari 是 200），折叠后不再等于
  `items\` 里的实际文件数（177）。**故意没有改写它**，它记录的是 v1 干了什么；
  准确数字在两次迁移的报告里。这也是旧 `verify_outputs.py` 会对折叠过的 run 报错的原因之一。
- TSV 的行顺序仍是**解包（剧情）顺序**，所以同一个桶的行不连续；靠 `bucket` 列区分。
- 字幕以 `。` 结尾时文件名形如 `はい……。.wav`（句号 + 扩展名），这是"字幕即文件名"的必然结果。
- `--limit N` 在 v2 里表示"预计进 `keep` 的 N 个文件"，`reject\` 里还会多出扫描过程中遇到的短句；
  v1 的 `--limit` 则表示 N 条脚本引用。

## 用法

```cmd
:: 第一次迁移：旧布局 -> 字幕命名（已执行；对已是 v2 的目录自动跳过）
python tools\migrate_items_to_v2.py --apply --report output\_migration_v2_report.json

:: 第二次迁移：按时长分桶（已执行；重跑只会修 metadata，不会重复移动）
python tools\split_items_by_duration.py --apply --report output\_split_by_duration_report.json

:: 换区间重新分桶（会把文件在 keep/reject 之间移动）
python tools\split_items_by_duration.py --apply --min-seconds 2 --max-seconds 8

:: 校验
python verify_outputs_v2.py
python verify_outputs_v2.py --output output\temari

:: 新的解包（未验证，需要 MuMu）
python gakumasu_voice_v2.py extract --character kotone --limit 200
python gakumasu_voice_v2.py extract --character kotone --min-seconds 3 --max-seconds 10
python gakumasu_voice_v2.py characters

:: 测试
python -m pytest -q
```
