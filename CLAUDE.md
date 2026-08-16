# CLAUDE.md (Claude / Anthropic Coding Agents)

Use `scripts_dev/AI_CODING_RULES.md` as the single source of truth for this repository.

## Always Enforce

- UTF-8 safety first; do not introduce Chinese text corruption.
- ExifTool Chinese metadata writes must use UTF-8 temp files (`-XMP:Title<=tmp.txt`) instead of inline CLI values.
- Keep changes cross-platform (Windows + macOS).
- Any persistent external process must have deterministic cleanup on task/app exit.
- Packaged CUDA failures: prioritize packaging/runtime diagnosis before algorithm refactors.
- Keep Windows Torch/CUDA packaging with `upx=False` unless explicitly requested and validated.

## Minimum Verification

- Run `.venv*/bin/python -m py_compile` on changed Python files.
- For metadata changes: write + read-back verification with Chinese sample values.
- For `.spec` changes: packaged startup smoke test.
- For DB/threading changes: run a small multi-thread write/read stress check and confirm no transaction-state errors.

## 设置中心架构 / Settings Center Architecture

所有用户设置统一由「设置中心」管理。改动任何设置相关代码前必读以下约定（违反这些约定正是本次重构前的混乱根源）：
All user settings are managed by the unified Settings Center. Read these conventions before touching any settings code:

- **单一事实源 / SSOT**：`advanced_config`（`advanced_config.json`）是所有设置的唯一存储。新增设置项 = 在 `DEFAULT_CONFIG` 加字段 + 加 `@property`/`set_*`；**setter 的 clamp 范围必须与 UI 控件范围一致**。**禁止**再引入独立 json 或控件本地状态。
  `advanced_config` is the only store. Add a setting via DEFAULT_CONFIG + property/setter; the setter's clamp range MUST match the UI widget range. Never add separate json files or widget-local state.
- **设置中心 / Settings Center**：`ui/settings_center.py` 的 `SettingsCenter`（左侧分类导航 + 右侧 5 页：精选/识鸟/输出/外部应用/关于）。页面与导航项都由 `PAGE_ORDER` 这一个列表驱动，增删页面改它即可——4.5.0 的 ExtremeSimple 就是把 `"video"` 从中摘掉来下架视频功能的（`_build_video_page()` 与图标/标题映射都保留，加回 key 即恢复）。主窗口经 `_open_settings_center(start_page)` 打开；关闭后调用 `_refresh_skill_chip()` + `_refresh_param_panel()` 刷新首页。
  Both the nav items and the stacked pages come from the single `PAGE_ORDER` list; 4.5.0 dropped video by removing that one key.
- **首页快速面板 / Home quick panel**：`main_window._create_parameters_section`（2 滑块：锐度/美学 + 3 开关：飞行/连拍/识鸟）是 `advanced_config` 的快捷编辑器，与设置中心**双向同步**。两处编辑同一字段，**滑块范围必须一致且对齐 setter clamp**——否则会静默截断或默认值漂移（已踩坑：锐度 100-600、美学 0-70）。
- **技能等级 / Skill level**：用 `core.skill_presets`（无 Qt 依赖）做 档↔阈值 换算。手动改阈值 → `skill_level="custom"` 并同步 `custom_sharpness`/`custom_aesthetics`（精选页与首页都遵循此协同，避免 GUI/CLI 路径发散）。
- **开关样式 / Checkbox style**：统一用 `ui.icon_utils.checkbox_indicator_qss`（圆圈=未选 / 带勾圆圈=选中），勿用全局默认方块。
- **识鸟设置 / BirdID**：`birdid_*` 字段在 `advanced_config`；启动时 `migrate_birdid_dock_settings()` 从旧 `birdid_dock_settings.json` 幂等迁移（接线在 `main.py`）；区域数据加载用 `core/region_data.py`；识鸟面板 `birdid_dock` 只负责运行时 UI（选图/截图/结果）。
- **已删除 / Removed**：`ui/about_dialog.py`、`ui/advanced_settings_dialog.py`（内容并入设置中心）。`ui/skill_level_dialog.py` 仅保留被复用的 `SkillLevelCard`/`SkillLevelSelector`/`get_skill_level_thresholds`。
- 设计与计划文档 / Design & plan docs：`docs/specs/2026-06-24-settings-center-design.md`、`docs/plans/2026-06-24-settings-center.md`。

