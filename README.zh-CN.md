# Thumbwork

[English](README.md) | 简体中文

Thumbwork 是一个通过命令行工具（CLI）使用的手机操作智能体。它观察手机截图，通过点击、滑动等动作完成任务，并在无法继续时请求人工协助。你的编程智能体负责安装配置、准备任务、安排运行以及分析结果。

## 从安装技能开始

在一台新电脑上，**只需先安装 [thumbwork-phone-use 技能](skills/thumbwork-phone-use)**。同一个技能文件夹可用于下表列出的本地技能格式，无需提前安装 Thumbwork CLI、Python、Git、Node.js 或 ADB。你需要先有一个已安装的编程智能体，并确保它能在即将连接手机的电脑上执行本地命令。

### 安装到你的编程智能体

1. 在 [GitHub 仓库](https://github.com/M4rque2/Thumbwork)中选择 **Code → Download ZIP** 下载，然后解压。
2. 将完整的 `skills/thumbwork-phone-use` 文件夹（包括 `references/`）复制到下表中对应智能体的**一个**安装位置。如果父文件夹不存在，先创建它。
3. 在智能体中开启新会话，然后调用技能。开始安装配置前，确认智能体能读取 `SKILL.md` 及其引用的配置指南。

`~` 表示你的用户主目录，例如 `/Users/you`、`/home/you` 或 `C:\Users\you`。以 `./` 开头的路径位于你在智能体中打开的项目内。

| 智能体 | 用户级安装位置（所有项目可用） | 项目级安装位置（仅当前项目可用） | 安装后如何调用 |
| --- | --- | --- | --- |
| [Claude Code](https://code.claude.com/docs/en/skills) | `~/.claude/skills/thumbwork-phone-use/` | `./.claude/skills/thumbwork-phone-use/` | `/thumbwork-phone-use` |
| [Codex](https://learn.chatgpt.com/docs/build-skills) | `~/.agents/skills/thumbwork-phone-use/` | `./.agents/skills/thumbwork-phone-use/` | 在 CLI/IDE 中输入 `$thumbwork-phone-use`，或在应用内选择技能 |
| [Pi](https://pi.dev/docs/latest/skills) | `~/.pi/agent/skills/thumbwork-phone-use/` | `./.pi/skills/thumbwork-phone-use/` | `/skill:thumbwork-phone-use` |
| [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/skill/skill-filesystem/README.md) | `~/.dsh/skills/thumbwork-phone-use/` | `./.dsh/skills/thumbwork-phone-use/` | 告诉它使用 `thumbwork-phone-use` 技能 |
| [WorkBuddy，本地项目模式](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Project) | `~/.codebuddy/skills/thumbwork-phone-use/` | `./.codebuddy/skills/thumbwork-phone-use/` | 告诉它使用 `thumbwork-phone-use` 技能 |

这些路径依据各智能体文档中的技能发现规则整理，并不代表已经在每个智能体中完成真机配置测试。DeepSeek Harness 需要启用文件系统技能提供器和技能工具，自定义或精简预设可能未包含它们。WorkBuddy 的本地配置兼容 [CodeBuddy 技能目录](https://www.codebuddy.ai/docs/cli/skills)；这里介绍的是本地文件夹安装方式，技能尚未因此上架其市场。

目前 Codex、Pi 和 DeepSeek Harness 的文档也支持共享位置 `~/.agents/skills/`。如果你在同一台电脑上使用其中多个智能体，在这里放一份技能即可供三者使用。不要同时在共享目录和智能体专属目录中重复安装同名技能。如果你自定义过技能主目录，请以实际配置为准。

安装后的目录结构应为：

```text
<chosen-skills-directory>/thumbwork-phone-use/
├── SKILL.md
└── references/
    ├── setup.md
    └── task-iteration.md
```

其中 `<chosen-skills-directory>` 是你选择的技能目录。请复制完整技能文件夹，不要只复制 `SKILL.md`，也不要将整个仓库嵌套到目标位置。如果需要替换已有技能，先将旧文件夹备份到技能搜索目录之外。

例如，在 macOS/Linux 上，进入解压后的仓库根目录，为 Claude Code 安装技能：

```sh
mkdir -p "$HOME/.claude/skills"
test ! -e "$HOME/.claude/skills/thumbwork-phone-use" && cp -R skills/thumbwork-phone-use "$HOME/.claude/skills/"
```

为 Codex 安装时，将 `.claude/skills` 替换为 `.agents/skills`。上述检查会保留已存在的技能，不会覆盖它。在 Windows PowerShell 中，同样从解压后的仓库根目录执行：

```powershell
$skillRoot = Join-Path $HOME '.claude/skills'
$skillTarget = Join-Path $skillRoot 'thumbwork-phone-use'
if (Test-Path $skillTarget) { throw '技能已存在，请先备份再替换。' }
New-Item -ItemType Directory -Force $skillRoot | Out-Null
Copy-Item -Recurse './skills/thumbwork-phone-use' $skillTarget
```

你也可以直接使用文件管理器复制，无需打开终端。在 macOS 访达中选择**前往 → 前往文件夹**，输入隐藏的技能目录；在 Windows 文件资源管理器地址栏中输入对应的用户目录路径。

Codex 用户还可以让内置的 `$skill-installer` 从 `M4rque2/Thumbwork` 安装 `skills/thumbwork-phone-use`。这种方式要求下载或发布的仓库版本中已经包含该技能；尚未发布的本地版本需要从本地文件夹安装。

### 开始配置

安装后，告诉你的编程智能体：

> 使用 thumbwork-phone-use 技能，为这台电脑配置 Android 手机操作能力。安装缺少的依赖，引导我完成模型配置和手机连接，然后帮我编写并测试第一个任务的提示词。

请使用能访问已连接手机的本地执行会话。远程或云端沙箱不会因为安装了技能就获得你电脑上 USB 设备的访问能力。如果智能体找不到技能，请检查目录结构和当前项目，然后重启或重新加载智能体。如果它无法查看图片，仍可读取结构化输出，但手机截图验证需要所在环境具备图片查看能力，或由你人工检查。

智能体会依次完成：

1. 安装缺少的 Thumbwork CLI 和所需 Python 环境。
2. 询问模型服务地址和模型名称，安排私密的 API 密钥输入方式，并验证模型能否理解截图。
3. 安装 ADB 工具，引导你通过 USB 连接 Android 手机并授权调试。
4. 安装并启用 ADB Keyboard，实际测试手机文字输入。
5. 起草可复用的任务提示词，通过 CLI 运行，检查结果，并在需要时修改重试。

你负责提供凭据、连接和解锁手机、确认手机上的授权弹窗，以及描述想要的结果。智能体负责执行命令和编写提示词文件。手机试运行通过后，才算配置完成；具体任务还需要单独对照预期结果验收。试运行成功能证明提示词在本次环境下有效，但不能保证未来应用或模型变化后仍然成功。

## CLI 使用参考

以下命令适用于已经安装好的 CLI。技能会引导智能体完成前面的安装和配置。

交互式添加模型：

```bash
thumbwork models add
```

按顺序输入模型服务地址（endpoint URL）、API 密钥（输入时隐藏）和服务端模型名称。以 `/chat/completions` 结尾的完整 URL 会自动规范化。如果服务端没有返回上下文长度上限或思考开关信息，配置流程会要求你补充。思考模式可选 `on`、`off`、`unsupported` 或 `default`（不确定时不覆盖服务端设置）。`on` 和 `off` 使用 `chat_template_kwargs.enable_thinking`；请求成功本身不能证明该开关确实改变了模型行为。

配置流程会检查模型能力，并评估两次图像理解测试。模型必须支持图像输入，通过检查后才会保存配置；基础冒烟测试不阻止保存。替换配置失败时，原配置保持不变。使用 `models add NAME` 可以指定保存名称，省略 `NAME` 则根据服务端模型名称自动生成。`models verify NAME` 会重新执行检查。配置完成后会建议你按需运行以下冒烟测试：

```bash
thumbwork smoke --model NAME --task all --mode quick
```

这个手动冒烟测试会运行内置的三个截图场景，不会操作手机。测试结果不影响保存或使用模型。

`dead_phone_back` 场景始终提供相同截图，用于模拟手机无响应。模型只要在 10 轮内通过非空的 `interact` 消息请求人工帮助，即为通过。测试不限制恢复操作的方式或顺序；如果模型直接使用 `terminate` 结束任务，或耗尽轮数仍未求助，则判定失败。

模型配置默认自动保存在 `~/.thumbwork`。以下命令显示实际使用的绝对路径，不会创建该文件夹：

```bash
thumbwork models path
```

使用 `thumbwork models path --json` 可获得包含 `config_dir` 字段的 JSON 对象。测试或高级配置可以通过 `THUMBWORK_CONFIG_DIR` 环境变量指定其他配置目录。项目目录默认为 `~/thumbwork_projects`，可以通过 `THUMBWORK_PROJECTS_DIR` 环境变量，或放在 `run`、`smoke` 子命令后的 `--projects-dir` 参数修改。例如：`thumbwork run collect --projects-dir ~/Documents/thumbwork_projects`。恢复任务时会继续使用该任务已有的运行目录。

直接运行 Thumbwork 而不提供子命令，或提供无效参数时，程序会立即显示相应帮助并以退出码 2 结束。使用 `--json` 时，参数错误会返回一个 JSON 对象，包含 `status`、`reason`，以及 `help` 字段中的完整帮助文本。

技能的[配置指南](skills/thumbwork-phone-use/references/setup.md)介绍新电脑的配置流程；[任务迭代指南](skills/thumbwork-phone-use/references/task-iteration.md)介绍提示词编写、测试和任务恢复。这两份供智能体读取的指南目前为英文。

列出可用任务，然后按唯一的提示词名称运行：

```bash
thumbwork run
thumbwork run xhs_search
```

也可以直接在终端中输入提示词，无需创建任务文件：

```bash
thumbwork run --prompt "打开 Google 应用，搜索关键词‘理想汽车’，返回前 10 条结果"
```

已保存的任务名称和 `--prompt TEXT` 二选一，不能同时使用。终端提示词支持与已保存任务相同的模型和运行参数。其输入快照和结果保存在 `~/thumbwork_projects/runs/<starting-datetime>/`（或你指定的项目目录），可以通过 `thumbwork resume RUN_DIRECTORY` 恢复。终端提示词不会出现在已保存任务列表中。提示词中的 Markdown 图片路径以当前终端目录为基准解析，并复制到本次运行的输入快照中。

将每个提示词保存为 `~/thumbwork_projects/NAME/NAME.md`，引用的图片放在它旁边，例如 `assets/` 子目录中。使用 `NAME`、`NAME.md` 或提示词文件的完整路径，都可以选择同一个任务。任务名称由文件名决定；项目目录内不允许出现重复的提示词文件名，大小写不同也视为重复。历史运行快照不参与重复检查。

```text
~/thumbwork_projects/NAME/
├── NAME.md
├── assets/
└── runs/
    └── YYYY-MM-DD_HH-MM-SS_microseconds+timezone-id/
        ├── input/
        ├── output.jsonl
        ├── result.json
        ├── checkpoint.json
        ├── state/
        └── debug/          # 仅在使用 --debug 时创建
```

运行文件夹名称包含本地开始日期、时间、时区和唯一后缀。每次运行都会保留独立的输入快照和结果。可以通过 `--projects-dir` 或 `THUMBWORK_PROJECTS_DIR` 修改项目根目录；已保存的任务提示词必须放在该根目录下的 `NAME/NAME.md` 中。旧版 `--task-name` 参数已移除。

### 可选的调试文件

默认不保存调试输出。需要在 `debug/` 中保留截图、标注截图、模型请求与响应记录以及日志时，使用 `thumbwork run NAME --debug`。这些文件可能占用较多磁盘空间。`smoke --debug` 同样会保留模型调用记录。

不使用 `--debug` 时，不会创建调试目录或保存模型调用记录。智能体当前上下文和检查点仍需要的截图保存在 `state/` 中；压缩上下文时会清理过期截图。已保存的提示词、提取输出、运行结果和检查点仍会保留。

`resume` 会沿用原运行的调试设置。新运行不加 `--debug`，不会删除旧运行中的调试文件。如果准备恢复未完成的任务，请不要删除其检查点引用的截图。

### 在手机上输入文字

`type` 动作会替换当前获得焦点的输入框中的文字；传入空字符串会清空内容。它不会按下回车。智能体必须检查下一张截图，再通过单独的动作提交搜索或表单。如果当前没有获得焦点的可编辑输入框，智能体会收到反馈，要求先点击输入框再重试。

ADB Keyboard 是首选输入方式。文字通过它的 [UTF-8 Base64 输入协议](https://github.com/senzhk/ADBKeyBoard#usage-example)发送，能够保留空格、中文和标点。剪贴板与普通 ADB 输入作为备用方式；遇到不支持的文本会报错，不会悄悄丢失字符。临时切换输入法后会恢复原输入法，输入失败时也会尝试恢复。

Thumbwork 会等待 ADB Keyboard 被选中并准备就绪，然后才清空或发送文字。只有设备明确提供所需命令时，才会使用备用方式；不支持的剪贴板或全选命令会在修改输入框前被跳过或报告。缺少输入能力时，运行会暂停并给出配置说明，你可以启用 ADB Keyboard 后使用 `resume` 继续。设备命令超时也会暂停运行，供你检查，因为文字可能已经部分输入。

输入日志会标明使用的方式，并报告 `status=sent verification=pending`。随后，智能体会检查新截图。空输入框中的灰色占位文字不应被误认为未清除的内容。如果恢复原输入法失败，Thumbwork 会报告问题，不会因此自动重新输入文字。
