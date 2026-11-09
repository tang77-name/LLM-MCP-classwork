# -*- coding: utf-8 -*-
"""组合根（Composition Root）：装配 core 的各个部件。

UI 层只需要 ``AgentService`` 一个对象即可获得全部业务能力，
不必了解 LLM 客户端 / 工具注册表 / 配置加载的内部结构。
这也让未来新增 Web / Desktop 界面时无需重复装配代码。
"""

from __future__ import annotations

import os
from pathlib import Path
from threading import Thread
from typing import List, Optional

from ..config import Settings, load_settings
from ..llm import ChatClient
from ..mcp import MCP_CONFIG_FILENAME, MCPManager, MCPServerStatus
from ..market import InstallResult, SkillMarket, UninstallResult
from ..sessions import SessionRecord, SessionStore
from ..skills import SkillCatalog, discover_skills
from ..tools import (
    ToolRegistry,
    build_default_registry,
    register_create_skill_tool,
    register_recall_tool,
    register_skill_tool,
)
from .agents_md import load_agents_md
from .prompts import SYSTEM_PROMPT, build_system_prompt
from .session import AgentSession


class AgentService:
    """应用服务：配置 + LLM 客户端 + 工具 / skill / 市场 + 会话工厂。"""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        *,
        registry: Optional[ToolRegistry] = None,
        mcp_async: bool = False,
    ) -> None:
        self._settings = settings or load_settings()
        # 以程序启动时的工作目录作为项目根目录，初始化阶段只扫描 skill 元信息
        self._project_root = Path(os.getcwd()).resolve()
        self._skills = discover_skills(self._project_root)
        self._market = SkillMarket(
            self._project_root,
            self._settings.market_directory,
        )
        #: AGENTS.md 项目规范（存在时在会话首次加载时注入一次，全程复用）
        self._agents_md = load_agents_md(self._project_root)
        #: 持久化会话仓库按需创建（CLI/TUI 不产生 .zagent 目录副作用）
        self._sessions: Optional[SessionStore] = None
        self._system_prompt = build_system_prompt(
            SYSTEM_PROMPT, self._skills.entries_for_prompt()
        )
        self._client = ChatClient(
            chat_url=self._settings.chat_url,
            api_key=self._settings.api_key,
            model=self._settings.model_name,
            temperature=self._settings.temperature,
            timeout=self._settings.timeout,
        )
        if registry is None:
            self._registry = build_default_registry(
                self._settings.diagram_directory,
                search_api_key=self._settings.baidu_qianfan_api_key,
                search_timeout=self._settings.search_timeout,
                search_top_k=self._settings.search_top_k,
                search_snippet_chars=self._settings.search_snippet_chars,
                skill_catalog=lambda: self._skills,
                project_root=str(self._project_root),
                skill_refresh=self.refresh_skills,
                # [tools] 段参数（config.ini，缺省时取内置默认值）
                fetch_timeout=self._settings.fetch_timeout,
                fetch_max_bytes=self._settings.fetch_max_bytes,
                fetch_max_chars=self._settings.fetch_max_chars,
                file_read_chars=self._settings.file_read_chars,
                file_list_entries=self._settings.file_list_entries,
                cli_timeout=self._settings.cli_timeout,
                cli_max_timeout=self._settings.cli_max_timeout,
                cli_max_chars=self._settings.cli_max_chars,
            )
        else:
            self._registry = registry
            register_skill_tool(self._registry, lambda: self._skills)
            register_create_skill_tool(
                self._registry,
                lambda: self._skills,
                self.refresh_skills,
            )
        #: 上下文归档目录（/compact 写入、recallContext 检索）
        self._context_dir = self._project_root / ".agents" / "context"
        register_recall_tool(
            self._registry,
            self._context_dir,
            limit=self._settings.recall_limit,
            max_chars=self._settings.recall_max_chars,
        )
        #: MCP 客户端：读取项目根目录 .mcp.json，把外部工具并入同一注册表。
        #: 单个 server 连接失败只记状态（见 :meth:`mcp_statuses`），不影响本地工具。
        self._mcp = MCPManager(
            self._project_root / MCP_CONFIG_FILENAME,
            connect_timeout=float(self._settings.mcp_connect_timeout),
            call_timeout=float(self._settings.mcp_call_timeout),
        )
        #: MCP 连接线程（mcp_async=True 时后台加载，UI 先进入再动态刷新）
        self._mcp_thread: Optional[Thread] = None
        if mcp_async:
            self._mcp_thread = Thread(
                target=self._mcp.connect,
                args=(self._registry,),
                name="zagent-mcp-connect",
                daemon=True,
            )
            self._mcp_thread.start()
        else:
            self._mcp.connect(self._registry)

    # -- 访问器 -------------------------------------------------------------
    @property
    def settings(self) -> Settings:
        return self._settings

    @property
    def client(self) -> ChatClient:
        return self._client

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    @property
    def skills(self) -> SkillCatalog:
        return self._skills

    @property
    def market(self) -> SkillMarket:
        return self._market

    @property
    def mcp(self) -> MCPManager:
        """MCP 客户端管理器（连接状态、工具映射、关闭入口）。"""
        return self._mcp

    def mcp_statuses(self) -> List[MCPServerStatus]:
        """每个 MCP Server 的连接状态（含错误信息）。"""
        return self._mcp.statuses

    @property
    def mcp_loading(self) -> bool:
        """MCP 是否仍在后台加载（``mcp_async=True`` 时先 true后 false）。"""
        thread = self._mcp_thread
        return thread is not None and thread.is_alive()

    def wait_mcp(self, timeout: float = 120.0) -> bool:
        """等待后台 MCP 连接完成（UI 可据此刷新 MCP 区域）。

        :param timeout: 最长等待秒数
        :return: ``True`` 表示连接已结束（无论成功与否）
        """
        thread = self._mcp_thread
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    @property
    def agents_md(self) -> Optional[str]:
        """AGENTS.md 项目规范内容（当前目录不存在该文件时为 None）。"""
        return self._agents_md

    @property
    def project_root(self) -> Path:
        return self._project_root

    @property
    def sessions(self) -> SessionStore:
        """持久化会话仓库（Web 前端的会话树数据源，首次访问时创建）。"""
        if self._sessions is None:
            self._sessions = SessionStore(
                self._project_root / ".zagent" / "sessions"
            )
        return self._sessions

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    def refresh_skills(self) -> SkillCatalog:
        """重新扫描 skills 目录并重建 System Prompt（市场安装后调用）。"""
        self._skills = discover_skills(self._project_root)
        self._system_prompt = build_system_prompt(
            SYSTEM_PROMPT, self._skills.entries_for_prompt()
        )
        return self._skills

    def install_market_skill(
        self, name: str, *, force: bool = False
    ) -> InstallResult:
        """安装本地市场技能，并立即刷新可用 skill 清单。"""
        result = self._market.install(name, force=force)
        self.refresh_skills()
        return result

    def uninstall_skill(self, name: str) -> UninstallResult:
        """卸载已安装技能，并立即刷新可用 skill 清单。"""
        result = self._market.uninstall(name)
        self.refresh_skills()
        return result

    def describe(self) -> dict:
        """UI 展示用的环境信息。"""
        info = self._settings.masked()
        info["tools"] = self._registry.names()
        info["skills"] = self._skills.names()
        info["skills_dir"] = str(self._skills.skills_dir)
        info["market_dir"] = str(self._market.market_dir)
        info["mcp"] = self._mcp.describe()
        info["mcp_loading"] = self.mcp_loading
        info["context_dir"] = str(self._context_dir)
        info["context_window"] = self._settings.context_window
        #: AGENTS.md 是否已注入（路径；未配置时为空字符串）
        info["agents_md"] = (
            str(self._project_root / "AGENTS.md") if self._agents_md else ""
        )
        return info

    def shutdown(self) -> None:
        """释放外部资源（MCP 连接与子进程），进程退出前调用。"""
        # 先等后台连接线程收尾，避免“边连边关”留下僵尸子进程
        if self._mcp_thread is not None:
            self.wait_mcp(timeout=10.0)
        self._mcp.close()

    # -- 会话工厂 -----------------------------------------------------------
    def new_session(self, system_prompt: Optional[str] = None) -> AgentSession:
        """创建一个新的对话会话（多个界面可各自持有独立会话）。

        ``system_prompt`` 省略时使用内置提示词 + 当前可用 skill 清单。
        当前目录存在 ``AGENTS.md`` 时，会话历史中会一次性注入其内容。
        """
        prompt = self._system_prompt if system_prompt is None else system_prompt
        return AgentSession(
            self._client, self._registry, self._settings, prompt,
            context_dir=self._context_dir,
            agents_md=self._agents_md,
        )

    # -- 持久化会话（Web 前端） --------------------------------------------
    def list_sessions(
        self, *, include_archived: bool = False
    ) -> list[SessionRecord]:
        return self.sessions.list(
            str(self._project_root), include_archived=include_archived
        )

    def create_session(self, title: str = "") -> SessionRecord:
        """新建一条持久化会话记录。"""
        return self.sessions.create(
            workspace=str(self._project_root), title=title
        )

    def get_session(self, session_id: str) -> Optional[SessionRecord]:
        return self.sessions.get(session_id)

    def open_session(self, session_id: str) -> Optional[AgentSession]:
        """打开一条已存会话：重建历史并绑定持久化。"""
        record = self.sessions.get(session_id)
        if record is None:
            return None
        session = self.new_session()
        session.load_history(record.messages)
        session.attach_store(self.sessions, record.id)
        return session

    def save_session(
        self,
        messages,
        *,
        record_id: Optional[str] = None,
        title: str = "",
    ) -> SessionRecord:
        """保存（新建或更新）一条持久化会话记录。

        :param messages: 当前会话历史（不可持久化的角色会自动丢弃）
        :param record_id: 已有记录 id 时更新该记录；未知或省略时新建
        :param title: 可选标题（新建时省略则按首条用户消息自动生成）
        """
        store = self.sessions
        history = [dict(item) for item in (messages or [])]
        if record_id:
            saved = store.save(str(record_id), history, title=(title or None))
            if saved is not None:
                return saved
        return store.create(
            workspace=str(self._project_root),
            title=title,
            messages=history,
        )


__all__ = ["AgentService"]
