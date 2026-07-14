# Gakumasu ADV Voice Extraction Agent Guide

这份文档是给下一次接手的 agent 用的。目标是把当前已经跑通的燐羽抽取链路抽象成可复用流程，用来为其他角色编写或改造抽取脚本。

当前项目位置：

```text
C:\Repositories\GakumasuVoice
```

运行环境默认是 Windows Command Prompt，不要给用户 PowerShell 指令。

## 目标产物

为指定角色导出一组一一对应的字幕和语音文件：

```text
output\<character_slug>\<character_slug>_lines.tsv
output\<character_slug>\items\<voice_cue>\subtitle.txt
output\<character_slug>\items\<voice_cue>\voice.wav
output\<character_slug>\items\<voice_cue>\metadata.json
output\<character_slug>\reports\coverage.json
output\<character_slug>\reports\unmatched.json   # 仅有未匹配项时生成
output\<character_slug>\scripts\                 # 拉取到本地的 ADV 脚本文本
output\<character_slug>\acb\                     # 拉取到本地的 ACB bank
```

每个 `items\<voice_cue>` 目录代表一个独立的字幕/subsong 对。`voice.wav` 必须是该 `voice_cue` 对应的 vgmstream subsong，不能靠文件排序猜。

## 基础环境

默认 Android 环境：

```text
MuMu Player 12:
C:\Program Files\NetEase\MuMu Player 12

ADB:
C:\Program Files\NetEase\MuMu Player 12\nx_main\adb.exe

Default device:
127.0.0.1:16384

Game package:
com.bandainamcoent.idolmaster_gakuen

Octo cache root:
/data/data/com.bandainamcoent.idolmaster_gakuen/files/octo/v1/400
```

必须能用 root 读取 app 私有目录。脚本里应通过：

```text
adb -s 127.0.0.1:16384 shell su 0 ...
```

或者等价的 `su 0` 调用读取 `/data/data/...`。

音频解码依赖 `vgmstream-cli.exe`。优先查：

```text
C:\Repositories\GakumasuVoice\vgmstream-cli.exe
PATH 中的 vgmstream-cli.exe
```

用户机器上曾经可用的位置：

```text
C:\Softwares\vgmstream-cli\vgmstream-cli.exe
```

## 核心原则

不要把 `name=<角色名>` 当成角色语音的唯一依据。

原因：

- 有些脚本会显示角色名，但实际 voice cue 是其他角色。
- 有些角色台词可能显示为 `？？？`、旁白或别的名字，但 voice cue 后缀仍然属于目标角色。
- ACB 里的 subsong 顺序可能按角色或内部 cue 分组，不一定等于剧情时间顺序。

权威依据应是 voice cue 的角色代码后缀：

```text
voice=sud_vo_adv_..._<character_code>-001
voice=sud_vo_adv_..._<character_code>-002
```

燐羽当前代码是：

```text
character_code = krnh
character_name = 燐羽
full_name = 賀陽燐羽
```

对其他角色，必须先确定其 `character_code`，然后以 `_<character_code>-数字` 的 voice cue 作为主过滤条件。

## 角色代码发现

如果已知角色代码，直接跳到“脚本搜索”。如果未知，先做发现。

建议搜索候选脚本根目录：

```text
/data/data/com.bandainamcoent.idolmaster_gakuen/files/octo/v1/400/3
/data/data/com.bandainamcoent.idolmaster_gakuen/files/octo/v1/400/4
/data/data/com.bandainamcoent.idolmaster_gakuen/files/octo/v1/400/6
/data/data/com.bandainamcoent.idolmaster_gakuen/files/octo/v1/400/7
/data/data/com.bandainamcoent.idolmaster_gakuen/files/octo/v1/400/9
```

这些 bucket 当前被当作 ADV 文本脚本候选。为了避免大二进制文件拖慢搜索，搜索时默认只扫小文件：

```text
-type f -size -500k
```

发现流程：