## 第一性原理 / First Principles

请使用第一性原理思考。你不能总是假设我非常清楚自己想要什么和该怎么得到。请保持审慎，从原始需求和问题出发，如果动机和目标不清晰，停下来和我讨论。
Please use first principles thinking. You should not assume that I always know exactly what I want or how to achieve it. Be cautious and start from the original needs and problems. If the motivation and goals are unclear, stop and discuss with me.

## 技术方案规范 / Technical Solution Specifications

当需要你给出修改或者重构方案时必须符合以下规范：
The following specifications must be followed when giving modification or refactoring plans:

* 你是技术专家，所以设计方案时要使用各种工具查询网络资料，确定基本事实，不要给出虚假观点。
  You are a technical expert, so when designing solutions, use various tools to check online resources and ensure the basic facts are correct. Do not provide false opinions.
* 除非我很确定，不然不能随意迁就我的观点，因为我的观点很可能是错的，需要基于基本事实有理有据的说服我同意你的新观点。
  Unless I am very sure, do not easily accommodate my opinions because they may be wrong. You need to convince me to agree with your new views based on facts.
* 给出兼容性或者补丁性的方案时需要给出确定性的理由与我讨论。
  When proposing compatibility or patch solutions, provide definitive reasons for discussion.
* 必须确保方案的逻辑正确，必须经过全链路的逻辑验证。
  Ensure that the solution is logically correct and has been verified across the entire system.

## 编码规范 / Coding Specifications

所有文件读写均需要满足如下规范：
All file reading and writing must meet the following specifications:

* 使用UTF-8编码，强制所有的中文输出，均为UTF-8。
  Use UTF-8 encoding, and enforce all Chinese output to be UTF-8.
* 在PowerShell中读取含有中文的文件时，限制性** **`chcp 65001`并设置UTF-8输出。
  When reading Chinese files in PowerShell, use** **`chcp 65001` and set UTF-8 output.
* 读取时用** **`open(file, 'r', encoding='utf-8')`方式读取。
  Use** **`open(file, 'r', encoding='utf-8')` to read files.
* 不要使用shell脚本（如sed/awk）处理含中文的文件，优先使用Python（Python 3.x），如果Python环境无法满足需求，再考虑其他语言，最后才考虑PowerShell。
  Do not use shell scripts (like sed/awk) to handle files with Chinese characters. Prefer Python (Python 3.x), and if Python environment cannot meet the requirements, consider other languages, and only as a last resort consider PowerShell.

## 代码规范 / Code Specifications

所有代码增删查改均需要满足如下规范：
All code changes (addition, deletion, modification) must meet the following specifications:

* 先阅读相关代码段落，预先评估代码修改量，如果发现改动文件过多，或者改动量很大，提前分成几个小部分进行修补，避免系统拒绝修补。
  First, read the relevant code sections, assess the extent of the changes, and if too many files are affected or the changes are too large, break them down into smaller parts to avoid rejection by the system.
* 代码按照逻辑顺序进行修补，避免改完之后又回头改。
  Make code changes in logical order to avoid having to go back and modify things again.
* 代码改动完毕后要重新整体阅读全链路，避免出现变量函数未定义未声明导致编译不通过。
  After code changes, review the entire system to ensure there are no undefined or undeclared variables or functions that could cause compilation errors.

## 注释规范 / Commenting Specifications

所有注释增删查改均需要满足如下规范：
All comment changes (addition, deletion, modification) must meet the following specifications:

* 如果没有额外指定，请使用UTF-8编码的中文注释 + 相同格式的英文注释。
  If not otherwise specified, use UTF-8 encoded Chinese comments + corresponding English comments in the same format.
