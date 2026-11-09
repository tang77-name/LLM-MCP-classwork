# -*- coding: utf-8 -*-
"""内嵌的 Codex 风格单页界面。

所有资源（HTML / CSS / JS）内联在一个字符串中，便于以单文件方式分发，
也避免 Web 前端引入 Node 构建链。页面通过 REST 接口与
:mod:`web.server` 通信。
"""

from __future__ import annotations

PAGE = r"""<!doctype html>
<html lang="zh-CN" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Zagent · Codex</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='14' fill='%234c8dff'/%3E%3Ctext x='32' y='45' font-size='40' font-family='Arial' font-weight='bold' text-anchor='middle' fill='white'%3EZ%3C/text%3E%3C/svg%3E">
<style>
  :root {
    color-scheme: dark;
    --bg: #0f1012;
    --bg-elev: #15161a;
    --side: #0b0c0e;
    --panel: #181a1e;
    --panel-2: #20232a;
    --panel-3: #282c34;
    --border: #2a2d35;
    --border-soft: #22252c;
    --text: #ececf1;
    --muted: #8b8f9a;
    --faint: #5f6470;
    --accent: #4c8dff;
    --accent-soft: rgba(76, 141, 255, .14);
    --accent-2: #6ea8ff;
    --user: #1d2738;
    --danger: #ff6b6b;
    --danger-soft: rgba(255, 107, 107, .12);
    --warn: #e8b339;
    --ok: #3fbf7f;
    --code: #0a0c0f;
    --shadow: 0 18px 50px rgba(0, 0, 0, .38);
    --radius: 12px;
  }
  :root[data-theme="light"] {
    color-scheme: light;
    --bg: #f7f7f8;
    --bg-elev: #ffffff;
    --side: #efeff2;
    --panel: #ffffff;
    --panel-2: #f2f3f5;
    --panel-3: #e8eaee;
    --border: #dcdde2;
    --border-soft: #e6e7eb;
    --text: #1e2329;
    --muted: #68707c;
    --faint: #98a0ab;
    --accent: #2563eb;
    --accent-soft: rgba(37, 99, 235, .1);
    --accent-2: #1d4ed8;
    --user: #e8eefc;
    --danger: #d92d20;
    --danger-soft: rgba(217, 45, 32, .08);
    --warn: #b7791f;
    --ok: #178a52;
    --code: #f4f5f7;
    --shadow: 0 18px 50px rgba(20, 24, 31, .16);
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; }
  body {
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font: 14px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
      "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
    overflow: hidden;
  }
  button, input, textarea { font: inherit; color: inherit; }
  button { cursor: pointer; }
  a { color: var(--accent-2); text-decoration: none; }
  a:hover { text-decoration: underline; }
  ::selection { background: var(--accent-soft); }
  ::-webkit-scrollbar { width: 9px; height: 9px; }
  ::-webkit-scrollbar-thumb { background: var(--panel-3); border-radius: 9px; }
  ::-webkit-scrollbar-track { background: transparent; }

  .app { display: flex; height: 100vh; min-width: 0; }

  /* ---------------- 侧栏 ---------------- */
  .sidebar {
    width: 274px;
    flex: 0 0 274px;
    background: var(--side);
    border-right: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    min-height: 0;
    transition: margin-left .18s ease;
  }
  body.sidebar-collapsed .sidebar { margin-left: -274px; }
  .side-head {
    display: flex; align-items: center; gap: 8px;
    padding: 13px 12px 9px;
  }
  .brand { display: flex; align-items: center; gap: 8px; min-width: 0; flex: 1; }
  .logo {
    width: 24px; height: 24px; border-radius: 7px;
    display: grid; place-items: center;
    background: linear-gradient(145deg, var(--accent), #7c5cff);
    color: #fff; font-weight: 800; font-size: 13px;
    box-shadow: 0 4px 14px rgba(76, 141, 255, .3);
  }
  .brand-name { font-weight: 700; letter-spacing: .2px; }
  .brand-tag {
    font-size: 10px; text-transform: uppercase; letter-spacing: .8px;
    color: var(--muted); border: 1px solid var(--border);
    padding: 1px 5px; border-radius: 5px;
  }
  .icon-btn {
    border: 1px solid transparent; background: transparent;
    color: var(--muted); width: 30px; height: 30px; border-radius: 8px;
    display: inline-grid; place-items: center; font-size: 15px;
    transition: background .15s, color .15s;
  }
  .icon-btn:hover { background: var(--panel-2); color: var(--text); }

  .side-search { position: relative; padding: 4px 12px 8px; }
  .search-box { position: relative; }
  .search-box svg {
    position: absolute; left: 10px; top: 50%; transform: translateY(-50%);
    width: 14px; height: 14px; color: var(--faint); pointer-events: none;
  }
  #search {
    width: 100%; background: var(--panel); border: 1px solid var(--border-soft);
    border-radius: 8px; padding: 7px 10px 7px 31px; outline: none;
    font-size: 12.5px; transition: border-color .15s, background .15s;
  }
  #search:focus { border-color: var(--accent); background: var(--bg-elev); }
  #search::placeholder { color: var(--faint); }

  .search-results {
    position: absolute; left: 12px; right: 12px; top: 44px; z-index: 40;
    background: var(--bg-elev); border: 1px solid var(--border);
    border-radius: 10px; box-shadow: var(--shadow); overflow: hidden;
    max-height: 55vh; overflow-y: auto;
  }
  .search-results .sr-empty { padding: 14px; color: var(--muted); font-size: 12.5px; }
  .sr-item { padding: 9px 11px; border-bottom: 1px solid var(--border-soft); cursor: pointer; }
  .sr-item:last-child { border-bottom: 0; }
  .sr-item:hover { background: var(--panel-2); }
  .sr-title { font-size: 12.5px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .sr-snippet {
    margin-top: 2px; font-size: 11.5px; color: var(--muted);
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
    overflow: hidden;
  }

  .new-task {
    margin: 2px 12px 10px; border: 1px solid var(--border);
    background: var(--panel-2); color: var(--text);
    border-radius: 9px; padding: 8px 11px; text-align: left;
    display: flex; align-items: center; gap: 9px; font-size: 13px;
    transition: background .15s, border-color .15s;
  }
  .new-task:hover { background: var(--panel-3); border-color: var(--accent); }
  .new-task .plus {
    width: 18px; height: 18px; border-radius: 5px; display: grid; place-items: center;
    background: var(--accent-soft); color: var(--accent-2); font-weight: 700;
  }
  .new-task kbd { margin-left: auto; }

  kbd {
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 10px; color: var(--faint); border: 1px solid var(--border);
    border-bottom-width: 2px; border-radius: 5px; padding: 0 4px; background: var(--panel);
  }

  .tree { flex: 1; overflow-y: auto; padding: 0 8px 10px; min-height: 0; }
  .tree-section { margin-top: 8px; }
  .tree-label {
    display: flex; align-items: center; gap: 6px;
    padding: 5px 6px; font-size: 10.5px; text-transform: uppercase;
    letter-spacing: .7px; color: var(--faint); user-select: none;
  }
  .tree-label .count { margin-left: auto; font-size: 10px; }
  .sess {
    position: relative; display: flex; align-items: flex-start; gap: 7px;
    padding: 7px 7px 7px 9px; border-radius: 8px; cursor: pointer;
    border: 1px solid transparent; min-width: 0;
  }
  .sess:hover { background: var(--panel); }
  .sess.active { background: var(--panel-2); border-color: var(--border); }
  .sess-dot {
    width: 7px; height: 7px; border-radius: 50%; margin-top: 7px;
    background: transparent; flex: 0 0 7px;
  }
  .sess-dot.unread { background: var(--accent); }
  .sess-dot.running {
    background: var(--ok); animation: pulse 1.1s ease-in-out infinite;
  }
  @keyframes pulse { 0%,100% { opacity: 1; transform: scale(1); } 50% { opacity: .35; transform: scale(.82); } }
  .sess-body { flex: 1; min-width: 0; }
  .sess-title {
    font-size: 12.8px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .sess.active .sess-title { color: var(--text); }
  .sess-meta { font-size: 10.5px; color: var(--faint); margin-top: 1px; }
  .sess-more {
    opacity: 0; border: 0; background: transparent; color: var(--muted);
    border-radius: 5px; width: 20px; height: 20px; line-height: 1;
    flex: 0 0 20px; margin-top: 2px;
  }
  .sess:hover .sess-more, .sess.active .sess-more { opacity: 1; }
  .sess-more:hover { background: var(--panel-3); color: var(--text); }
  .sess.archived .sess-title { color: var(--muted); }

  .side-foot {
    border-top: 1px solid var(--border-soft); padding: 7px 10px;
    display: flex; align-items: center; gap: 4px; flex-wrap: wrap;
  }
  .foot-btn {
    border: 0; background: transparent; color: var(--muted);
    font-size: 11.5px; padding: 4px 6px; border-radius: 6px;
  }
  .foot-btn:hover { background: var(--panel-2); color: var(--text); }
  .foot-btn.on { color: var(--accent-2); background: var(--accent-soft); }
  .conn {
    margin-left: auto; width: 7px; height: 7px; border-radius: 50%;
    background: var(--ok); box-shadow: 0 0 0 3px rgba(63, 191, 127, .12);
  }
  .conn.bad { background: var(--danger); box-shadow: 0 0 0 3px rgba(255, 107, 107, .12); }

  /* ---------------- 主区域 ---------------- */
  .main { flex: 1; min-width: 0; display: flex; flex-direction: column; background: var(--bg); position: relative; }
  .topbar {
    height: 54px; flex: 0 0 54px; border-bottom: 1px solid var(--border-soft);
    display: flex; align-items: center; gap: 10px; padding: 0 14px;
    background: color-mix(in srgb, var(--bg) 84%, transparent);
    backdrop-filter: blur(10px); z-index: 10;
  }
  .topbar .title-wrap { flex: 1; min-width: 0; display: flex; align-items: baseline; gap: 9px; }
  #conv-title {
    margin: 0; font-size: 14.5px; font-weight: 650; letter-spacing: .1px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .meta { font-size: 11px; color: var(--faint); white-space: nowrap; }
  .top-actions { display: flex; align-items: center; gap: 4px; }
  .badge {
    font-size: 10.5px; color: var(--muted); background: var(--panel);
    border: 1px solid var(--border-soft); border-radius: 999px; padding: 2px 9px;
    white-space: nowrap;
  }

  .chat { flex: 1; overflow-y: auto; position: relative; min-height: 0; scroll-behavior: smooth; }
  .messages {
    max-width: 880px; margin: 0 auto; padding: 26px 34px 160px 56px;
    display: flex; flex-direction: column; gap: 18px;
  }
  .turn-rail {
    position: absolute; left: 14px; top: 26px; width: 24px;
    display: flex; flex-direction: column; align-items: center; gap: 7px;
    z-index: 3; max-height: calc(100vh - 250px); overflow-y: auto;
    scrollbar-width: none;
  }
  .turn-rail::-webkit-scrollbar { display: none; }
  .turn {
    width: 18px; height: 18px; border-radius: 6px; border: 1px solid transparent;
    background: transparent; padding: 0; display: grid; place-items: center;
  }
  .turn span {
    width: 6px; height: 6px; border-radius: 50%; background: var(--faint);
    transition: transform .12s, background .12s;
  }
  .turn:hover span { background: var(--accent-2); transform: scale(1.35); }
  .turn.current span { background: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }

  .msg { position: relative; min-width: 0; animation: fade .18s ease; }
  @keyframes fade { from { opacity: 0; transform: translateY(3px); } to { opacity: 1; transform: none; } }
  .msg-label {
    display: flex; align-items: center; gap: 7px;
    font-size: 11px; color: var(--faint); margin-bottom: 5px; letter-spacing: .3px;
  }
  .avatar {
    width: 18px; height: 18px; border-radius: 6px; display: grid; place-items: center;
    font-size: 10px; font-weight: 800; color: #fff;
    background: linear-gradient(145deg, var(--accent), #7c5cff);
  }
  .msg.user { align-self: flex-end; max-width: 82%; }
  .msg.user .msg-label { justify-content: flex-end; }
  .msg.user .bubble {
    background: var(--user); border: 1px solid var(--border-soft);
    border-radius: 12px 12px 4px 12px; padding: 9px 13px;
    white-space: pre-wrap; word-break: break-word;
  }
  .msg.assistant { max-width: 100%; }
  .msg.assistant .bubble {
    padding: 2px 2px 2px 0; word-break: break-word; font-size: 14px;
  }
  .bubble p { margin: 0 0 10px; }
  .bubble p:last-child { margin-bottom: 0; }
  .bubble h3, .bubble h4, .bubble h5, .bubble h6 {
    margin: 18px 0 7px; line-height: 1.35; font-weight: 650;
  }
  .bubble h3 { font-size: 17px; }
  .bubble h4 { font-size: 15.5px; }
  .bubble h5, .bubble h6 { font-size: 14px; }
  .bubble ul, .bubble ol { margin: 8px 0 12px; padding-left: 22px; }
  .bubble li { margin: 3px 0; }
  .bubble blockquote {
    margin: 10px 0; padding: 3px 12px; color: var(--muted);
    border-left: 2px solid var(--border);
  }
  .bubble blockquote p { margin: 0; }
  .bubble code {
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 12.5px; background: var(--panel-2); border: 1px solid var(--border-soft);
    border-radius: 5px; padding: 1px 5px;
  }
  .bubble pre {
    margin: 10px 0; padding: 12px 14px; overflow-x: auto;
    background: var(--code); border: 1px solid var(--border); border-radius: 9px;
  }
  .bubble pre code { background: none; border: 0; padding: 0; font-size: 12.5px; line-height: 1.55; }
  .bubble strong { font-weight: 650; }
  .streaming::after {
    content: "▍"; color: var(--accent); margin-left: 2px;
    animation: blink 1s step-end infinite;
  }
  @keyframes blink { 50% { opacity: 0; } }

  .tool {
    border: 1px solid var(--border); background: var(--panel);
    border-radius: 10px; overflow: hidden; margin: 8px 0;
  }
  .tool-head {
    display: flex; align-items: center; gap: 8px; padding: 7px 11px;
    cursor: pointer; user-select: none; min-width: 0;
  }
  .tool-head:hover { background: var(--panel-2); }
  .tool-ico {
    width: 18px; height: 18px; border-radius: 5px; flex: 0 0 18px;
    display: grid; place-items: center; font-size: 10px;
    background: var(--accent-soft); color: var(--accent-2);
  }
  .tool-name {
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 12px; font-weight: 600; white-space: nowrap;
  }
  .tool-status { font-size: 10.5px; color: var(--faint); margin-left: auto; white-space: nowrap; }
  .tool-status.ok { color: var(--ok); }
  .tool-status.bad { color: var(--danger); }
  .tool-body { display: none; border-top: 1px solid var(--border-soft); }
  .tool.open .tool-body { display: block; }
  .tool-block { padding: 8px 11px; }
  .tool-block + .tool-block { border-top: 1px dashed var(--border-soft); }
  .tool-block h4 {
    margin: 0 0 4px; font-size: 10px; text-transform: uppercase;
    letter-spacing: .8px; color: var(--faint); font-weight: 600;
  }
  .tool-block pre {
    margin: 0; max-height: 300px; overflow: auto; white-space: pre-wrap;
    word-break: break-word; font-family: ui-monospace, Menlo, Consolas, monospace;
    font-size: 12px; line-height: 1.55; color: var(--muted);
  }

  .notice {
    font-size: 12px; color: var(--muted); border-left: 2px solid var(--border);
    padding: 2px 0 2px 10px;
  }
  .notice.error { color: var(--danger); border-left-color: var(--danger); background: var(--danger-soft); padding: 8px 11px; border-radius: 0 8px 8px 0; }
  .notice.cancel { color: var(--warn); border-left-color: var(--warn); }
  .msg.highlight .bubble {
    outline: 2px solid var(--accent);
    outline-offset: 4px; border-radius: 10px;
    transition: outline-color .2s;
  }

  .empty {
    position: absolute; inset: 0; display: flex; flex-direction: column;
    align-items: center; justify-content: center; padding: 40px; text-align: center;
    pointer-events: none;
  }
  .empty.hidden { display: none; }
  .empty-logo {
    width: 46px; height: 46px; border-radius: 14px; display: grid; place-items: center;
    background: linear-gradient(145deg, var(--accent), #7c5cff); color: #fff;
    font-size: 22px; font-weight: 800; margin-bottom: 14px;
    box-shadow: 0 14px 40px rgba(76, 141, 255, .28);
  }
  .empty h2 { margin: 0 0 6px; font-size: 19px; font-weight: 650; }
  .empty p { margin: 0 0 22px; color: var(--muted); font-size: 13px; max-width: 430px; }
  .suggestions {
    display: grid; grid-template-columns: repeat(2, minmax(0, 190px)); gap: 9px;
    pointer-events: auto;
  }
  .sug {
    text-align: left; border: 1px solid var(--border); background: var(--panel);
    border-radius: 10px; padding: 10px 12px; color: var(--muted); font-size: 12.5px;
    transition: border-color .15s, background .15s, color .15s;
  }
  .sug:hover { border-color: var(--accent); background: var(--panel-2); color: var(--text); }

  .composer-wrap {
    position: absolute; left: 0; right: 0; bottom: 0; padding: 12px 24px 18px;
    background: linear-gradient(to top, var(--bg) 62%, transparent);
    pointer-events: none;
  }
  .composer {
    pointer-events: auto; max-width: 880px; margin: 0 auto;
    background: var(--bg-elev); border: 1px solid var(--border);
    border-radius: 13px; box-shadow: 0 10px 34px rgba(0, 0, 0, .18);
    padding: 8px 10px 7px 13px;
    transition: border-color .15s, box-shadow .15s;
  }
  .composer:focus-within { border-color: var(--accent); box-shadow: 0 10px 34px rgba(0, 0, 0, .22), 0 0 0 3px var(--accent-soft); }
  #composer {
    width: 100%; resize: none; border: 0; outline: none; background: transparent;
    min-height: 26px; max-height: 190px; padding: 4px 2px; line-height: 1.6;
    font-size: 14px;
  }
  #composer::placeholder { color: var(--faint); }
  .composer-bar { display: flex; align-items: center; gap: 8px; padding-top: 3px; }
  .hint { font-size: 10.5px; color: var(--faint); flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .composer-actions { display: flex; align-items: center; gap: 6px; }
  .send {
    width: 30px; height: 30px; border-radius: 8px; border: 0;
    background: var(--accent); color: #fff; display: grid; place-items: center;
    font-size: 15px; font-weight: 700; transition: opacity .15s, transform .1s;
  }
  .send:hover { transform: translateY(-1px); }
  .send:disabled { opacity: .35; transform: none; cursor: default; }
  .stop {
    border: 1px solid var(--border); background: var(--panel-2);
    color: var(--text); border-radius: 7px; font-size: 11.5px; padding: 4px 9px;
  }
  .stop:hover { border-color: var(--danger); color: var(--danger); }

  /* ---------------- 浮层 ---------------- */
  .hidden { display: none !important; }
  .menu {
    position: fixed; z-index: 80; min-width: 172px; padding: 5px;
    background: var(--bg-elev); border: 1px solid var(--border);
    border-radius: 10px; box-shadow: var(--shadow);
  }
  .menu button {
    display: flex; width: 100%; align-items: center; gap: 8px;
    border: 0; background: transparent; text-align: left;
    padding: 7px 9px; border-radius: 7px; font-size: 12.5px; color: var(--text);
  }
  .menu button:hover { background: var(--panel-2); }
  .menu button.danger { color: var(--danger); }
  .menu hr { border: 0; border-top: 1px solid var(--border-soft); margin: 4px 3px; }

  .modal {
    position: fixed; inset: 0; z-index: 90; background: rgba(0, 0, 0, .45);
    display: flex; align-items: center; justify-content: center; padding: 26px;
    backdrop-filter: blur(2px);
  }
  .modal-card {
    width: min(620px, 100%); max-height: 82vh; display: flex; flex-direction: column;
    background: var(--bg-elev); border: 1px solid var(--border);
    border-radius: 14px; box-shadow: var(--shadow); overflow: hidden;
  }
  .modal-head {
    display: flex; align-items: center; gap: 10px; padding: 13px 16px;
    border-bottom: 1px solid var(--border-soft); font-weight: 600;
  }
  .modal-head button { margin-left: auto; }
  .modal-body { padding: 16px; overflow-y: auto; font-size: 13px; }
  .modal-foot {
    padding: 11px 16px; border-top: 1px solid var(--border-soft);
    display: flex; justify-content: flex-end; gap: 8px;
  }
  .modal input[type="text"] {
    width: 100%; background: var(--panel); border: 1px solid var(--border);
    border-radius: 8px; padding: 8px 10px; outline: none;
  }
  .modal input[type="text"]:focus { border-color: var(--accent); }
  .btn {
    border: 1px solid var(--border); background: var(--panel-2); color: var(--text);
    border-radius: 8px; padding: 6px 13px; font-size: 12.5px;
  }
  .btn:hover { border-color: var(--accent); }
  .btn.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
  .btn.danger { background: var(--danger); border-color: var(--danger); color: #fff; }
  .about-grid { display: grid; grid-template-columns: 108px 1fr; gap: 7px 12px; }
  .about-grid dt { color: var(--muted); }
  .about-grid dd { margin: 0; word-break: break-all; }
  .about-section { margin-bottom: 18px; }
  .about-section h3 {
    margin: 0 0 8px; font-size: 11px; letter-spacing: .8px; text-transform: uppercase;
    color: var(--faint);
  }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; }
  .chip {
    font-size: 11.5px; border: 1px solid var(--border); background: var(--panel);
    padding: 2px 8px; border-radius: 999px; color: var(--muted);
  }
  .theme-row { display: flex; gap: 8px; }
  .theme-choice {
    flex: 1; border: 1px solid var(--border); background: var(--panel);
    border-radius: 9px; padding: 10px; text-align: left; color: var(--muted);
  }
  .theme-choice.on { border-color: var(--accent); color: var(--text); background: var(--accent-soft); }
  .theme-choice b { display: block; color: var(--text); font-size: 12.5px; }

  .toast {
    position: fixed; bottom: 96px; left: 50%; transform: translateX(-50%);
    z-index: 100; background: var(--panel-3); color: var(--text);
    border: 1px solid var(--border); border-radius: 9px; padding: 8px 14px;
    font-size: 12.5px; box-shadow: var(--shadow); max-width: 70vw;
  }
  .toast.bad { border-color: var(--danger); color: var(--danger); }

  @media (max-width: 760px) {
    .sidebar {
      position: fixed; inset: 0 auto 0 0; z-index: 60;
      box-shadow: var(--shadow);
    }
    body.sidebar-collapsed .sidebar { margin-left: -274px; }
    body:not(.sidebar-collapsed)::after {
      content: ""; position: fixed; inset: 0; background: rgba(0,0,0,.35); z-index: 50;
    }
    .topbar { padding: 0 8px; gap: 4px; }
    .meta, #model-badge, #fork-btn { display: none; }
    #conv-title { font-size: 14px; }
    .messages { padding: 20px 18px 150px 42px; }
    .turn-rail { left: 8px; }
    .hint { display: none; }
    .suggestions { grid-template-columns: 1fr; }
    .composer-wrap { padding: 10px 12px 14px; }
  }
</style>
</head>
<body>
<div class="app">
  <aside class="sidebar" id="sidebar">
    <div class="side-head">
      <div class="brand">
        <span class="logo">Z</span>
        <span class="brand-name">Zagent</span>
        <span class="brand-tag">Codex</span>
      </div>
      <button class="icon-btn" id="collapse-btn" title="收起侧栏">‹</button>
    </div>

    <div class="side-search">
      <div class="search-box">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="11" cy="11" r="7"></circle><path d="m20 20-3.5-3.5"></path>
        </svg>
        <input id="search" type="text" placeholder="搜索会话与消息…" autocomplete="off" spellcheck="false">
      </div>
      <div id="search-results" class="search-results hidden"></div>
    </div>

    <button class="new-task" id="new-task">
      <span class="plus">+</span>新建任务 <kbd>Ctrl K</kbd>
    </button>

    <div class="tree" id="tree"></div>

    <div class="side-foot">
      <button class="foot-btn" id="archived-btn">已归档</button>
      <button class="foot-btn" id="settings-btn">设置</button>
      <span class="conn" id="conn" title="已连接"></span>
    </div>
  </aside>

  <main class="main">
    <header class="topbar">
      <button class="icon-btn" id="sidebar-btn" title="显示 / 隐藏侧栏">☰</button>
      <div class="title-wrap">
        <h1 id="conv-title">新任务</h1>
        <span class="meta" id="conv-meta"></span>
      </div>
      <div class="top-actions">
        <span class="badge" id="model-badge">model</span>
        <button class="icon-btn" id="theme-btn" title="切换深浅色">◐</button>
        <button class="icon-btn" id="fork-btn" title="分叉当前会话">⑂</button>
        <button class="icon-btn" id="more-btn" title="会话操作">⋯</button>
      </div>
    </header>

    <div class="chat" id="chat">
      <div id="empty" class="empty">
        <div class="empty-logo">Z</div>
        <h2>今天想让 Zagent 做什么？</h2>
        <p>在工作区里继续之前的对话，或创建新任务。历史会自动保存，可随时搜索、置顶与分叉。</p>
        <div class="suggestions">
          <button class="sug" data-prompt="梳理一下当前项目的架构，并画出关键模块的 Mermaid 图。">梳理项目架构并画图</button>
          <button class="sug" data-prompt="帮我为最近的改动补一组测试，并说明覆盖了哪些边界情况。">为改动补测试</button>
          <button class="sug" data-prompt="把下面的需求写成一份正式的公司内部通知：">撰写内部通知</button>
          <button class="sug" data-prompt="总结这个工作区最近的会话，列出待办事项和风险。">总结会话待办</button>
        </div>
      </div>
      <div id="turn-rail" class="turn-rail"></div>
      <div id="messages" class="messages"></div>
    </div>

    <div class="composer-wrap">
      <div class="composer">
        <textarea id="composer" rows="1" placeholder="给 Zagent 发消息…"></textarea>
        <div class="composer-bar">
          <div class="hint" id="composer-hint">Enter 发送 · Shift+Enter 换行 · ↑/↓ 历史输入</div>
          <div class="composer-actions">
            <button class="stop hidden" id="stop-btn">■ 停止</button>
            <button class="send" id="send-btn" title="发送">↑</button>
          </div>
        </div>
      </div>
    </div>
  </main>
</div>

<div id="menu" class="menu hidden"></div>
<div id="modal" class="modal hidden"></div>
<div id="toast" class="toast hidden"></div>

<script>
"use strict";

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
};

const S = {
  state: null,
  sessions: [],
  activeId: null,
  record: null,
  seq: 0,
  running: new Set(),
  showArchived: false,
  streamEl: null,
  streamRaw: "",
  toolCards: new Map(),
  history: [],
  historyIndex: -1,
  draft: "",
  searchTimer: null,
  pollFailures: 0,
  stick: true,
  lastSidebarRefresh: 0,
  keepView: false,
};

/* ---------------- 基础工具 ---------------- */
function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function md(text) {
  const lines = String(text ?? "").split(/\r?\n/);
  const html = [];
  let listTag = null;

  const closeList = () => {
    if (listTag) { html.push("</" + listTag + ">"); listTag = null; }
  };

  for (let i = 0; i < lines.length;) {
    const line = lines[i];

    if (/^```/.test(line)) {
      closeList();
      const code = [];
      i += 1;
      while (i < lines.length && !/^```/.test(lines[i])) {
        code.push(lines[i]);
        i += 1;
      }
      i += 1;
      html.push("<pre><code>" + escapeHtml(code.join("\n")) + "</code></pre>");
      continue;
    }

    if (!line.trim()) { closeList(); i += 1; continue; }

    let match = line.match(/^(#{1,4})\s+(.*)$/);
    if (match) {
      closeList();
      const level = Math.min(6, match[1].length + 2);
      html.push("<h" + level + ">" + inlineMd(match[2]) + "</h" + level + ">");
      i += 1;
      continue;
    }

    match = line.match(/^\s*[-*+]\s+(.*)$/);
    if (match) {
      if (listTag !== "ul") { closeList(); html.push("<ul>"); listTag = "ul"; }
      html.push("<li>" + inlineMd(match[1]) + "</li>");
      i += 1;
      continue;
    }

    match = line.match(/^\s*\d+\.\s+(.*)$/);
    if (match) {
      if (listTag !== "ol") { closeList(); html.push("<ol>"); listTag = "ol"; }
      html.push("<li>" + inlineMd(match[1]) + "</li>");
      i += 1;
      continue;
    }

    match = line.match(/^>\s?(.*)$/);
    if (match) {
      closeList();
      html.push("<blockquote>" + inlineMd(match[1]) + "</blockquote>");
      i += 1;
      continue;
    }

    closeList();
    const paragraph = [line];
    i += 1;
    while (i < lines.length && lines[i].trim()
      && !/^(```|#{1,4}\s|>\s?|\s*[-*+]\s|\s*\d+\.\s)/.test(lines[i])) {
      paragraph.push(lines[i]);
      i += 1;
    }
    html.push("<p>" + paragraph.map((part) => inlineMd(part)).join("<br>") + "</p>");
  }

  closeList();
  return html.join("");
}

function inlineMd(text) {
  const codes = [];
  let out = escapeHtml(text).replace(/`([^`\n]+)`/g, (match, code) => {
    codes.push("<code>" + code + "</code>");
    return "\u0001" + (codes.length - 1) + "\u0001";
  });
  out = out
    .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
    .replace(/\[([^\]\n]+)\]\((https?:\/\/[^)\s]+)\)/g,
      '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
  return out.replace(/\u0001(\d+)\u0001/g, (match, index) => codes[Number(index)]);
}

function timeAgo(iso) {
  if (!iso) return "";
  const time = new Date(iso).getTime();
  if (!Number.isFinite(time)) return "";
  const diff = Math.max(0, Date.now() - time) / 1000;
  if (diff < 60) return "刚刚";
  if (diff < 3600) return Math.floor(diff / 60) + " 分钟前";
  if (diff < 86400) return Math.floor(diff / 3600) + " 小时前";
  if (diff < 86400 * 7) return Math.floor(diff / 86400) + " 天前";
  return new Date(iso).toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
}

function toast(message, bad) {
  const node = $("#toast");
  node.textContent = message;
  node.className = "toast" + (bad ? " bad" : "");
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => node.classList.add("hidden"), 3200);
}

async function api(path, options) {
  const opts = Object.assign({ headers: {} }, options || {});
  if (opts.body !== undefined && typeof opts.body !== "string") {
    opts.body = JSON.stringify(opts.body);
  }
  if (opts.body !== undefined) opts.headers["Content-Type"] = "application/json";
  const response = await fetch(path, opts);
  const text = await response.text();
  let data = {};
  if (text) {
    try { data = JSON.parse(text); } catch (_) { data = { raw: text }; }
  }
  if (!response.ok) throw new Error(data.error || ("HTTP " + response.status));
  return data;
}

function setConnection(ok) {
  const node = $("#conn");
  node.className = "conn" + (ok ? "" : " bad");
  node.title = ok ? "已连接" : "连接中断，正在重试";
}

/* ---------------- 主题 / 布局 ---------------- */
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme === "light" ? "light" : "dark";
  localStorage.setItem("zagent.theme", document.documentElement.dataset.theme);
}

function toggleSidebar() {
  document.body.classList.toggle("sidebar-collapsed");
  localStorage.setItem("zagent.sidebar",
    document.body.classList.contains("sidebar-collapsed") ? "0" : "1");
}

/* ---------------- 侧栏会话树 ---------------- */
function baseName(path) {
  if (!path) return "工作区";
  const parts = String(path).split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] || path;
}

function sessionRow(session) {
  const row = el("div", "sess" +
    (session.id === S.activeId ? " active" : "") +
    (session.archived ? " archived" : ""));
  row.dataset.id = session.id;
  const running = S.running.has(session.id);
  const dot = el("span", "sess-dot" + (running ? " running" : (session.unread ? " unread" : "")));
  const body = el("div", "sess-body");
  body.append(
    el("div", "sess-title", session.title || "新会话"),
    el("div", "sess-meta", timeAgo(session.updated_at) + (session.message_count ? " · " + session.message_count + " 条" : ""))
  );
  const more = el("button", "sess-more", "⋯");
  more.title = "会话操作";
  more.addEventListener("click", (event) => {
    event.stopPropagation();
    openSessionMenu(event, session);
  });
  row.append(dot, body, more);
  row.addEventListener("click", () => { closeMenu(); openSession(session.id); });
  return row;
}

function renderTree() {
  const tree = $("#tree");
  tree.innerHTML = "";
  const visible = S.sessions.filter((s) => !s.archived || S.showArchived);
  const pinned = visible.filter((s) => s.pinned && !s.archived);
  const normal = visible.filter((s) => !s.pinned && !s.archived);
  const archived = visible.filter((s) => s.archived);

  if (pinned.length) {
    const section = el("div", "tree-section");
    const label = el("div", "tree-label");
    label.append(el("span", "", "已固定"), el("span", "count", pinned.length));
    section.append(label);
    pinned.forEach((s) => section.append(sessionRow(s)));
    tree.append(section);
  }

  const section = el("div", "tree-section");
  const label = el("div", "tree-label");
  label.append(el("span", "", "工作区 · " + baseName(S.state ? S.state.workspace : "")),
    el("span", "count", normal.length));
  section.append(label);
  if (!normal.length) {
    section.append(el("div", "sr-empty", "还没有会话"));
  } else {
    normal.forEach((s) => section.append(sessionRow(s)));
  }
  tree.append(section);

  if (S.showArchived) {
    const archivedSection = el("div", "tree-section");
    const archivedLabel = el("div", "tree-label");
    archivedLabel.append(el("span", "", "已归档"), el("span", "count", archived.length));
    archivedSection.append(archivedLabel);
    if (!archived.length) archivedSection.append(el("div", "sr-empty", "没有归档会话"));
    archived.forEach((s) => archivedSection.append(sessionRow(s)));
    tree.append(archivedSection);
  }
}

async function refreshSessions() {
  try {
    const data = await api("/api/sessions?archived=1");
    S.sessions = data.sessions || [];
    renderTree();
    setConnection(true);
  } catch (error) {
    setConnection(false);
  }
}

async function refreshRuntime() {
  try {
    const state = await api("/api/state");
    S.state = state;
    $("#model-badge").textContent = state.model || "model";
    $("#model-badge").title = state.base_url || "";
    const next = new Set(state.running || []);
    const changed = next.size !== S.running.size
      || [...next].some((id) => !S.running.has(id));
    S.running = next;
    if (changed) renderTree();
  } catch (_) { /* 下一轮重试 */ }
}

/* ---------------- 会话操作菜单 ---------------- */
function closeMenu() { $("#menu").classList.add("hidden"); }

function menuItem(label, handler, danger) {
  const button = el("button", danger ? "danger" : "", label);
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    closeMenu();
    handler();
  });
  return button;
}

function openSessionMenu(event, session) {
  const menu = $("#menu");
  menu.innerHTML = "";
  menu.append(
    menuItem("重命名", () => renameSession(session)),
    menuItem(session.pinned ? "取消置顶" : "置顶", () => patchSession(session.id, { pinned: !session.pinned })),
    menuItem(session.unread ? "标记已读" : "标记未读", () => patchSession(session.id, { unread: !session.unread })),
    menuItem("复制会话内容", () => copyTranscript(session.id)),
    menuItem("分叉会话", () => forkSession(session.id)),
    menuItem(session.archived ? "取消归档" : "归档", () => patchSession(session.id, { archived: !session.archived }))
  );
  menu.append(document.createElement("hr"));
  menu.append(menuItem("删除会话", () => deleteSession(session), true));
  menu.classList.remove("hidden");
  const width = 190;
  const left = Math.min(event.clientX, window.innerWidth - width - 12);
  const top = Math.min(event.clientY + 4, window.innerHeight - menu.offsetHeight - 12);
  menu.style.left = Math.max(8, left) + "px";
  menu.style.top = Math.max(8, top) + "px";
}

async function patchSession(id, fields) {
  try {
    const updated = await api("/api/sessions/" + id, { method: "PATCH", body: fields });
    const index = S.sessions.findIndex((s) => s.id === id);
    if (index >= 0) S.sessions[index] = Object.assign({}, S.sessions[index], updated);
    if (id === S.activeId && S.record) Object.assign(S.record, updated);
    renderTree();
    if (id === S.activeId) renderHeader();
  } catch (error) { toast(error.message, true); }
}

async function renameSession(session) {
  const value = await askText("重命名会话", session.title || "");
  if (value === null) return;
  await patchSession(session.id, { title: value.trim() || session.title });
}

async function deleteSession(session) {
  const ok = await confirmAction("删除会话", "将永久删除「" + (session.title || "新会话") + "」及其全部消息，此操作不可撤销。");
  if (!ok) return;
  try {
    await api("/api/sessions/" + session.id, { method: "DELETE" });
    S.sessions = S.sessions.filter((s) => s.id !== session.id);
    if (S.activeId === session.id) {
      S.activeId = null;
      const next = S.sessions.find((s) => !s.archived);
      if (next) await openSession(next.id);
      else await createSession();
    } else {
      renderTree();
    }
    toast("会话已删除");
  } catch (error) { toast(error.message, true); }
}

async function forkSession(id) {
  try {
    const record = await api("/api/sessions/" + id + "/fork", { method: "POST", body: {} });
    toast("已创建分叉会话");
    await refreshSessions();
    await openSession(record.id);
  } catch (error) { toast(error.message, true); }
}

async function copyTranscript(id) {
  try {
    const record = await api("/api/sessions/" + id);
    const lines = [];
    for (const message of record.messages || []) {
      if (message.role === "user") lines.push("你：\n" + (message.content || ""));
      else if (message.role === "assistant" && message.content) lines.push("Zagent：\n" + message.content);
      else if (message.role === "tool") lines.push("工具结果：\n" + (message.content || ""));
    }
    const text = lines.join("\n\n");
    if (!text) { toast("会话还没有可复制的内容"); return; }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
    } else {
      const area = el("textarea");
      area.value = text;
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.append(area);
      area.select();
      document.execCommand("copy");
      area.remove();
    }
    toast("已复制会话内容");
  } catch (error) { toast("复制失败：" + error.message, true); }
}

async function createSession() {
  const record = await api("/api/sessions", { method: "POST", body: {} });
  await refreshSessions();
  await openSession(record.id);
  return record;
}

/* ---------------- 会话主体 ---------------- */
function renderHeader() {
  const record = S.record || {};
  const title = record.title || "新任务";
  $("#conv-title").textContent = title;
  document.title = title + " · Zagent";
  $("#conv-meta").textContent = record.message_count
    ? record.message_count + " 条消息 · " + timeAgo(record.updated_at)
    : "";
  $("#fork-btn").disabled = !record.id;
  $("#more-btn").disabled = !record.id;
}

function clearMessagesView() {
  $("#messages").innerHTML = "";
  $("#turn-rail").innerHTML = "";
  S.streamEl = null;
  S.streamRaw = "";
  S.toolCards = new Map();
}

function scrollToBottom(force) {
  const chat = $("#chat");
  if (force || S.stick) chat.scrollTop = chat.scrollHeight;
}

function messageNode(role, index) {
  const node = el("div", "msg " + role);
  if (index !== undefined) node.dataset.index = String(index);
  const label = el("div", "msg-label");
  if (role === "user") {
    label.append(el("span", "", "你"));
  } else {
    const avatar = el("span", "avatar", "Z");
    label.append(avatar, el("span", "", "Zagent"));
  }
  node.append(label);
  return node;
}

function addTurnMarker(index, text) {
  const rail = $("#turn-rail");
  const button = el("button", "turn");
  button.dataset.index = String(index);
  button.title = (text || "").slice(0, 90) || "第 " + (rail.children.length + 1) + " 轮";
  button.append(el("span"));
  button.addEventListener("click", () => jumpToIndex(index));
  rail.append(button);
}

function jumpToIndex(index) {
  const target = document.querySelector('.msg[data-index="' + index + '"]');
  if (!target) return;
  target.scrollIntoView({ behavior: "smooth", block: "center" });
  target.classList.add("highlight");
  setTimeout(() => target.classList.remove("highlight"), 1200);
  railSetCurrent(index);
}

function railSetCurrent(index) {
  document.querySelectorAll(".turn").forEach((node) => {
    node.classList.toggle("current", Number(node.dataset.index) === Number(index));
  });
}

function renderToolCard(container, callId, name, args) {
  const card = el("div", "tool");
  const head = el("div", "tool-head");
  const ico = el("span", "tool-ico", "⚙");
  const title = el("span", "tool-name", name || "tool");
  const status = el("span", "tool-status", "运行中");
  head.append(ico, title, status);
  const body = el("div", "tool-body");
  const argsBlock = el("div", "tool-block");
  argsBlock.append(el("h4", "", "参数"));
  const argsPre = el("pre", "", prettyArgs(args));
  argsBlock.append(argsPre);
  body.append(argsBlock);
  const resultBlock = el("div", "tool-block hidden");
  resultBlock.append(el("h4", "", "结果"));
  const resultPre = el("pre", "");
  resultBlock.append(resultPre);
  body.append(resultBlock);
  head.addEventListener("click", () => card.classList.toggle("open"));
  card.append(head, body);
  container.append(card);
  const entry = { card, status, resultBlock, resultPre };
  if (callId) S.toolCards.set(callId, entry);
  return entry;
}

function prettyArgs(raw) {
  if (!raw) return "";
  try { return JSON.stringify(JSON.parse(raw), null, 2); }
  catch (_) { return String(raw); }
}

function renderRecord(record) {
  clearMessagesView();
  const messages = record.messages || [];
  let turnCount = 0;
  let lastAssistantNode = null;
  let lastToolContainer = null;

  messages.forEach((message, index) => {
    if (message.role === "system") return;
    if (message.role === "user") {
      const node = messageNode("user", index);
      const bubble = el("div", "bubble");
      bubble.textContent = message.content || "";
      node.append(bubble);
      $("#messages").append(node);
      addTurnMarker(index, message.content || "");
      turnCount += 1;
      lastAssistantNode = null;
      lastToolContainer = null;
      return;
    }
    if (message.role === "assistant") {
      if (message.content) {
        const node = messageNode("assistant", index);
        const bubble = el("div", "bubble");
        bubble.innerHTML = md(message.content);
        node.append(bubble);
        $("#messages").append(node);
        lastAssistantNode = bubble;
        lastToolContainer = bubble;
      } else {
        lastAssistantNode = null;
        lastToolContainer = null;
      }
      const calls = Array.isArray(message.tool_calls) ? message.tool_calls : [];
      for (const call of calls) {
        const fn = call.function || {};
        const target = lastToolContainer || $("#messages");
        renderToolCard(target, call.id, fn.name, fn.arguments);
      }
      return;
    }
    if (message.role === "tool") {
      const entry = S.toolCards.get(message.tool_call_id);
      if (entry) {
        entry.status.textContent = String(message.content || "").startsWith("错误：") ? "失败" : "完成";
        entry.status.className = "tool-status " + (String(message.content || "").startsWith("错误：") ? "bad" : "ok");
        entry.resultPre.textContent = message.content || "";
        entry.resultBlock.classList.remove("hidden");
      } else {
        const node = el("div", "notice", "工具结果：" + (message.content || ""));
        $("#messages").append(node);
      }
    }
  });

  $("#empty").classList.toggle("hidden", messages.length > 0);
  renderHeader();
  $("#turn-rail").style.transform = "";
  scrollToBottom(true);
  if (turnCount) railSetCurrent(_lastUserIndex(messages));
  S.stick = true;
}

function _lastUserIndex(messages) {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    if (messages[i].role === "user") return i;
  }
  return 0;
}

async function openSession(id) {
  if (!id) return;
  try {
    const record = await api("/api/sessions/" + id);
    S.activeId = id;
    S.record = record;
    S.keepView = false;
    if (record.archived && !S.showArchived) {
      S.showArchived = true;
      $("#archived-btn").classList.add("on");
    }
    localStorage.setItem("zagent.active", id);
    const events = await api("/api/sessions/" + id + "/events?since=999999999");
    S.seq = events.seq || 0;
    S.running = new Set(events.running ? [id] : []);
    renderRecord(record);
    setRunning(events.running);
    renderTree();
    const index = S.sessions.findIndex((s) => s.id === id);
    if (index >= 0 && S.sessions[index].unread) {
      S.sessions[index].unread = false;
      renderTree();
    }
    if (window.innerWidth <= 760) document.body.classList.add("sidebar-collapsed");
    $("#composer").focus();
  } catch (error) {
    toast(error.message, true);
    setConnection(false);
  }
}

async function reloadActive() {
  if (!S.activeId) return;
  const id = S.activeId;
  try {
    const record = await api("/api/sessions/" + id);
    if (S.activeId !== id) return;
    S.record = record;
    renderRecord(record);
  } catch (_) { /* 静默：下一轮轮询会重试 */ }
}

/* ---------------- 事件流 ---------------- */
function setRunning(running) {
  if (!S.activeId) return;
  const was = S.running.has(S.activeId);
  if (running) S.running.add(S.activeId); else S.running.delete(S.activeId);
  const active = !!running;
  $("#stop-btn").classList.toggle("hidden", !active);
  $("#send-btn").disabled = active;
  $("#composer-hint").textContent = active
    ? "Zagent 正在生成… 可点击停止"
    : "Enter 发送 · Shift+Enter 换行 · ↑/↓ 历史输入";
  if (was !== active) renderTree();
}

function appendUser(content) {
  $("#empty").classList.add("hidden");
  const index = (S.record && S.record.messages ? S.record.messages.length : 0);
  const node = messageNode("user", index);
  const bubble = el("div", "bubble");
  bubble.textContent = content;
  node.append(bubble);
  $("#messages").append(node);
  addTurnMarker(index, content);
  if (S.record && S.record.messages) {
    S.record.messages.push({ role: "user", content: content });
    S.record.message_count = S.record.messages.length;
  }
  renderHeader();
  scrollToBottom();
}

function ensureStreamBubble() {
  if (S.streamEl) return S.streamEl;
  const node = messageNode("assistant");
  const bubble = el("div", "bubble streaming");
  node.append(bubble);
  $("#messages").append(node);
  S.streamEl = bubble;
  S.streamRaw = "";
  scrollToBottom();
  return bubble;
}

function handleEvent(event) {
  switch (event.type) {
    case "UserMessage":
      appendUser(event.content || "");
      break;
    case "TextDelta": {
      const bubble = ensureStreamBubble();
      S.streamRaw += event.text || "";
      bubble.textContent = S.streamRaw;
      scrollToBottom();
      break;
    }
    case "AssistantText": {
      const bubble = ensureStreamBubble();
      S.streamRaw = event.text || S.streamRaw;
      bubble.innerHTML = md(S.streamRaw);
      bubble.classList.remove("streaming");
      S.streamEl = null;
      S.streamRaw = "";
      scrollToBottom();
      break;
    }
    case "ToolCallStart": {
      S.streamEl = null;
      S.streamRaw = "";
      const container = el("div", "tool-wrap");
      $("#messages").append(container);
      renderToolCard(container, event.tool_call_id, event.name, event.arguments);
      scrollToBottom();
      break;
    }
    case "ToolCallEnd": {
      const entry = S.toolCards.get(event.tool_call_id);
      if (entry) {
        entry.status.textContent = event.ok ? "完成" : "失败";
        entry.status.className = "tool-status " + (event.ok ? "ok" : "bad");
        entry.resultPre.textContent = event.result || "";
        entry.resultBlock.classList.remove("hidden");
        if (!event.ok) entry.card.classList.add("open");
      }
      scrollToBottom();
      break;
    }
    case "LimitReached":
      $("#messages").append(el("div", "notice", "已达到工具调用轮次上限（" + event.rounds + " 轮），本轮已停止。"));
      break;
    case "TurnError": {
      const node = el("div", "notice error", "请求失败（" + (event.category || "error") + "）：" + (event.message || ""));
      $("#messages").append(node);
      S.keepView = true;
      S.streamEl = null;
      scrollToBottom();
      break;
    }
    case "TurnEnd":
      setRunning(false);
      break;
    case "Cancelled":
      $("#messages").append(el("div", "notice cancel", "已停止生成。"));
      S.keepView = true;
      setRunning(false);
      break;
    case "HistoryCleared":
      S.record.messages = [];
      S.record.message_count = 0;
      renderRecord(S.record);
      toast("会话历史已清空");
      break;
    case "CompactStart":
      $("#messages").append(el("div", "notice", "开始上下文压缩（归档 + LLM 摘要）…"));
      S.keepView = true;
      break;
    case "CompactProgress":
      $("#messages").append(el("div", "notice", "[压缩] " + (event.message || "")));
      S.keepView = true;
      break;
    case "CompactEnd":
      $("#messages").append(el(
        "div",
        event.ok ? "notice" : "notice error",
        (event.ok ? "上下文压缩完成：" : "上下文压缩失败：") + (event.message || "")
      ));
      S.keepView = true;
      break;
    case "ContextUsage":
      if (event.warning) {
        $("#messages").append(el(
          "div",
          "notice",
          "上下文已占用 " + Math.round((event.percent || 0) * 100) +
            "%，请输入 /compact 压缩上下文。"
        ));
        S.keepView = true;
      }
      break;
    case "Idle":
      setRunning(false);
      refreshSessions();
      if (S.keepView) S.keepView = false;
      else reloadActive();
      break;
    default:
      break;
  }
  if (event.seq) S.seq = Math.max(S.seq, event.seq);
}

async function pollEvents() {
  if (!S.activeId) return;
  const id = S.activeId;
  try {
    const data = await api("/api/sessions/" + id + "/events?since=" + S.seq);
    if (S.activeId !== id) return;
    if ((data.seq || 0) < S.seq) {
      // 服务端重启后序号归零：重建视图并从新序号继续
      S.seq = 0;
      await reloadActive();
    }
    for (const event of data.events || []) handleEvent(event);
    S.seq = data.seq || S.seq;
    setRunning(!!data.running);
    S.pollFailures = 0;
    setConnection(true);
  } catch (error) {
    S.pollFailures += 1;
    if (S.pollFailures >= 2) setConnection(false);
  }
}

async function pollLoop() {
  await pollEvents();
  const now = Date.now();
  if (now - S.lastSidebarRefresh > 4000) {
    S.lastSidebarRefresh = now;
    await refreshSessions();
    await refreshRuntime();
  }
  setTimeout(pollLoop, 320);
}

/* ---------------- 发送 / 停止 ---------------- */
async function sendMessage() {
  const composer = $("#composer");
  const content = composer.value.trim();
  if (!content || !S.activeId || S.running.has(S.activeId)) return;
  composer.value = "";
  autosize();
  rememberInput(content);
  S.stick = true;
  S.keepView = false;
  setRunning(true);
  try {
    await api("/api/sessions/" + S.activeId + "/ask", {
      method: "POST", body: { content: content },
    });
  } catch (error) {
    setRunning(false);
    composer.value = content;
    autosize();
    toast(error.message, true);
  }
}

async function stopGeneration() {
  if (!S.activeId) return;
  try { await api("/api/sessions/" + S.activeId + "/cancel", { method: "POST", body: {} }); }
  catch (error) { toast(error.message, true); }
}

/* ---------------- 输入框 / 历史 ---------------- */
function historyKey() {
  return "zagent.inputHistory." + (S.state ? S.state.workspace : "default");
}

function loadHistory() {
  try {
    const data = JSON.parse(localStorage.getItem(historyKey()) || "[]");
    S.history = Array.isArray(data) ? data.slice(-100) : [];
  } catch (_) { S.history = []; }
  S.historyIndex = -1;
  S.draft = "";
}

function rememberInput(text) {
  S.history = S.history.filter((item) => item !== text);
  S.history.push(text);
  if (S.history.length > 100) S.history = S.history.slice(-100);
  localStorage.setItem(historyKey(), JSON.stringify(S.history));
  S.historyIndex = -1;
  S.draft = "";
}

function recallHistory(direction) {
  const composer = $("#composer");
  if (!S.history.length) return;
  if (S.historyIndex === -1) {
    if (direction > 0) return;
    S.draft = composer.value;
    S.historyIndex = S.history.length - 1;
  } else if (direction < 0) {
    if (S.historyIndex <= 0) return;
    S.historyIndex -= 1;
  } else {
    if (S.historyIndex >= S.history.length - 1) {
      composer.value = S.draft;
      S.historyIndex = -1;
      autosize();
      return;
    }
    S.historyIndex += 1;
  }
  composer.value = S.history[S.historyIndex] || "";
  autosize();
  requestAnimationFrame(() => {
    composer.selectionStart = composer.selectionEnd = composer.value.length;
  });
}

function autosize() {
  const composer = $("#composer");
  composer.style.height = "auto";
  composer.style.height = Math.min(composer.scrollHeight, 190) + "px";
}

/* ---------------- 搜索 ---------------- */
function closeSearch() {
  $("#search-results").classList.add("hidden");
}

async function runSearch(query) {
  if (!query.trim()) { closeSearch(); return; }
  try {
    const data = await api("/api/search?archived=1&q=" + encodeURIComponent(query));
    const box = $("#search-results");
    box.innerHTML = "";
    const results = data.results || [];
    if (!results.length) {
      box.append(el("div", "sr-empty", "没有匹配的会话"));
    } else {
      results.forEach((item) => {
        const row = el("div", "sr-item");
        row.append(
          el("div", "sr-title", item.title || "新会话"),
          el("div", "sr-snippet", item.snippet || "")
        );
        row.addEventListener("click", async () => {
          closeSearch();
          $("#search").value = "";
          await openSession(item.id);
          if (item.message_index !== undefined) {
            const messages = (S.record && S.record.messages) || [];
            let target = item.message_index;
            for (let i = target; i >= 0; i -= 1) {
              if (messages[i] && messages[i].role === "user") { target = i; break; }
            }
            setTimeout(() => jumpToIndex(target), 60);
          }
        });
        box.append(row);
      });
    }
    box.classList.remove("hidden");
  } catch (error) { toast(error.message, true); }
}

/* ---------------- 弹窗 ---------------- */
function closeModal() {
  const modal = $("#modal");
  modal.classList.add("hidden");
  modal.innerHTML = "";
}

function showModal(title, body, footer) {
  const modal = $("#modal");
  modal.innerHTML = "";
  const card = el("div", "modal-card");
  const head = el("div", "modal-head");
  head.append(el("span", "", title));
  const close = el("button", "icon-btn", "✕");
  close.addEventListener("click", closeModal);
  head.append(close);
  const bodyNode = el("div", "modal-body");
  bodyNode.append(body);
  card.append(head, bodyNode);
  if (footer) {
    const foot = el("div", "modal-foot");
    foot.append(footer);
    card.append(foot);
  }
  modal.append(card);
  modal.classList.remove("hidden");
  modal.addEventListener("click", (event) => {
    if (event.target === modal) closeModal();
  }, { once: true });
  return bodyNode;
}

function askText(title, value) {
  return new Promise((resolve) => {
    const wrap = el("div");
    const input = el("input");
    input.type = "text";
    input.value = value || "";
    wrap.append(input);
    const foot = el("div");
    const cancel = el("button", "btn", "取消");
    const confirm = el("button", "btn primary", "确定");
    const finish = (result) => { closeModal(); resolve(result); };
    cancel.addEventListener("click", () => finish(null));
    confirm.addEventListener("click", () => finish(input.value));
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") { event.preventDefault(); finish(input.value); }
      if (event.key === "Escape") finish(null);
    });
    foot.append(cancel, confirm);
    showModal(title, wrap, foot);
    setTimeout(() => { input.focus(); input.select(); }, 20);
  });
}

function confirmAction(title, text, danger) {
  return new Promise((resolve) => {
    const wrap = el("div");
    wrap.append(el("p", "", text));
    wrap.querySelector("p").style.margin = "0";
    const foot = el("div");
    const cancel = el("button", "btn", "取消");
    const confirm = el("button", "btn " + (danger === false ? "primary" : "danger"), "确认");
    const finish = (result) => { closeModal(); resolve(result); };
    cancel.addEventListener("click", () => finish(false));
    confirm.addEventListener("click", () => finish(true));
    foot.append(cancel, confirm);
    showModal(title, wrap, foot);
  });
}

/* ---------------- 设置 ---------------- */
function chip(text) { return el("span", "chip", text); }

async function openSettings() {
  let state = S.state;
  try { state = await api("/api/state"); S.state = state; } catch (_) { /* 用缓存 */ }
  const body = el("div");

  const themeSection = el("div", "about-section");
  themeSection.append(el("h3", "", "外观"));
  const row = el("div", "theme-row");
  [["dark", "深色", "Codex 默认的暗色界面"], ["light", "浅色", "明亮、适合白天使用"]].forEach(([key, name, desc]) => {
    const choice = el("button", "theme-choice" + (document.documentElement.dataset.theme === key ? " on" : ""));
    choice.append(el("b", "", name), el("span", "", desc));
    choice.addEventListener("click", () => {
      applyTheme(key);
      row.querySelectorAll(".theme-choice").forEach((node) => node.classList.remove("on"));
      choice.classList.add("on");
    });
    row.append(choice);
  });
  themeSection.append(row);
  body.append(themeSection);

  if (state) {
    const about = el("div", "about-section");
    about.append(el("h3", "", "关于"));
    const grid = el("dl", "about-grid");
    [
      ["界面", "Zagent Codex Web"],
      ["模型", state.model],
      ["接口", state.base_url],
      ["工作区", state.workspace],
      ["会话数", String(state.session_count)],
      ["技能目录", state.skills_dir],
      ["市场目录", state.market_dir],
    ].forEach(([key, value]) => {
      grid.append(el("dt", "", key), el("dd", "", value));
    });
    about.append(grid);
    body.append(about);

    const tools = el("div", "about-section");
    tools.append(el("h3", "", "工具（" + (state.tools || []).length + "）"));
    const toolChips = el("div", "chips");
    (state.tools || []).forEach((name) => toolChips.append(chip(name)));
    tools.append(toolChips);
    body.append(tools);

    const mcpRows = state.mcp || [];
    if (mcpRows.length) {
      const mcpSection = el("div", "about-section");
      mcpSection.append(el("h3", "", "MCP Server（" + mcpRows.length + "）"));
      const mcpChips = el("div", "chips");
      mcpRows.forEach((row) => {
        const node = chip((row.ok ? "✓ " : "✗ ") + row.name);
        node.title = row.ok
          ? (row.type || "") + " · " + (row.target || "") +
            " · " + (row.tool_count || 0) + " 个工具"
          : ("连接失败：" + (row.error || ""));
        mcpChips.append(node);
      });
      mcpSection.append(mcpChips);
      body.append(mcpSection);
    }

    const skills = el("div", "about-section");
    skills.append(el("h3", "", "技能（" + (state.skills || []).length + "）"));
    const skillChips = el("div", "chips");
    if (!(state.skills || []).length) skillChips.append(el("span", "sr-empty", "尚未安装技能"));
    (state.skills || []).forEach((skill) => {
      const node = chip(skill.name);
      node.title = skill.description || "";
      skillChips.append(node);
    });
    skills.append(skillChips);
    body.append(skills);
  }

  const actions = el("div", "about-section");
  actions.append(el("h3", "", "数据"));
  const clearBtn = el("button", "btn", "清空当前会话历史");
  clearBtn.addEventListener("click", async () => {
    closeModal();
    if (!S.activeId) return;
    const ok = await confirmAction("清空历史", "将删除当前会话的全部消息（保留会话本身）。");
    if (!ok) return;
    try {
      await api("/api/sessions/" + S.activeId + "/clear", { method: "POST", body: {} });
      toast("已清空当前会话");
    } catch (error) { toast(error.message, true); }
  });
  actions.append(clearBtn);
  body.append(actions);

  showModal("设置", body);
}

/* ---------------- 事件绑定 ---------------- */
function bindEvents() {
  $("#collapse-btn").addEventListener("click", toggleSidebar);
  $("#sidebar-btn").addEventListener("click", toggleSidebar);
  $("#new-task").addEventListener("click", () => { closeMenu(); createSession(); });
  $("#settings-btn").addEventListener("click", openSettings);
  $("#archived-btn").addEventListener("click", () => {
    S.showArchived = !S.showArchived;
    $("#archived-btn").classList.toggle("on", S.showArchived);
    renderTree();
  });
  $("#theme-btn").addEventListener("click", () => {
    applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
  });
  $("#fork-btn").addEventListener("click", () => { if (S.activeId) forkSession(S.activeId); });
  $("#more-btn").addEventListener("click", (event) => {
    if (!S.record) return;
    openSessionMenu(event, S.record);
  });

  $("#send-btn").addEventListener("click", sendMessage);
  $("#stop-btn").addEventListener("click", stopGeneration);

  const composer = $("#composer");
  composer.addEventListener("input", autosize);
  composer.addEventListener("keydown", (event) => {
    if (event.isComposing) return;
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendMessage();
      return;
    }
    if (event.key === "ArrowUp" && composer.selectionStart === 0 && composer.selectionEnd === 0) {
      event.preventDefault();
      recallHistory(-1);
    } else if (event.key === "ArrowDown" && S.historyIndex >= 0) {
      event.preventDefault();
      recallHistory(1);
    }
  });

  $("#search").addEventListener("input", (event) => {
    clearTimeout(S.searchTimer);
    const value = event.target.value;
    S.searchTimer = setTimeout(() => runSearch(value), 240);
  });
  $("#search").addEventListener("keydown", (event) => {
    if (event.key === "Escape") { $("#search").value = ""; closeSearch(); $("#search").blur(); }
  });

  document.querySelectorAll(".sug").forEach((button) => {
    button.addEventListener("click", () => {
      $("#composer").value = button.dataset.prompt || "";
      autosize();
      $("#composer").focus();
    });
  });

  $("#chat").addEventListener("scroll", () => {
    const chat = $("#chat");
    S.stick = chat.scrollHeight - chat.scrollTop - chat.clientHeight < 120;
    // 轮次导航固定在视口左侧，不随消息一起滚走
    $("#turn-rail").style.transform = "translateY(" + chat.scrollTop + "px)";
  });

  document.addEventListener("click", (event) => {
    if (!event.target.closest("#menu") && !event.target.closest(".sess-more") && !event.target.closest("#more-btn")) {
      closeMenu();
    }
    if (!event.target.closest("#search") && !event.target.closest("#search-results")) closeSearch();
    if (window.innerWidth <= 760
      && !document.body.classList.contains("sidebar-collapsed")
      && !event.target.closest("#sidebar")
      && !event.target.closest("#sidebar-btn")) {
      document.body.classList.add("sidebar-collapsed");
    }
  });

  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      createSession();
    }
    if (event.key === "Escape") {
      closeMenu();
      closeSearch();
      if (!$("#modal").classList.contains("hidden")) closeModal();
    }
  });
}

/* ---------------- 启动 ---------------- */
async function init() {
  applyTheme(localStorage.getItem("zagent.theme") || "dark");
  if (localStorage.getItem("zagent.sidebar") === "0" || window.innerWidth <= 760) {
    document.body.classList.add("sidebar-collapsed");
  }
  bindEvents();
  autosize();

  try {
    S.state = await api("/api/state");
    $("#model-badge").textContent = S.state.model || "model";
    $("#model-badge").title = S.state.base_url || "";
    S.running = new Set(S.state.running || []);
  } catch (error) {
    toast("无法连接本地服务：" + error.message, true);
  }

  loadHistory();
  await refreshSessions();

  const saved = localStorage.getItem("zagent.active");
  const target = S.sessions.find((s) => s.id === saved && !s.archived)
    || S.sessions.find((s) => !s.archived);
  try {
    if (target) {
      await openSession(target.id);
    } else {
      await createSession();
    }
  } catch (error) {
    toast("初始化会话失败：" + error.message, true);
  }
  pollLoop();
}

document.addEventListener("DOMContentLoaded", init);
</script>
</body>
</html>
"""

__all__ = ["PAGE"]