1. 搜 `name=<角色名>`，找包含显示名的脚本。
2. 拉取少量命中脚本，查看附近 `[voice ...]` 行。
3. 从 voice cue 中提取后缀，如 `_krnh-001`，其中 `krnh` 就是角色代码。
4. 同时查找 `img_adv_speaker_<code>`，它常作为 speaker 头像线索。
5. 再用 `<code>-` 反查所有包含该角色 voice cue 的脚本。

Windows Command Prompt 示例：

```cmd
set "ADB=C:\Program Files\NetEase\MuMu Player 12\nx_main\adb.exe"
set "DEVICE=127.0.0.1:16384"
set "ROOT=/data/data/com.bandainamcoent.idolmaster_gakuen/files/octo/v1/400"

"%ADB%" connect %DEVICE%
"%ADB%" -s %DEVICE% shell "su 0 find %ROOT%/3 %ROOT%/4 %ROOT%/6 %ROOT%/7 %ROOT%/9 -type f -size -500k -exec grep -a -l 'name=角色名' {} \;"
```

如果 `cmd.exe` 显示日文乱码，不代表文件坏了。脚本和输出应使用 UTF-8 写入；验证时可以输出 unicode escape 或直接查看文件。

## 脚本搜索

对一个角色，至少做四类搜索，并把结果取并集：

```text
<character_code>-
name=<character_name>
img_adv_speaker_<character_code>
<full_name>
```

其中 `<character_code>-` 是抽取语音的主依据；其他三类用于 coverage 和发现漏网脚本。

当前实现对应函数：

```text
AndroidClient.grep_text_files(...)
extract_rinha(...)
```

注意：

- `grep_text_files` 应接受多个 script root。
- script root 可以用逗号或分号分隔。
- 每个 root 内用 `find ... -size -500k -exec grep -a -l ...` 搜索。
- 拉取脚本文本时可直接 `su 0 cat <remote_path>`。
- 本地保存脚本到 `output\<character_slug>\scripts\`，文件名使用安全化后的远端 bucket/hash。

## ADV 脚本解析

需要解析两种行：

```text
[message text=... name=... clip={...}]
[voice voice=... clip={...}]
```

字段规则：

- `message.text` 是字幕文本。
- `message.name` 是显示名，不一定等于真实说话角色。
- `voice.voice` 是 CRI cue 名，例如 `sud_vo_adv_dear_hmsz_020_krnh-001`。
- `clip` 里可能包含 `_startTime` 和 `_duration`，用于把 voice 和 message 对齐。

不要用简单的 `split(" ")` 解析属性。`text=` 里可能有转义或特殊字符。当前实现用“当前 key 到下一批已知 key 之前”的方式抽属性。

需要处理的转义：

```text
\n  -> 换行
\=  -> =
\{  -> {
\}  -> }
```

当前实现对应函数：

```text
extract_attr(...)
decode_adv_text(...)
parse_script_events(...)
```

## 角色 voice line 收集

从 voice cue 提取 bank name：

```text
voice_cue:
sud_vo_adv_dear_hmsz_020_krnh-001

character_code:
krnh

