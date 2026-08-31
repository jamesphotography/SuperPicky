#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
遥测投递层测试：按日轮换 ID 与上报内容契约。

Telemetry delivery tests: daily-rotating ID and payload contract.
"""
import re

from app_user_stat.telemetry import _build_request_payload, _daily_rotating_id


def test_daily_id_is_sha256_hex() -> None:
    """上报 ID 为 64 位十六进制，与 Worker 的校验正则一致。/ 64-hex id."""
    value = _daily_rotating_id("install-abc", "2026-08-21")
    assert re.fullmatch(r"[0-9a-f]{64}", value)


def test_daily_id_is_stable_within_a_day() -> None:
    """同一天内稳定，否则算不出去重日活。/ Stable within one day."""
    a = _daily_rotating_id("install-abc", "2026-08-21")
    b = _daily_rotating_id("install-abc", "2026-08-21")
    assert a == b


def test_daily_id_rotates_across_days() -> None:
    """跨日必须变化，这是「不构成持久标识符」的关键。/ Rotates daily."""
    a = _daily_rotating_id("install-abc", "2026-08-21")
    b = _daily_rotating_id("install-abc", "2026-08-22")
    assert a != b


def test_daily_id_differs_between_installs() -> None:
    """不同安装不得碰撞，否则日活会被低估。/ Distinct per install."""
    a = _daily_rotating_id("install-abc", "2026-08-21")
    b = _daily_rotating_id("install-xyz", "2026-08-21")
    assert a != b


def test_payload_never_contains_the_local_install_id() -> None:
    """
    本地安装 ID 永不上报——这是整个匿名方案的立足点。

    The local install id must never appear in the payload.
    """
    payload = _build_request_payload("install-secret-value", ["app_start"])
    assert "install-secret-value" not in str(payload)


def test_payload_matches_worker_contract() -> None:
    """字段与 Worker 的 validatePayload 白名单一致。/ Matches /t contract."""
    payload = _build_request_payload("install-abc", ["install", "app_start"])
    assert payload["v"] == 1
    assert re.fullmatch(r"[0-9a-f]{64}", payload["id"])
    assert payload["events"] == ["install", "app_start"]
    for key in ("app_version", "os", "arch", "python_version", "locale"):
        assert isinstance(payload[key], str) and payload[key]


def test_on_ready_fires_even_when_telemetry_disabled(monkeypatch) -> None:
    """
    遥测关闭时 on_ready 仍须触发，且这条路径下绝不能碰线程、文件或网络。

    telemetry_enabled 默认是 True（advanced_config.py），关闭是少数用户
    主动选择的路径。「关掉就不发」是 opt-out 默认开启这件事唯一的立足点，
    所以这条测试守的是两件事：on_ready 依旧触发，且 run() 在读到
    telemetry_enabled=False 后必须**在构造 _TelemetryClient 之前**就返回。

    这条测试此前是**无法失败**的，值得记下来免得改回去：

    1. 它只桩掉了 `request.urlopen`，用 `pytest.fail` 作为哨兵。但真正的
       投递发生在 `_TelemetryClient.bootstrap()` 起的**守护线程**上。
       `pytest.fail` 抛的 `Failed` 继承自 `BaseException`，`_send_due_events`
       的 `except Exception` 接不住它，于是它在工作线程里静静死掉——
       pytest 从头到尾看不见。也就是说，针对「删掉那个 early return」这个
       最该被抓住的变异，测试照样是绿的。
    2. 那个变异还会顺带读写用户**真实的** telemetry_state.json；
    3. 守护线程可能活过 monkeypatch 拆卸，把 urlopen 还原成真的，
       于是一次测试运行真的向线上发了一个 POST。

    改法：把 `_TelemetryClient` 一并桩掉，让违约在**主线程**上、在任何
    线程/文件系统访问发生**之前**就被记录下来；并且用「记录到列表 + 事后
    断言」而不是「在哨兵里抛异常」——后者的成败取决于异常类的继承关系和
    它跑在哪个线程上，那正是上一版栽的跟头。urlopen 哨兵保留为第二道防线，
    同样只记录不抛。

    The previous version of this test could not fail. It only stubbed
    urlopen with `pytest.fail`, but delivery happens on a daemon thread and
    `Failed` subclasses `BaseException`, so it died unseen in the worker and
    the test stayed green against the one mutation it exists to catch —
    while reading/creating the user's real state file and potentially firing
    a genuine production POST after monkeypatch teardown. Now
    `_TelemetryClient` is stubbed too, so any violation is recorded on the
    main thread before a thread or the filesystem is ever touched, and the
    assertion is made on a recorded list rather than on an exception whose
    visibility depends on its base class and which thread raised it.
    """
    import app_user_stat.telemetry as tm

    class _Off:
        telemetry_enabled = False

    # 违约记录。用列表而不是抛异常：列表在哪个线程上被追加都算数，
    # 而异常只有在主线程上、且基类是 Exception 时才会被 pytest 看见。
    # A list, not an exception: appends count from any thread.
    violations: list = []

    class _MustNotBeUsed:
        """开关关闭时构造它就是违约——它一旦被构造，就意味着后面会起线程、
        读写 telemetry_state.json 并发出请求。
        Constructing this at all is the violation."""

        def __init__(self) -> None:
            violations.append("_TelemetryClient() 被构造")

        def bootstrap(self) -> None:
            violations.append("bootstrap() 被调用")

    monkeypatch.setattr(tm, "_schedule_on_qt_event_loop", lambda fn: False)
    monkeypatch.setitem(__import__("sys").modules, "advanced_config",
                        type("M", (), {"AdvancedConfig": lambda: _Off()}))
    monkeypatch.setattr(tm, "_BOOTSTRAPPED", False)
    monkeypatch.setattr(tm, "_TelemetryClient", _MustNotBeUsed)
    monkeypatch.setattr(
        tm.request, "urlopen",
        lambda *a, **k: violations.append("urlopen() 被调用"),
    )

    fired = []
    tm.bootstrap_telemetry(parent=None, on_ready=lambda: fired.append(True))

    assert violations == [], f"关闭遥测后仍发生了投递动作: {violations}"
    assert fired == [True]


def test_on_ready_fires_when_telemetry_enabled(monkeypatch) -> None:
    """
    默认配置（telemetry_enabled=True）下 on_ready 也必须触发。

    多数用户走的是这条「启用」路径：_TelemetryClient().bootstrap() 之后
    落到 finally。之前只测了关闭分支，等于没测到大多数用户实际会走的
    代码。这里把 _TelemetryClient 换成不起线程、不碰文件系统的桩，只验证
    on_ready 的触发时机。

    on_ready must also fire on the default "enabled" path, which is what
    most users actually hit. _TelemetryClient is stubbed out so this test
    starts no thread and touches no filesystem.
    """
    import app_user_stat.telemetry as tm

    class _On:
        telemetry_enabled = True

    class _StubClient:
        def bootstrap(self) -> None:
            pass

    monkeypatch.setattr(tm, "_schedule_on_qt_event_loop", lambda fn: False)
    monkeypatch.setattr(tm, "_TelemetryClient", _StubClient)
    monkeypatch.setitem(__import__("sys").modules, "advanced_config",
                        type("M", (), {"AdvancedConfig": lambda: _On()}))
    monkeypatch.setattr(tm, "_BOOTSTRAPPED", False)

    fired = []
    tm.bootstrap_telemetry(parent=None, on_ready=lambda: fired.append(True))
    assert fired == [True]


def test_on_ready_fires_when_reading_the_switch_raises(monkeypatch) -> None:
    """
    读取 telemetry_enabled 本身抛异常时 on_ready 也必须触发且只触发一次。

    这是此前完全没有测试覆盖的分支：AdvancedConfig() 构造或属性访问抛出
    时，run() 的 try/finally 保证 finally 里调用一次 on_ready；run()
    必须把异常挡在自己这一层（except 分支），不能让它继续往外逸出到
    bootstrap_telemetry() 的外层 except 再调一次，否则回调会被触发两次。

    on_ready must fire exactly once even if reading telemetry_enabled
    itself raises — previously zero coverage. run()'s own except must
    swallow the error locally so bootstrap_telemetry()'s outer except
    does not also invoke the callback, which would double-fire it.
    """
    import app_user_stat.telemetry as tm

    class _Boom:
        def __init__(self) -> None:
            raise RuntimeError("config read failed")

    monkeypatch.setattr(tm, "_schedule_on_qt_event_loop", lambda fn: False)
    monkeypatch.setitem(__import__("sys").modules, "advanced_config",
                        type("M", (), {"AdvancedConfig": _Boom}))
    monkeypatch.setattr(tm, "_BOOTSTRAPPED", False)

    fired = []
    tm.bootstrap_telemetry(parent=None, on_ready=lambda: fired.append(True))
    assert fired == [True]


def test_unreachable_endpoint_never_raises(monkeypatch) -> None:
    """
    端点不可达时不得抛异常——这正是 Countly 域名失效时的处境。

    当年那个死域名之所以数月无人察觉，是因为异常全被吞掉；吞异常本身是
    对的（统计不能拖垮启动），本测试守住这个行为不被「改成抛错好排查」。

    An unreachable endpoint must never raise: swallowing is correct here,
    since telemetry must never break startup.
    """
    import app_user_stat.telemetry as tm

    def _boom(*args, **kwargs):
        raise OSError("Could not resolve host")

    monkeypatch.setattr(tm.request, "urlopen", _boom)
    client = tm._TelemetryClient()
    state = {"device_id": "install-abc", "install_reported_at": None, "last_heartbeat_at": None}
    # 不应抛出任何异常
    client._send_due_events(state, ["app_start"])
