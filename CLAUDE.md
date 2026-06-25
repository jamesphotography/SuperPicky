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
- **设置中心 / Settings Center**：`ui/settings_center.py` 的 `SettingsCenter`（左侧分类导航 + 右侧 6 页：精选/识鸟/输出/视频/外部应用/关于）。主窗口经 `_open_settings_center(start_page)` 打开；关闭后调用 `_refresh_skill_chip()` + `_refresh_param_panel()` 刷新首页。
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
* 代码优化精简的时候需要按照逻辑顺序对变量函数进行重排，方便维护者从上到下进行阅读。
  When optimizing and simplifying the code, rearrange variables and functions in logical order to make it easier for maintainers to read from top to bottom.
* 跨文件代码边界维护要清晰分明，高内聚低耦合。
  Maintain clear boundaries for cross-file code, ensuring high cohesion and low coupling.
* 在Python中，避免使用全局变量。优先选择函数或类封装，保持数据和功能分离。
  In Python, avoid using global variables. Prefer encapsulation in functions or classes to separate data and functionality.

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

在使用Python语言时均需要满足如下规范：
The following specifications must be met when using Python:

* **类型注解 / Type Annotations** ：尽量使用类型注解（Python 3.x），以增强代码可读性和静态检查工具的支持。例如，函数的输入和输出应该明确标注类型。
  **Type annotations** : Try to use type annotations (Python 3.x) to enhance code readability and static analysis tool support. For example, the input and output of functions should clearly annotate their types.

```python
  def add_numbers(a: int, b: int) -> int:
      return a + b
```

* **避免使用过于宽泛的类型标注 / Avoid overly broad type annotations** ：Python中不存在** **`any`类型，但要尽量避免过于宽泛的类型标注。
  Python does not have an** **`any` type, but avoid overly broad type annotations whenever possible.
* **操作用户文件规范 / User File Operations** ：当使用代码操作用户系统中的文件时，要使用安全的方法，并注意权限。对于配置文件的存放位置应该局限在一个文件夹内，不要在用户的文件夹中到处存放零星文件。
  When manipulating user files, use secure methods and be mindful of permissions. The storage location for configuration files should be limited to a single folder, and avoid scattering files across the user's directories.
* **遵循PEP8规范 / Follow PEP8** ：始终遵循Python的官方代码风格PEP8，并且使用自动化工具（如** **`black`）进行格式化。
  Always follow the official Python coding style PEP8 and use automation tools (like** **`black`) for formatting.
* **严格使用UTF-8 / Strict Use of UTF-8** ：始终遵循Python的官方代码标准PEP686，始终使用 UTF-8 作为文件、标准输入输出和管道的默认编码。
  Always follow Python's official code standard PEP686, and use UTF-8 as the default encoding for files, standard input/output, and pipes.
* **注重安全性 / Focus on Security** ：避免直接执行来自不可信来源的代码，如避免使用** **`eval()`或** **`exec()`等函数。使用适当的输入验证和参数化查询，避免SQL注入、XSS等安全漏洞。
  Avoid executing code from untrusted sources, such as using** **`eval()` or** **`exec()`. Use proper input validation and parameterized queries to avoid SQL injection, XSS, and other security vulnerabilities.

```python
  import sqlite3
  connection = sqlite3.connect('database.db')
  cursor = connection.cursor()

  # 避免 SQL 注入，使用参数化查询
  cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
```

* **异常处理 / Exception Handling** ：要优雅地处理可能的错误和异常，避免程序崩溃。优先使用Python标准库提供的异常机制。
  Handle potential errors and exceptions gracefully to avoid crashes. Use Python's standard exception mechanisms first.

```python
  try:
      result = 10 / 0
  except ZeroDivisionError as e:
      print(f"Error occurred: {e}")
```

## Python 3 环境配置与工具使用规范 / Python 3 Environment Setup and Tool Usage Specifications

