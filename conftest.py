# -*- coding: utf-8 -*-
"""
全局测试防线：不让模态对话框把测试进程静默挂死。

事故（2026-09-08）：某个端到端测试建了两个已处理批次后调 open_directory，
产品代码弹出目录选择框，`QDialog.exec()` 在无头环境里永远等不到人点。整个
pytest 进程就那么停住——没有报错、没有超时、没有任何输出。更糟的是当时用
``pytest ... | tail -3`` 取结果，管道的退出码是 tail 的，把进程 kill 掉之后
harness 看到的是 exit 0，于是「全量测试绿」被误报了两次。

阻塞比失败危险得多：失败会告诉你是哪一行，阻塞只会让人以为「还在跑」。所以
这里把模态入口统统换成立刻抛错——测试要么自己 stub 掉那个交互点，要么当场
看到一条指名道姓的报错。

需要走对话框的测试照常 monkeypatch 自己那一个（monkeypatch 在用例体内执行，
晚于本 fixture，所以一定盖得住这里的替身）。

A modal dialog in a headless test run blocks forever with no output, which is
far worse than a failure. Every modal entry point raises instead; tests that
need one stub it themselves.
"""
import os

import pytest

# Qt 平台必须在任何 QApplication 之前定好，否则 CI/无头机上会去连显示服务
# Must be set before any QApplication exists.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# 构造主窗口时不触发后台启动工作（识鸟服务器 / 模型预加载 / startup.log）。
# 事故（2026-10-05）：测试里构造的主窗口 1 秒后起了真实的常驻识鸟服务器进程，
# 测试结束后成了占着 5156 端口的孤儿（用户的 Lightroom 插件连的就是它）；3 秒后
# 各窗口实例各开一个线程并发加载 torch 模型，偶发堆损坏，整个 pytest 进程以
# SIGTRAP（退出码 133）崩掉，此前被误判为「内存不足」。见 ui/main_window.py。
# Constructing the main window must not start its background work: it spawned an
# orphaned Bird-ID server and raced torch model loads (intermittent SIGTRAP).
os.environ["SUPERPICKY_NO_BACKGROUND_STARTUP"] = "1"

# 测试统一用的界面语言：不随本机系统语言漂移
# Fixed UI language for tests, independent of the machine's locale.
TEST_LANGUAGE = "zh_CN"


def _blocked(name: str):
    """造一个「一调用就报错」的替身，报错里写清楚该怎么办。"""

    def _raise(*_args, **_kwargs):
        raise RuntimeError(
            f"测试中调用了模态对话框 {name}()，它会永久阻塞整个测试进程。"
            f"请在用例里 stub 掉这个交互点"
            f"（例如 monkeypatch.setattr(窗口类, '_ask_which_batches', ...)）。"
            f"\nBlocking modal {name}() called during tests; stub it instead."
        )

    return _raise


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch):
    """把所有模态入口换成立刻抛错的替身（每个用例自动生效）。"""
    try:
        from PySide6.QtWidgets import (QDialog, QFileDialog, QInputDialog,
                                       QMessageBox)
    except Exception:          # 没装 Qt 的环境（纯逻辑测试）直接跳过
        return

    monkeypatch.setattr(QDialog, "exec", _blocked("QDialog.exec"))
    # QMessageBox / QFileDialog / QInputDialog 的静态便捷方法在 C++ 里自己起
    # 事件循环，盖不住上面那个 QDialog.exec，必须逐个替换。
    # The static convenience methods spin their own C++ event loop.
    for cls, methods in (
        (QMessageBox, ("warning", "information", "critical", "question", "about")),
        (QFileDialog, ("getExistingDirectory", "getOpenFileName",
                       "getOpenFileNames", "getSaveFileName")),
        (QInputDialog, ("getText", "getItem", "getInt", "getDouble")),
    ):
        for method in methods:
            monkeypatch.setattr(cls, method,
                                staticmethod(_blocked(f"{cls.__name__}.{method}")),
                                raising=False)


def _restore_i18n_instances(saved: dict) -> None:
    """
    把各语言 i18n 实例的当前语言恢复到测试前（构造主窗口等会 switch_language 改共享实例）。

    参数:
    saved (dict): 注册表键 → 测试前的 current_lang

    Restore each shared i18n instance's language to its pre-test value.
    """
    from config import get_lazy_registry

    registry = get_lazy_registry()
    for key, lang in saved.items():
        inst = registry.get(key)
        if inst is not None and getattr(inst, "current_lang", lang) != lang:
            inst.switch_language(lang)


def _drop_instance_method_shadows() -> None:
    """
    清掉测试留在 exiftool 单例**实例**上的方法替身。

    pytest 的 monkeypatch.setattr(实例, "方法", 替身) 在还原时不会删除实例属性，
    而是把原方法作为绑定方法写回实例 __dict__——此后它永久遮住类上的同名方法，
    后续测试在类上打的补丁全部失效（2026-10-05：test_exiftool_write_mode_none
    合跑必挂的根因）。这里在每个测试后把这类遮挡删掉，让单例回到干净状态。

    Remove method shadows left on the exiftool singleton instance: monkeypatch's
    undo writes the original bound method back into the instance __dict__, which
    then hides later class-level patches.
    """
    import sys

    module = sys.modules.get("tools.exiftool_manager")
    inst = getattr(module, "exiftool_manager", None) if module else None
    if inst is None:
        return
    cls = type(inst)
    for name in list(vars(inst)):
        if callable(getattr(cls, name, None)) and callable(vars(inst)[name]):
            delattr(inst, name)


