# MusicLaw

`MusicLaw` 是一个本地优先的专辑信息抓取与标签整理工具，专门面向 VOCALOID、UTAU、Synthesizer V、CeVIO，以及其他合成音声软件相关的同人专辑。

它会扫描本地专辑文件夹，搜索 `VocaDB`、`VCPedia`、`dizzylab` 等来源，结合站点规则解析与可选的 OpenAI 兼容 LLM 流程，在人工审核后将元数据写回音频文件。

## 这个发布包包含什么

当前 `release/` 目录已经整理为适合发布的形式：

- 只保留 `src/musiclaw` 下的源码
- 只保留默认配置文件
- 保留空的运行目录：`cache`、`reports`、`snapshots`、`logs`、`temp`
- 不包含测试数据、不包含你的音乐文件、不包含历史报告、不包含缓存 CSV、不包含快照

## 主要功能

- 专门针对 VOCALOID 与合成音声软件同人专辑的信息抓取
- 扫描按 `01.xxx`、`02.xxx`、`03.xxx` 顺序命名的本地音轨
- 搜索 `VocaDB`、`VCPedia`、`dizzylab`
- 支持为单张专辑手动指定优先抓取 URL
- 支持手填原始文本，并将其作为主要证据
- 支持启发式规则与可选 LLM 联合解析
- 坚持“先审核、后写入”的安全流程
- 提供基于 Qt 的桌面 GUI

## 环境要求

- Python `3.11+`
- 当前发布版主要以 Windows 环境为主进行验证
- OpenAI 兼容接口可选，不是必须

## 安装方法

建议先创建虚拟环境：

```bash
python -m venv .venv
. .venv/Scripts/activate
pip install -r requirements.txt
pip install -e .
scrapling install
```

如果不想用 editable install，也可以直接在发布目录安装：

```bash
pip install .
scrapling install
```

## 配置说明

先复制一份配置模板：

```bash
copy config.example.toml config.toml
```

常用配置项：

- `root.music_dir`：你的音乐库根目录
- `sources.enabled`：启用的数据源
- `matching.*`：自动应用 / 进入审核的阈值
- `processing.*`：并发和限速相关设置
- `tags.*`：是否写标签、写封面、重命名文件

### 可选 LLM 环境变量

如果要启用 LLM，请设置：

- `MUSICLAW_LLM_BASE_URL`
- `MUSICLAW_LLM_API_KEY`
- `MUSICLAW_LLM_MODEL`

即使不设置这些变量，启发式流程也仍然可以使用。

## 命令行用法

### 扫描专辑

```bash
musiclaw scan --root D:/Albums --config config.toml
```

### 生成匹配报告

```bash
musiclaw match --root D:/Albums --config config.toml --report reports/latest.json
```

### 在终端查看审核队列

```bash
musiclaw review --report reports/latest.json
```

### 应用审核后的结果

```bash
musiclaw apply --report reports/reviewed.json --config config.toml --output reports/apply.json
```

## GUI 用法

启动桌面界面：

```bash
musiclaw-gui
```

推荐使用流程：

1. 选择音乐根目录。
2. 检查扫描出的专辑列表。
3. 如有需要，修改 `Search album name`。
4. 在 `Priority URLs` 中填入你希望优先抓取的页面。
5. 如果你手头有商品说明、活动文本、群公告、手抄曲目表等，把它们粘贴到 `Manual raw text`。
6. 运行匹配。
7. 查看 evidence、曲目 artist/source、冲突信息。
8. 标记 verified 后再 apply。

## 手填原始文本说明

本工具对“手写/速记/半结构化”文本做了专门优化。下面这些形式都尽量支持：

```text
Title: Album Name
Circle: Circle Name
Album artist: Singer A
Catalog: ABC-123
Event: M3-2026春

全碟演唱: 星尘Infinity
包含曲目:
1. 当美梦浮于夜空
2. 新人类
3. 行星

M1 Song A / Vocal A
Track 2 Song B (Vocal: Singer B)
01) Song C
Tr4 Song D - Singer D

作曲: Composer A
编曲: Arranger B
调校: Tuner C
PV: Visual D
曲绘: Illustrator E
```

只要手填文本里明确写出了信息，系统会把它视为主要证据。

## 目录结构

```text
release/
|- README.md
|- README.en.md
|- README.zh-CN.md
|- pyproject.toml
|- requirements.txt
|- config.example.toml
|- .gitignore
|- src/
|  \- musiclaw/
|     |- __main__.py
|     |- cli.py
|     |- collector.py
|     |- config.py
|     |- gui.py
|     |- matcher.py
|     |- models.py
|     |- pipeline.py
|     |- reporter.py
|     |- scanner.py
|     |- llm/
|     |- sources/
|     |- tagger/
|     \- utils/
|- cache/
|- reports/
|- snapshots/
|- logs/
\- temp/
   \- vocadb_csv/
```

## 运行目录说明

- `cache/`：HTTP、搜索、LLM 等缓存
- `reports/`：生成的 JSON 报告
- `snapshots/`：写入前快照
- `logs/`：预留日志目录
- `temp/vocadb_csv/`：VocaDB 临时 CSV 缓存

## 隐私与安全说明

- 当前发布版已经去除了测试运行产生的持久化内容
- 你自己的报告、快照和临时文件，只会在实际使用后生成
- `match` 阶段不会直接修改音乐文件
- `apply` 只会对已审核并批准的结果执行写入

## 数据源说明

- `VocaDB`：适合专辑、歌曲与 vocalist 相关信息；适配器也支持构建/使用 track CSV 数据
- `VCPedia`：适合中文合成音声作品，以及明确写出轨道演唱信息的页面
- `dizzylab`：适合同人专辑发售页与商店页信息抓取

## 适用范围

这是一个高度定制化工具，并不是面向所有商业音乐库的通用标签器。它主要针对：

- VOCALOID / UTAU / SynthV / CeVIO 等相关作品
- 同人专辑、活动首发专辑、会场发行专辑
- 手写或半结构化元数据整理
- 需要人工审核的本地标签工作流