* 需要给出详细且必要的功能说明，增加可维护性，让不熟悉相关类型代码的人也能看懂。
  Provide detailed and necessary functional descriptions to increase maintainability, so that those unfamiliar with the relevant code can understand it.
* 使用docstring格式进行函数、类注释，确保清晰描述函数的功能、参数、返回值及可能的异常。
  Use docstring format for function and class comments, ensuring clear descriptions of the function's functionality, parameters, return values, and possible exceptions.

```python
def example_function(param: int) -> str:
    """
    这是一个示例函数，接受一个整数作为输入，返回字符串。

    参数:
    param (int): 输入的整数

    返回:
    str: 返回一个简单的字符串，表示输入的平方值

    This is a sample function that takes an integer as input and returns a string.

    Parameters:
    param (int): The integer to input

    Return:
    str: Returns a simple string representing the square of the input.
    """

    return f"The square is {param ** 2}"
```

## 总结汇报规范 / Summary Reporting Specifications

所有的总结汇报均需要满足如下规范：
All summary reports must meet the following specifications:

* 改动部分请加上具体文件的行号，如果涉及多个跨行的改动，给出相关段落，方便进行查找。
  Specify the line numbers of the changed parts, and provide relevant sections for easy search if multiple lines are involved.
* 对于Python项目，考虑到代码可能涉及模块导入、功能封装等，需要明确指出哪些模块或类的修改或新增影响了其他模块的功能。
  For Python projects, since the code may involve module imports and function encapsulation, clearly indicate which module or class changes or additions affect the functionality of other modules.

## Python使用规范 / Python Usage Specifications

* **类型注解 / Type Annotations**：函数入参与返回值均标注类型；避免过于宽泛的标注
  （Python 无 `any` 类型）。
  Annotate parameter and return types; avoid overly broad annotations.
* **严格使用 UTF-8 / Strict UTF-8**：遵循 PEP 686，文件、标准输入输出与管道一律 UTF-8。
  Follow PEP 686 — UTF-8 for files, stdio, and pipes.
* **操作用户文件 / User File Operations**：配置文件集中存放于单一目录，不要在用户目录
  各处散落零星文件。
  Keep config files in one directory; do not scatter files across the user's home.

## Python 3 环境配置与工具使用规范 / Python 3 Environment Setup and Tool Usage Specifications

为了避免Python 3工具默认使用系统中的Python环境（可能导致许多不可预料的问题），请务必采用以下规范进行配置：

* **使用虚拟环境 / Virtual Environment** ：优先使用 `venv`或 `conda`等工具创建独立的Python环境，避免使用系统全局环境。
  Prefer using** **`venv` or** **`conda` to create isolated Python environments, avoiding the use of the system's global environment.
* **确保包管理一致性 / Ensure Package Management Consistency** ：在项目中使用 `pip`来管理依赖，确保依赖版本的一致性，避免版本冲突和意外问题。
  Use** **`pip` to manage dependencies in the project, ensuring version consistency and avoiding conflicts and unexpected issues.
* **工具使用推荐 / Recommended Tool Usage** ：为了避免依赖于系统环境的Python，建议使用虚拟环境中的解释器进行构建和运行。
  To avoid relying on the system environment's Python, it is recommended to use the interpreter in the virtual environment for builds and executions.

## 多系统规范 / Multi-System Specifications

* **路径**：用 `os.path` / `pathlib` 处理路径，不得硬编码分隔符。
  Use `os.path` / `pathlib`; never hard-code separators.
* **权限**：Linux/macOS 与 Windows 的文件权限模型不同（POSIX 位 vs ACL），涉及
  文件读写权限的设计需两边都验证。
  POSIX bits vs Windows ACLs differ; verify permission-sensitive code on both.
* **避免 PowerShell**：跨平台项目中不要依赖 PowerShell；必须用时以
  `platform.system() == "Windows"` 守卫。
  Avoid PowerShell in cross-platform code; guard with `platform.system()` when unavoidable.
