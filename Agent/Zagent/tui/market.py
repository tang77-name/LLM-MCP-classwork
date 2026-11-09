# -*- coding: utf-8 -*-
"""TUI 本地技能市场（ModalScreen）。

提供搜索、列表、详情、安装 / 卸载 / 刷新；所有业务委托给
:class:`core.market.SkillMarket`，本模块只负责交互与渲染。
"""

from __future__ import annotations

from typing import Callable, Optional

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Static

from core.agent import AgentService
from core.market import MarketListing, MarketPackage

_HEADING = "bold #4aa5ff"


class ConfirmScreen(ModalScreen[bool]):
    """通用确认弹窗。"""

    DEFAULT_CSS = """
    ConfirmScreen {
        align: center middle;
        background: $background 70%;
    }
    #confirm-panel {
        width: 70;
        max-width: 90%;
        height: auto;
        border: solid $warning;
        background: $surface;
        padding: 1 2;
    }
    #confirm-title { height: auto; text-style: bold; margin-bottom: 1; }
    #confirm-message { height: auto; margin-bottom: 1; }
    #confirm-actions { height: 3; align-horizontal: right; }
    #confirm-actions Button { margin-left: 1; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "取消", show=False),
    ]

    def __init__(self, message: str, title: str = "确认操作") -> None:
        super().__init__()
        self._message = message
        self._title = title

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-panel"):
            yield Static(Text(self._title, style="bold"), id="confirm-title")
            yield Static(self._message, id="confirm-message")
            with Horizontal(id="confirm-actions"):
                yield Button("取消 (Esc)", id="confirm-cancel")
                yield Button("确定", variant="error", id="confirm-ok")

    @on(Button.Pressed, "#confirm-cancel")
    def _cancel(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#confirm-ok")
    def _ok(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class MarketScreen(ModalScreen[None]):
    """本地技能市场弹窗。"""

    DEFAULT_CSS = """
    MarketScreen {
        align: center middle;
        background: $background 70%;
    }
    #market-panel {
        width: 110;
        max-width: 96%;
        height: 90%;
        max-height: 44;
        border: solid $accent;
        background: $surface;
        padding: 1 2;
    }
    #market-title { height: auto; text-style: bold; margin-bottom: 1; }
    #market-meta { height: auto; color: $text-muted; margin-bottom: 1; }
    #market-search { height: 3; margin-bottom: 1; }
    #market-body { height: 1fr; }
    #market-list-wrap {
        width: 60%;
        height: 100%;
        border-right: solid $accent;
        padding-right: 1;
    }
    #market-table { height: 1fr; }
    #market-detail {
        width: 40%;
        height: 100%;
        padding: 0 0 0 1;
    }
    #market-detail-text { height: auto; }
    #market-actions { height: 3; margin-top: 1; }
    #market-actions Button { margin-right: 1; }
    """

    BINDINGS = [
        Binding("escape", "close", "关闭", show=False),
        Binding("f2", "close", "关闭", show=False),
        Binding("ctrl+r", "refresh", "刷新", show=False),
    ]

    def __init__(
        self,
        service: AgentService,
        *,
        on_change: Optional[Callable[[], None]] = None,
        initial_query: str = "",
    ) -> None:
        super().__init__()
        self.service = service
        self._on_change = on_change
        self._initial_query = initial_query
        self._query = initial_query
        self._listing: Optional[MarketListing] = None
        self._selected_name: Optional[str] = None

    # -- 组装 ---------------------------------------------------------------
    def compose(self) -> ComposeResult:
        with Vertical(id="market-panel"):
            yield Static(
                Text.assemble(("Zagent ", "bold"), ("Market", _HEADING)),
                id="market-title",
            )
            yield Static("", id="market-meta")
            yield Input(
                value=self._initial_query,
                placeholder="搜索技能：名称 / 描述 / 分类 / 标签（Enter 选中结果）",
                id="market-search",
            )
            with Horizontal(id="market-body"):
                with Vertical(id="market-list-wrap"):
                    yield DataTable(
                        id="market-table",
                        cursor_type="row",
                        zebra_stripes=True,
                    )
                with VerticalScroll(id="market-detail"):
                    yield Static("", id="market-detail-text")
            with Horizontal(id="market-actions"):
                yield Button("安装", id="market-install", variant="success")
                yield Button("卸载", id="market-uninstall", variant="warning")
                yield Button("刷新 (Ctrl+R)", id="market-refresh")
                yield Button("关闭 (Esc)", id="market-close")

    def on_mount(self) -> None:
        table = self.query_one("#market-table", DataTable)
        table.add_columns("技能", "版本", "状态", "分类")
        self._reload()
        self.query_one("#market-search", Input).focus()

    # -- 数据刷新 -----------------------------------------------------------
    def _reload(self, select: Optional[str] = None) -> None:
        listing = self.service.market.search(self._query)
        self._listing = listing
        table = self.query_one("#market-table", DataTable)
        table.clear(columns=False)

        installed = sum(1 for package in listing.packages if package.installed)
        market_count = sum(1 for package in listing.packages if package.in_market)
        index_by_name: dict[str, int] = {}
        for index, package in enumerate(listing.packages):
            index_by_name[package.name] = index
            table.add_row(
                Text(package.name, style="bold"),
                package.version or "-",
                Text(
                    "已安装" if package.installed else "可安装",
                    style="green" if package.installed else "dim",
                ),
                package.category,
                key=package.name,
            )

        meta = Text.assemble(
            ("市场目录 ", "dim"), (str(listing.market_dir), ""),
            ("\n展示 ", "dim"), (f"{len(listing.packages)} 个", "bold"),
            ("  ·  市场包 ", "dim"), (f"{market_count} 个", "bold"),
            ("  ·  已安装 ", "dim"), (f"{installed} 个", "bold"),
            ("  ·  安装目录 ", "dim"), (str(listing.skills_dir), ""),
        )
        for warning in listing.warnings[:2]:
            meta.append("\n! ", style="yellow")
            meta.append(warning, style="yellow")
        self.query_one("#market-meta", Static).update(meta)

        if not listing.packages:
            self._selected_name = None
            self.query_one("#market-detail-text", Static).update(
                Text(
                    "没有匹配的技能。\n\n"
                    "把技能包放到市场目录下，例如：\n"
                    f"{listing.market_dir}\\<name>\\skill.md\n"
                    "然后点击“刷新”。",
                    style="dim",
                )
            )
            self._update_buttons()
            return

        target = select or self._selected_name
        if target not in index_by_name:
            target = listing.packages[0].name
        self._show_detail(target)
        try:
            table.move_cursor(row=index_by_name[target], column=0, animate=False)
        except Exception:  # pragma: no cover - Textual 版本差异
            pass

    def _current_package(self) -> Optional[MarketPackage]:
        if not self._selected_name:
            return None
        return self.service.market.get(self._selected_name)

    def _show_detail(self, name: str) -> None:
        package = self.service.market.get(name)
        if package is None:
            self._selected_name = None
            self.query_one("#market-detail-text", Static).update(
                Text("技能不存在，可能已被移动。", style="red")
            )
            self._update_buttons()
            return

        self._selected_name = package.name
        text = Text()
        text.append(package.name, style=f"bold {_HEADING}")
        if package.version:
            text.append(f"  v{package.version}", style="dim")
        text.append("\n")
        text.append("状态  ", style="dim")
        text.append(package.status + "\n", style="green" if package.installed else "yellow")
        text.append("类型  ", style="dim")
        text.append(package.source_type + "\n")
        text.append("分类  ", style="dim")
        text.append(package.category + "\n")
        if package.author:
            text.append("作者  ", style="dim")
            text.append(package.author + "\n")
        if package.tags:
            text.append("标签  ", style="dim")
            text.append(", ".join(package.tags) + "\n")
        if package.homepage:
            text.append("主页  ", style="dim")
            text.append(package.homepage + "\n")
        text.append("来源  ", style="dim")
        text.append(str(package.directory) + "\n")
        if package.installed_directory:
            text.append("安装  ", style="dim")
            text.append(str(package.installed_directory) + "\n")
        text.append("\n说明\n", style="bold")
        text.append((package.description or "-") + "\n")
        files = package.files()
        text.append(f"\n文件 · {len(files)}\n", style="bold")
        for name in files:
            text.append(f"  - {name}\n", style="dim")
        self.query_one("#market-detail-text", Static).update(text)
        self._update_buttons()

    def _update_buttons(self) -> None:
        package = self._current_package()
        install = self.query_one("#market-install", Button)
        uninstall = self.query_one("#market-uninstall", Button)
        install.disabled = package is None or not package.in_market
        uninstall.disabled = package is None or not package.installed
        if package is None:
            install.label = "安装"
        elif not package.in_market:
            install.label = "本地技能"
        else:
            install.label = "重新安装" if package.installed else "安装"

    # -- 事件 ---------------------------------------------------------------
    @on(Input.Changed, "#market-search")
    def _on_search(self, event: Input.Changed) -> None:
        self._query = event.value.strip()
        self._reload()

    @on(Input.Submitted, "#market-search")
    def _on_search_submit(self) -> None:
        self.query_one("#market-table", DataTable).focus()

    @on(DataTable.RowHighlighted, "#market-table")
    def _on_row(self, event: DataTable.RowHighlighted) -> None:
        name = str(event.row_key.value)
        if name and name != self._selected_name:
            self._show_detail(name)

    @on(DataTable.RowSelected, "#market-table")
    def _on_row_selected(self, event: DataTable.RowSelected) -> None:
        name = str(event.row_key.value)
        if name:
            self._show_detail(name)

    @on(Button.Pressed, "#market-install")
    def _install(self) -> None:
        package = self._current_package()
        if package is None:
            return
        if package.installed:
            self.app.push_screen(
                ConfirmScreen(
                    f"技能 {package.name!r} 已安装。\n"
                    "重新安装会覆盖现有目录中的文件，是否继续？",
                    title="覆盖安装",
                ),
                self._after_confirm_install,
            )
            return
        self._do_install(package.name, force=False)

    def _after_confirm_install(self, confirmed: bool) -> None:
        package = self._current_package()
        if confirmed and package is not None:
            self._do_install(package.name, force=True)

    def _do_install(self, name: str, *, force: bool) -> None:
        try:
            result = self.service.install_market_skill(name, force=force)
        except Exception as exc:
            self.notify(str(exc), title="安装失败", severity="error")
            return
        self._changed()
        self._reload(select=result.name)
        self.notify(
            f"已安装 {result.name} v{result.version or '-'}",
            title="市场",
            severity="information",
        )

    @on(Button.Pressed, "#market-uninstall")
    def _uninstall(self) -> None:
        package = self._current_package()
        if package is None or not package.installed:
            return
        self.app.push_screen(
            ConfirmScreen(
                f"确定卸载技能 {package.name!r} 吗？\n"
                f"将删除：{package.installed_directory or ''}",
                title="卸载技能",
            ),
            self._after_confirm_uninstall,
        )

    def _after_confirm_uninstall(self, confirmed: bool) -> None:
        package = self._current_package()
        if not confirmed or package is None:
            return
        try:
            result = self.service.uninstall_skill(package.name)
        except Exception as exc:
            self.notify(str(exc), title="卸载失败", severity="error")
            return
        self._changed()
        self._reload(select=result.name)
        self.notify(f"已卸载 {result.name}", title="市场", severity="warning")

    @on(Button.Pressed, "#market-refresh")
    def _refresh_button(self) -> None:
        self.action_refresh()

    @on(Button.Pressed, "#market-close")
    def _close_button(self) -> None:
        self.dismiss()

    def action_refresh(self) -> None:
        self._reload(select=self._selected_name)

    def action_close(self) -> None:
        self.dismiss()

    def _changed(self) -> None:
        if self._on_change is not None:
            self._on_change()


__all__ = ["ConfirmScreen", "MarketScreen"]