为了避免Python 3工具默认使用系统中的Python环境（可能导致许多不可预料的问题），请务必采用以下规范进行配置：

* **使用虚拟环境 / Virtual Environment** ：优先使用 `venv`或 `conda`等工具创建独立的Python环境，避免使用系统全局环境。
  Prefer using** **`venv` or** **`conda` to create isolated Python environments, avoiding the use of the system's global environment.
* **确保包管理一致性 / Ensure Package Management Consistency** ：在项目中使用 `pip`来管理依赖，确保依赖版本的一致性，避免版本冲突和意外问题。
  Use** **`pip` to manage dependencies in the project, ensuring version consistency and avoiding conflicts and unexpected issues.
* **工具使用推荐 / Recommended Tool Usage** ：为了避免依赖于系统环境的Python，建议使用虚拟环境中的解释器进行构建和运行。
  To avoid relying on the system environment's Python, it is recommended to use the interpreter in the virtual environment for builds and executions.

## 多系统规范 / Multi-System Specifications

### 1. 避免多系统之间的差异导致程序出现无法运行甚至安全漏洞 / Avoid System-Specific Differences Leading to Errors or Security Vulnerabilities

- 在开发跨平台应用时，需避免代码中因操作系统差异（如Windows与Linux、macOS之间的差异）导致程序无法运行或出现安全漏洞。
  When developing cross-platform applications, avoid code differences that cause errors or security vulnerabilities due to differences between operating systems (e.g., Windows vs. Linux or macOS).

- **路径问题**：文件路径的格式在不同操作系统间有所不同。确保使用跨平台兼容的路径分隔符，推荐使用Python的 `os.path`模块，或 `pathlib`模块来自动处理路径分隔符。
  **Path Issues**: File path formats differ across operating systems. Ensure the use of cross-platform compatible path separators. It is recommended to use Python's `os.path` or `pathlib` modules to automatically handle path separators.

  ```
  from pathlib import Path

  file_path = Path("some_folder") / "file.txt"  # This works across all OS
  ```

- **换行符问题**：Windows和类Unix系统的换行符不同。
  **Line Endings**: Line endings differ between Windows and Unix-based systems.

### 2. 不同系统的文件存储策略和文件夹权限管理不同，需要提前预防 / Different Systems Have Different File Storage and Folder Permissions

- 在设计涉及文件存储和访问的应用时，需注意不同操作系统对文件权限和路径访问的管理差异。Windows、Linux和macOS在文件权限、符号链接和隐藏文件的处理上有所不同。
  When designing applications that involve file storage and access, be aware of the differences in file permission and path access management across operating systems. Windows, Linux, and macOS handle file permissions, symlinks, and hidden files differently.
- **权限问题**：Linux和macOS有严格的文件权限控制，而Windows则使用ACL（访问控制列表）来管理权限。确保文件的读写权限适合所使用的操作系统，并且文件夹权限应在应用设计时进行适当配置。
  **Permission Issues**: Linux and macOS have strict file permission controls, while Windows uses ACLs (Access Control Lists) for permission management. Ensure that file read/write permissions are suitable for the operating system in use, and folder permissions should be appropriately configured during application design.

### 3. 避免大量使用PowerShell代码 / Avoid Excessive Use of PowerShell Code

- PowerShell主要是Windows环境下使用的脚本语言，避免在跨平台项目中广泛使用PowerShell。为了确保程序的兼容性，尽量使用Python脚本或其他语言。
  PowerShell is primarily used in Windows environments. Avoid using PowerShell extensively in cross-platform projects. To ensure compatibility, try to use Python scripts or other languages instead.

- 如果必须使用PowerShell，请确保通过条件语句检查操作系统类型，并仅在Windows系统中执行相关命令。
  If PowerShell must be used, ensure that conditional statements are used to check the operating system and only execute related commands on Windows systems.

  ```
  import platform

  if platform.system() == "Windows":
      # Execute PowerShell command
      pass
  ```