bank_name:
sud_vo_adv_dear_hmsz_020
```

通用正则：

```text
^(.+)_<character_code>-\d+$
```

只有匹配这个正则的 voice cue 才算目标角色语音。

当前实现对应函数：

```text
character_bank_name(...)
collect_character_voice_lines(...)
```

## 字幕与 voice cue 对齐

不要假设 `[voice]` 行一定紧挨着对应 `[message]` 行。

推荐策略：

1. 如果 voice 有 `_startTime`，在所有 message 中找时间窗口覆盖 voice start 的候选。
2. message 结束时间可用 `message.start + message.duration + 0.75`，开始前可容忍约 `0.25` 秒。
3. 多个候选时选 start time 最接近的。
4. 如果没有时间信息或找不到时间候选，退回到 voice 行之前最近的 message。
5. 再不行才用脚本内第一个 message。

当前实现对应函数：

```text
find_matching_message(...)
```

这个逻辑是为了处理脚本里夹着 layout、camera、fade 等行的情况。

## ACB bank 定位

Android octo 缓存里的 ACB 通常没有 `.acb` 后缀，只是 hash 文件名。不能通过文件名找。

定位流程：

1. 对每个 `bank_name`，先查可选的旧 voice map。
2. 如果旧 map 给了路径，必须先用 `su 0 test -f <path>` 确认远端文件仍存在。
3. 如果旧路径不存在或没提供 map，在整个 octo root 内二进制 grep：

```text
grep -R -a -l <bank_name> /data/data/.../files/octo/v1/400
```

4. 对每个候选远端文件：
   - 用 `su 0 cp <remote> /sdcard/Download/<tmp>`。
   - `chmod 644`。
   - `adb pull` 到本地临时 `.acb`。
   - 跑 `vgmstream-cli -m <local_probe.acb>`。
   - 如果 metadata 里包含目标 bank 或目标 cue，则接受这个远端路径。

不要信任陈旧 map。之前出现过旧 map 指向已经不存在或不匹配的资源，必须做远端存在性检查。

当前实现对应函数：

```text
read_existing_voice_map(...)
AndroidClient.remote_file_exists(...)
AndroidClient.grep_files(...)
AndroidClient.pull_private_file(...)
resolve_bank_path(...)
```

## subsong 映射

一个 ACB bank 内通常有多个 subsong。必须用 vgmstream metadata 建立：

```text
stream name -> subsong index
```

流程：

1. 跑：

```cmd
vgmstream-cli.exe -m bank.acb
```

2. 解析：

```text
stream count: N
```

3. 对 `1..N` 每个 subsong 跑：

```cmd
vgmstream-cli.exe -m -s 1 bank.acb
vgmstream-cli.exe -m -s 2 bank.acb
...
```

4. 解析：

```text
stream name: sud_vo_adv_..._<character_code>-001
```

5. 建立字典：

```text
subsong_map[stream_name] = index
```

导出 WAV 时必须根据 exact `voice_cue` 查这个字典：

```cmd
vgmstream-cli.exe -s <subsong_index> -o output.wav bank.acb
```

当前实现对应函数：

```text
parse_stream_count(...)
parse_stream_name(...)
vgmstream_metadata(...)
build_subsong_map(...)
write_voice_line_outputs(...)
```

关键坑点：不要用 `_001.wav`、`_002.wav` 或 subsong 顺序猜角色。之前已经确认过，某些 bank 的第一个 subsong 可能是别的角色，比如 Saki，而不是目标角色。

## 输出 metadata

每个 item 的 `metadata.json` 至少应包含：

```json
{
  "script_path": "...",
  "line_no": 123,
  "voice_line_no": 124,
  "text": "...",
  "speaker": "...",
  "voice_cue": "sud_vo_adv_...",
  "bank_name": "sud_vo_adv_...",
  "start": 1.23,
  "duration": 2.34,
  "voice_start": 1.24,
  "voice_duration": 2.20,
  "subsong_index": 17,
  "acb_file": "...",
  "script_file": "...",
  "wav_file": "...",
  "subtitle_file": "..."
}
```

汇总 TSV 建议列：

```text
index
speaker
text
voice_cue
bank_name
subsong_index
wav_file
script_path
line_no
voice_line_no
acb_path
```

TSV 里的换行应转成字面量 `\n`，否则一条台词会拆成多行。

## 覆盖率报告

`coverage.json` 应记录：

```text
script_roots
script_max_kb
octo_root
character_code
character_name
full_name
scripts_with_character_voice
scripts_with_character_name
scripts_with_character_speaker_image
scripts_with_full_name_text
voice_line_count
exported_count
unmatched_count
bank_count
bank_remote_paths
```

判断是否完成：

- `voice_line_count > 0`
- `exported_count == voice_line_count`
- `unmatched_count == 0`
- 每个 item 都有非空 `voice.wav`
- 每个 item 都有 `subtitle.txt`
- 每个 item 都有 `metadata.json`
- 抽样检查 metadata 中 `voice_cue` 与 vgmstream 的 `stream name` 一致

如果 `unmatched_count > 0`，不要假装完成。通常原因是：

- ACB bank 定位错了。
- vgmstream 读到的 stream name 与 script voice cue 不一致。
- 该角色资源未完整下载。
- 搜索根目录遗漏了脚本或资源。

## 当前代码如何复用到其他角色

当前入口仍叫：

```cmd
python gakumasu_voice.py extract-rinha
```

但参数已经可以换角色：

```cmd
cd /d C:\Repositories\GakumasuVoice
python gakumasu_voice.py extract-rinha --character-code <code> --character-name <display_name> --full-name <full_name> --output output\<character_slug>
```

如果为其他角色正式改代码，建议把命名泛化：

```text
extract-rinha        -> extract-character
extract_rinha(...)   -> extract_character(...)
DEFAULT_CHARACTER_*  -> 只作为默认示例，不写死业务逻辑
rinha_lines.tsv      -> <character_slug>_lines.tsv 或 lines.tsv
extract_rinha_assets.bat -> extract_character_assets.bat
```

同时保留原有测试，并新增角色无关测试。

## 建议测试

至少覆盖这些行为：

1. display name 不是目标角色，但 voice cue 后缀是目标角色代码时，应抽取。
2. display name 是目标角色，但 voice cue 是其他角色时，应忽略。
3. voice 和 message 中间隔着 layout/camera/fade 行时，应按 clip 时间匹配。
4. 能解析 vgmstream 的 `stream count` 和 `stream name`。
5. 输出目录名使用 `voice_cue`，保证一个 cue 一个独立 item。
6. 旧 voice map 路径失效时，应忽略旧路径并重新 grep ACB。
7. 多个 script roots 用逗号/分号传入时都能搜索。
8. 输出验证能发现缺失或空的 wav/subtitle/metadata。

当前测试文件：

```text
C:\Repositories\GakumasuVoice\tests\test_gakumasu_voice.py
```

运行：

```cmd
cd /d C:\Repositories\GakumasuVoice
python -m pytest
```

## 验证脚本

当前仓库有：

```text
C:\Repositories\GakumasuVoice\verify_outputs.py
```

燐羽当前验证：

```cmd
cd /d C:\Repositories\GakumasuVoice
python verify_outputs.py
```

为其他角色改造时，验证脚本也应参数化：

```cmd
python verify_outputs.py --output output\<character_slug>
```

或新增对应验证脚本。验证输出至少要打印：

```text
rows=<tsv rows>
missing=0
voice_line_count=<count>
exported_count=<count>
unmatched_count=0
bank_count=<count>
```

普通 `cmd.exe` 里日文可能乱码，可以额外打印：

```text
sample_text_escape=\u....
```

用来证明 UTF-8 内容实际正确。

## 给下次 agent 的任务模板

可以这样要求下一个 agent：

```text
请阅读 C:\Repositories\GakumasuVoice\AGENT_EXTRACTION_GUIDE.md，
基于当前 GakumasuVoice 项目为 <角色名> 写一个抽取器。

要求：
- 使用 Windows Command Prompt 指令，不要使用 PowerShell。
- 如果未知 character_code，先从 ADV 脚本中发现。
- 以 voice cue 后缀 _<character_code>-NNN 作为权威角色依据，不要只靠 name=<角色名>。
- 导出每条台词的 voice.wav、subtitle.txt、metadata.json。
- 用 vgmstream stream name 精确匹配 subsong，不要按 subsong 顺序猜。
- 生成 coverage.json 和 lines.tsv。
- 跑 pytest 和输出验证，确认 unmatched_count=0、missing=0。
```

需要提供给 agent 的角色信息越多越好：

```text
character_name = 显示名
full_name = 全名，可为空
character_code = 已知则填；未知则让 agent 发现
output slug = 英文或罗马字目录名
```

## 当前燐羽结果作为参考

当前本地最终结果曾验证为：

```text
rows=286
missing=0
voice_line_count=286
exported_count=286
unmatched_count=0
bank_count=19
```

这说明全链路已经跑通：脚本发现、字幕解析、ACB 定位、subsong 映射、WAV 导出、逐条 metadata 输出都已完成。