@pytest.fixture(autouse=True)
def _isolate_global_state(tmp_path_factory):
    """
    每个用例自动隔离进程级共享状态，杜绝「单跑绿、合跑红」与改动用户真实设置：

    1. 配置：注入一份写到临时目录的 AdvancedConfig 单例（界面语言固定为
       TEST_LANGUAGE）。此前测试直接读写用户真实的 advanced_config.json，
       结果随本机设置漂移，还会改掉用户的设置。
    2. 界面语言：固定主语言；用例结束后把各共享 i18n 实例的语言恢复原状。
    3. exiftool 单例：用例结束后清掉留在实例上的方法替身。

    需要自定义配置的用例照常自己注入（在用例体内执行，晚于本 fixture，覆盖得住）。

    Per-test isolation of process-wide state: a temp-file config singleton with a
    fixed language, restored i18n instances, and a clean exiftool singleton.
    """
    try:
        from advanced_config import AdvancedConfig
        from config import get_lazy_registry
        from tools.i18n import get_i18n, set_primary_language
    except Exception:          # 精简环境（纯逻辑测试）直接跳过 / minimal envs
        yield
        return

    registry = get_lazy_registry()
    cfg_dir = tmp_path_factory.mktemp("sp_config")
    cfg = AdvancedConfig(config_file=str(cfg_dir / "advanced_config.json"))
    cfg.set_language(TEST_LANGUAGE)
    registry.set("advanced_config.instance", cfg)

    get_i18n(TEST_LANGUAGE)
    set_primary_language(TEST_LANGUAGE)
    saved_langs = {
        key: registry.get(key).current_lang
        for key in (f"i18n.instance::{lang}" for lang in ("zh_CN", "zh_TW", "en_US", "auto"))
        if registry.get(key) is not None
    }
    try:
        yield
    finally:
        registry.clear("advanced_config.instance")
        _restore_i18n_instances(saved_langs)
        set_primary_language(TEST_LANGUAGE)
        _drop_instance_method_shadows()


# ---------------------------------------------------------------------------
# 退出时跳过 PySide 析构 / Skip the PySide teardown at exit
# ---------------------------------------------------------------------------
# 事故（2026-11-17）：测试全部通过后，解释器收尾（Py_Finalize）时 shiboken 销毁残留的
# Qt 包装对象（设置中心的复选框、标签等）访问已释放内存，进程以 SIGSEGV（退出码 139）
# 结束，macOS 每次弹「Python 意外退出」。测试结论不受影响，但退出码被污染，任何靠退出码
# 判断成败的自动化都会误报。与软件 ⌘Q 退出崩溃同一根因（分支 fix/macos-quit-segfault）。
# 崩溃点实测在 PySide 自己登记的 atexit 收尾里（销毁 QApplication 与残留控件），所以
# 不能把 atexit 全跑一遍：只执行本项目经 tools.exit_hooks 登记的清理（ExifTool 常驻进程
# 关闭、缩略图加载线程停止等；os._exit 跳过 atexit，不手动执行 ExifTool 会成孤儿进程），
# 刷新输出，再按 pytest 的退出码 os._exit——与 main.py 的 _finish_shutdown_and_exit 一致。
# Incident: after every test passed, the process died with SIGSEGV (exit 139) in
# PySide's own atexit teardown of the QApplication and leftover widgets. Same root
# cause as the app's Cmd+Q crash. So we must not run every atexit handler: run only
# this project's hooks registered via tools.exit_hooks (ExifTool's persistent
# process, thumbnail loader threads; os._exit skips atexit and would orphan
# ExifTool), flush, then os._exit with pytest's status, as main.py does.
_SESSION_EXIT_STATUS = [0]


def pytest_sessionfinish(session, exitstatus):
    """
    记下 pytest 的最终退出码，供 pytest_unconfigure 退出时使用。

    Record pytest's final exit status for pytest_unconfigure.
    """
    _SESSION_EXIT_STATUS[0] = int(exitstatus)


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config):
    """
    所有插件（含 junitxml 等）收尾之后，执行 atexit 清理并直接退出，跳过会段错误的
    PySide/shiboken 析构（含 PySide 自己的 atexit 收尾）。设 SUPERPICKY_PYTEST_NORMAL_EXIT=1 可恢复正常退出（排查时用）。

    After every plugin has finished, run atexit handlers and exit directly, skipping
    the crashing PySide/shiboken teardown. Set SUPERPICKY_PYTEST_NORMAL_EXIT=1 to
    restore the normal exit while debugging.
    """
    if os.environ.get("SUPERPICKY_PYTEST_NORMAL_EXIT") == "1":
        return
    import sys
    # 项目的退出清理都登记在 tools.exit_hooks（只有已导入的模块才登记过）
    # Project exit cleanups live in tools.exit_hooks (registered on import only)
    hooks = sys.modules.get("tools.exit_hooks")
    if hooks is not None:
        try:
            hooks.run_all()
        except Exception as exc:  # 清理失败不应掩盖测试结果 / never mask the test result
            print(f"[conftest] exit hooks failed: {exc}", file=sys.stderr)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    os._exit(_SESSION_EXIT_STATUS[0])
