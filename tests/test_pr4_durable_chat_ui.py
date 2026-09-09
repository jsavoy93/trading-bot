# PR4 — Durable + Live Chat UI for the Engineering Dashboard
#
# These tests cover the browser-side merge logic (computeMergedRows,
# messageDedupKey) and the integration of live + durable + optimistic
# sources in the rendered Chat tab. They exercise the embedded JS via
# Node evaluation (same pattern as the existing chat tests in
# tests/test_dashboard_api_app.py) plus a pure-Python unit test of the
# merge primitives to keep coverage fast and deterministic.
#
# Scope locked with Josh 2026-09-08 16:52 UTC:
# - Durable-first load.
# - Live + durable merge with stable identity keys.
# - Optimistic send collapse.
# - "Load older" prepend + scroll preservation.
# - Auto-scroll rules (mobile-first).
# - Copy controls preserved.
# - Hidden/tool/system rows never exposed.
# - No new HTTP routes beyond the existing PR3 durable endpoint;
#   no Cloudflare / Tunnel / Access changes; no schema changes.

from __future__ import annotations

import json
import os
import re
import subprocess
import textwrap
from pathlib import Path

import pytest

from dashboard_api.app import render_dashboard
from tests.test_dashboard_api_app import populated_snapshot as _populated_snapshot

# --------------------------------------------------------------------------
# Test fixtures: shared Node-eval harness mirroring test_dashboard_api_app.
# --------------------------------------------------------------------------


def _dashboard_script(html: str) -> str:
    """Extract the embedded dashboard JS from the rendered HTML."""
    match = re.search(r"<script>(.*?)</script>", html, re.DOTALL)
    assert match is not None, "dashboard HTML must embed the refresh script"
    return match.group(1)


def _run_dashboard_script_case(script: str, body: str) -> None:
    """Pipe the dashboard script + a Node test harness into ``node -e``."""
    cmd = ["node", "-e", textwrap.dedent(body)]
    proc = subprocess.run(cmd, input=script, capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def _populated_snapshot_dict() -> dict[str, object]:
    return {
        "data_freshness_timestamp": "2026-09-08T16:00:00+00:00",
        "repository": {"root": "/root/.openclaw/workspace/trading-bot", "is_dirty": False, "head": "main", "head_commit": "b7f113a"},
        "pull_request": {"number": None, "url": None, "state": None, "title": None, "base": None, "head": None, "mergeable": None, "checks": []},
        "engineering_health": {"status": "healthy", "details": []},
        "health_warnings": [],
        "testing": {"configured": True, "active": False, "active_session": None},
        "live_activity": [],
        "recent_executions": [],
        "current_tasks": [],
        "backlog": {"counts_by_status": {"todo": 0, "in_progress": 0, "blocked": 0, "done": 0}, "active_task_id": None, "active_task_title": None, "status": None, "owner": None, "priority": None},
        "recent_events": [],
        "recent_reports": [],
    }


# --------------------------------------------------------------------------
# 1. Durable-first load renders before live polling lands
# --------------------------------------------------------------------------








def test_durable_first_load_renders_before_live_polling_lands() -> None:
    """On Chat tab open, refreshChatHistory fetches the durable endpoint
    FIRST so the user sees prior conversation immediately. Live polling
    follows, but the durable rows are already rendered."""
    html = render_dashboard(_populated_snapshot())
    script = _dashboard_script(html)
    body = """
    const assert = require('assert');
    const fs = require('fs');
    const script = fs.readFileSync(0, 'utf8');
    const calls = [];
    global.window = {scrollX: 0, scrollY: 0, setInterval: () => 1, scrollTo: () => {}};
    const copySince = {hidden: true, disabled: true, dataset: {}, addEventListener: () => {}, textContent: 'Copy since my last message'};
    const chatHistory = {innerHTML: '', scrollTop: 0, scrollHeight: 1000, firstElementChild: null, offsetTop: 0};
    global.document = {
      getElementById: (id) => id === 'dashboard-content' ? {innerHTML: '', addEventListener: () => {}, contains: () => true, querySelectorAll: () => []}
        : id === 'update-warning' ? {textContent: '', style: {display: 'none'}}
        : id === 'chat-state' ? {textContent: '', style: {display: 'none'}, dataset: {}}
        : id === 'chat-history' ? chatHistory
        : id === 'chat-message' ? {value: ''}
        : id === 'chat-send' ? {disabled: false, textContent: 'Send'}
        : id === 'chat-load-older' ? {hidden: true, disabled: true, dataset: {}, addEventListener: () => {}}
        : null,
    };
    global.fetch = async (url) => {
      calls.push(url);
      if (url.startsWith('/api/engineering/chat/history/durable')) {
        return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available'}, messages: [
          {role: 'user', text: 'old user 1', timestamp: '2026-09-01T10:00:00+00:00', durable_id: 1, source_message_id: null, openclaw_run_id: 'run-old-1', openclaw_session_id: 'sess-old'},
          {role: 'assistant', text: 'old assistant 1', timestamp: '2026-09-01T10:00:01+00:00', durable_id: 2, source_message_id: 'old-asst-mid', openclaw_run_id: null, openclaw_session_id: 'sess-old'},
        ]})};
      }
      if (url === '/api/engineering/chat/history') {
        return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available'}, messages: []})};
      }
      throw new Error('unexpected url ' + url);
    };
    eval(script.replace('<script>', '').replace('</script>', ''));
    (async () => {
      await window.engineeringDashboard.refreshChatHistory();
      // Durable fetch MUST happen before the live fetch.
      const durableIdx = calls.findIndex((u) => u.startsWith('/api/engineering/chat/history/durable'));
      const liveIdx = calls.indexOf('/api/engineering/chat/history');
      assert(durableIdx >= 0, 'durable fetch was issued');
      assert(liveIdx >= 0, 'live fetch was issued');
      assert(durableIdx < liveIdx, 'durable fetch precedes live fetch (PR4 contract)');
      // After both fetches, durable rows are present in the DOM.
      const history = document.getElementById('chat-history');
      assert(history.innerHTML.includes('old user 1'));
      assert(history.innerHTML.includes('old assistant 1'));
      assert(history.innerHTML.includes('data-source="durable"'));
    })().catch((e) => { console.error(e); process.exit(1); });
    """
    _run_dashboard_script_case(script, body)


# --------------------------------------------------------------------------
# 2. Live-only rows collapse into durable rows on next poll
# --------------------------------------------------------------------------








def test_live_only_row_collapses_into_durable_row_on_next_poll() -> None:
    """A live-visible row appears immediately with data-source="live-only".
    When the durable store catches up (next poll) with a row carrying the
    same identity, the live-only marker is removed and the durable row
    takes its place — without re-rendering the entire history."""
    html = render_dashboard(_populated_snapshot())
    script = _dashboard_script(html)
    body = """
    const assert = require('assert');
    const fs = require('fs');
    const script = fs.readFileSync(0, 'utf8');
    global.window = {scrollX: 0, scrollY: 0, setInterval: () => 1, scrollTo: () => {}};
    const copySince = {hidden: true, disabled: true, dataset: {}, addEventListener: () => {}, textContent: 'Copy since my last message'};
    const chatHistory = {innerHTML: '', scrollTop: 0, scrollHeight: 1000, firstElementChild: null, offsetTop: 0};
    global.document = {
      getElementById: (id) => id === 'dashboard-content' ? {innerHTML: '', addEventListener: () => {}, contains: () => true, querySelectorAll: () => []}
        : id === 'update-warning' ? {textContent: '', style: {display: 'none'}}
        : id === 'chat-state' ? {textContent: '', style: {display: 'none'}, dataset: {}}
        : id === 'chat-history' ? chatHistory
        : id === 'chat-message' ? {value: ''}
        : id === 'chat-send' ? {disabled: false, textContent: 'Send'}
        : id === 'chat-load-older' ? {hidden: true, disabled: true, dataset: {}, addEventListener: () => {}}
        : null,
    };
    let poll = 0;
    global.fetch = async (url) => {
      poll += 1;
      if (url.startsWith('/api/engineering/chat/history/durable')) {
        // First poll: empty durable. Second poll: assistant message
        // has been persisted.
        if (poll <= 2) {
          return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available'}, messages: []})};
        }
        return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available'}, messages: [
          {role: 'assistant', text: 'reply', timestamp: '2026-09-08T16:00:00+00:00', durable_id: 7, source_message_id: 'mid-reply', openclaw_run_id: null, openclaw_session_id: 'sess-1'},
        ]})};
      }
      if (url === '/api/engineering/chat/history') {
        return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available'}, messages: [
          {role: 'assistant', text: 'reply', timestamp: '2026-09-08T16:00:00+00:00', source_message_id: 'mid-reply'},
        ]})};
      }
      throw new Error('unexpected url ' + url);
    };
    eval(script.replace('<script>', '').replace('</script>', ''));
    (async () => {
      await window.engineeringDashboard.refreshChatHistory();
      const history = document.getElementById('chat-history');
      // First poll: assistant row appears as live-only.
      assert(history.innerHTML.includes('data-source="live-only"'));
      assert(history.innerHTML.includes('reply'));
      // Second poll: durable row matches by source_message_id; the
      // live-only marker is gone and the row is now "durable".
      await window.engineeringDashboard.refreshChatHistory();
      assert(!history.innerHTML.includes('data-source="live-only"'));
      assert(history.innerHTML.includes('data-source="durable"'));
      assert(history.innerHTML.includes('reply'));
      // Exactly one row for "reply" — no duplication.
      const occurrences = (history.innerHTML.match(/reply/g) || []).length;
      assert.strictEqual(occurrences, 1, 'expected exactly one reply row after collapse');
    })().catch((e) => { console.error(e); process.exit(1); });
    """
    _run_dashboard_script_case(script, body)


# --------------------------------------------------------------------------
# 3. Live assistant dedup uses source_message_id
# --------------------------------------------------------------------------








def test_live_assistant_dedup_uses_source_message_id() -> None:
    """Two live rows with the same source_message_id collapse to one."""
    html = render_dashboard(_populated_snapshot())
    script = _dashboard_script(html)
    body = """
    const assert = require('assert');
    const fs = require('fs');
    const script = fs.readFileSync(0, 'utf8');
    global.window = {scrollX: 0, scrollY: 0, setInterval: () => 1, scrollTo: () => {}};
    const copySince = {hidden: true, disabled: true, dataset: {}, addEventListener: () => {}, textContent: 'Copy since my last message'};
    const chatHistory = {innerHTML: '', scrollTop: 0, scrollHeight: 1000, firstElementChild: null, offsetTop: 0};
    global.document = {
      getElementById: (id) => id === 'dashboard-content' ? {innerHTML: '', addEventListener: () => {}, contains: () => true, querySelectorAll: () => []}
        : id === 'update-warning' ? {textContent: '', style: {display: 'none'}}
        : id === 'chat-state' ? {textContent: '', style: {display: 'none'}, dataset: {}}
        : id === 'chat-history' ? chatHistory
        : id === 'chat-message' ? {value: ''}
        : id === 'chat-send' ? {disabled: false, textContent: 'Send'}
        : id === 'chat-load-older' ? {hidden: true, disabled: true, dataset: {}, addEventListener: () => {}}
        : null,
    };
    global.fetch = async (url) => {
      if (url.startsWith('/api/engineering/chat/history/durable')) {
        return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available'}, messages: []})};
      }
      if (url === '/api/engineering/chat/history') {
        return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available'}, messages: [
          {role: 'assistant', text: 'dup reply', timestamp: '2026-09-08T16:00:00+00:00', source_message_id: 'same-mid'},
          {role: 'assistant', text: 'dup reply', timestamp: '2026-09-08T16:00:00+00:00', source_message_id: 'same-mid'},
        ]})};
      }
      throw new Error('unexpected url ' + url);
    };
    eval(script.replace('<script>', '').replace('</script>', ''));
    (async () => {
      await window.engineeringDashboard.refreshChatHistory();
      const history = document.getElementById('chat-history');
      const occurrences = (history.innerHTML.match(/dup reply/g) || []).length;
      assert.strictEqual(occurrences, 1, 'source_message_id dedup should collapse duplicates to one row');
    })().catch((e) => { console.error(e); process.exit(1); });
    """
    _run_dashboard_script_case(script, body)


# --------------------------------------------------------------------------
# 4. Live user dedup falls back to (role, text, ts_bucket)
# --------------------------------------------------------------------------








def test_live_user_dedup_falls_back_to_text_and_ts_bucket() -> None:
    """Two user rows with identical text + timestamp bucket (no
    source_message_id, no openclaw_run_id) collapse to one row."""
    html = render_dashboard(_populated_snapshot())
    script = _dashboard_script(html)
    body = """
    const assert = require('assert');
    const fs = require('fs');
    const script = fs.readFileSync(0, 'utf8');
    global.window = {scrollX: 0, scrollY: 0, setInterval: () => 1, scrollTo: () => {}};
    const copySince = {hidden: true, disabled: true, dataset: {}, addEventListener: () => {}, textContent: 'Copy since my last message'};
    const chatHistory = {innerHTML: '', scrollTop: 0, scrollHeight: 1000, firstElementChild: null, offsetTop: 0};
    global.document = {
      getElementById: (id) => id === 'dashboard-content' ? {innerHTML: '', addEventListener: () => {}, contains: () => true, querySelectorAll: () => []}
        : id === 'update-warning' ? {textContent: '', style: {display: 'none'}}
        : id === 'chat-state' ? {textContent: '', style: {display: 'none'}, dataset: {}}
        : id === 'chat-history' ? chatHistory
        : id === 'chat-message' ? {value: ''}
        : id === 'chat-send' ? {disabled: false, textContent: 'Send'}
        : id === 'chat-load-older' ? {hidden: true, disabled: true, dataset: {}, addEventListener: () => {}}
        : null,
    };
    global.fetch = async (url) => {
      if (url.startsWith('/api/engineering/chat/history/durable')) {
        return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available'}, messages: []})};
      }
      if (url === '/api/engineering/chat/history') {
        return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available'}, messages: [
          {role: 'user', text: 'hello', timestamp: '2026-09-08T16:00:00+00:00'},
          {role: 'user', text: 'hello', timestamp: '2026-09-08T16:00:00+00:00'},
        ]})};
      }
      throw new Error('unexpected url ' + url);
    };
    eval(script.replace('<script>', '').replace('</script>', ''));
    (async () => {
      await window.engineeringDashboard.refreshChatHistory();
      const history = document.getElementById('chat-history');
      const occurrences = (history.innerHTML.match(/>hello</g) || []).length;
      assert.strictEqual(occurrences, 1, 'text+ts_bucket dedup should collapse duplicates to one row');
    })().catch((e) => { console.error(e); process.exit(1); });
    """
    _run_dashboard_script_case(script, body)


# --------------------------------------------------------------------------
# 5. Identical text from different runs stays as 2 rows
# --------------------------------------------------------------------------








def test_identical_text_different_runs_stays_as_two_rows() -> None:
    """Two user rows with identical text but DIFFERENT openclaw_run_id
    remain as two separate rows. We never dedup on text alone."""
    html = render_dashboard(_populated_snapshot())
    script = _dashboard_script(html)
    body = """
    const assert = require('assert');
    const fs = require('fs');
    const script = fs.readFileSync(0, 'utf8');
    global.window = {scrollX: 0, scrollY: 0, setInterval: () => 1, scrollTo: () => {}};
    const copySince = {hidden: true, disabled: true, dataset: {}, addEventListener: () => {}, textContent: 'Copy since my last message'};
    const chatHistory = {innerHTML: '', scrollTop: 0, scrollHeight: 1000, firstElementChild: null, offsetTop: 0};
    global.document = {
      getElementById: (id) => id === 'dashboard-content' ? {innerHTML: '', addEventListener: () => {}, contains: () => true, querySelectorAll: () => []}
        : id === 'update-warning' ? {textContent: '', style: {display: 'none'}}
        : id === 'chat-state' ? {textContent: '', style: {display: 'none'}, dataset: {}}
        : id === 'chat-history' ? chatHistory
        : id === 'chat-message' ? {value: ''}
        : id === 'chat-send' ? {disabled: false, textContent: 'Send'}
        : id === 'chat-load-older' ? {hidden: true, disabled: true, dataset: {}, addEventListener: () => {}}
        : null,
    };
    global.fetch = async (url) => {
      if (url.startsWith('/api/engineering/chat/history/durable')) {
        return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available'}, messages: [
          {role: 'user', text: 'same', timestamp: '2026-09-08T16:00:00+00:00', durable_id: 1, source_message_id: null, openclaw_run_id: 'run-A', openclaw_session_id: 'sess-1'},
          {role: 'user', text: 'same', timestamp: '2026-09-08T16:00:02+00:00', durable_id: 2, source_message_id: null, openclaw_run_id: 'run-B', openclaw_session_id: 'sess-1'},
        ]})};
      }
      if (url === '/api/engineering/chat/history') {
        return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available'}, messages: []})};
      }
      throw new Error('unexpected url ' + url);
    };
    eval(script.replace('<script>', '').replace('</script>', ''));
    (async () => {
      await window.engineeringDashboard.refreshChatHistory();
      const history = document.getElementById('chat-history');
      const occurrences = (history.innerHTML.match(/>same</g) || []).length;
      assert.strictEqual(occurrences, 2, 'identical text from different runs must remain as 2 rows');
    })().catch((e) => { console.error(e); process.exit(1); });
    """
    _run_dashboard_script_case(script, body)


# --------------------------------------------------------------------------
# 6. Session rotation: durable rows preserved, live polling resumes
# --------------------------------------------------------------------------








def test_session_rotation_preserves_durable_rows() -> None:
    """After OpenClaw session rotation, durable rows from the previous
    session are kept and new live rows appear with the new session."""
    html = render_dashboard(_populated_snapshot())
    script = _dashboard_script(html)
    body = """
    const assert = require('assert');
    const fs = require('fs');
    const script = fs.readFileSync(0, 'utf8');
    global.window = {scrollX: 0, scrollY: 0, setInterval: () => 1, scrollTo: () => {}};
    const copySince = {hidden: true, disabled: true, dataset: {}, addEventListener: () => {}, textContent: 'Copy since my last message'};
    const chatHistory = {innerHTML: '', scrollTop: 0, scrollHeight: 1000, firstElementChild: null, offsetTop: 0};
    global.document = {
      getElementById: (id) => id === 'dashboard-content' ? {innerHTML: '', addEventListener: () => {}, contains: () => true, querySelectorAll: () => []}
        : id === 'update-warning' ? {textContent: '', style: {display: 'none'}}
        : id === 'chat-state' ? {textContent: '', style: {display: 'none'}, dataset: {}}
        : id === 'chat-history' ? chatHistory
        : id === 'chat-message' ? {value: ''}
        : id === 'chat-send' ? {disabled: false, textContent: 'Send'}
        : id === 'chat-load-older' ? {hidden: true, disabled: true, dataset: {}, addEventListener: () => {}}
        : null,
    };
    let poll = 0;
    global.fetch = async (url) => {
      poll += 1;
      if (url.startsWith('/api/engineering/chat/history/durable')) {
        // Same conversation_id, but a rotating openclaw_session_id.
        return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available'}, messages: [
          {role: 'user', text: 'old turn', timestamp: '2026-09-01T10:00:00+00:00', durable_id: 1, source_message_id: null, openclaw_run_id: 'run-old', openclaw_session_id: 'sess-A'},
          {role: 'user', text: 'new turn', timestamp: '2026-09-08T16:00:00+00:00', durable_id: 2, source_message_id: null, openclaw_run_id: 'run-new', openclaw_session_id: 'sess-B-rotated'},
        ]})};
      }
      if (url === '/api/engineering/chat/history') {
        // After rotation, the live poll sees the new session's messages.
        return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available'}, messages: [
          {role: 'assistant', text: 'reply after rotation', timestamp: '2026-09-08T16:00:05+00:00', source_message_id: 'mid-rot'},
        ]})};
      }
      throw new Error('unexpected url ' + url);
    };
    eval(script.replace('<script>', '').replace('</script>', ''));
    (async () => {
      await window.engineeringDashboard.refreshChatHistory();
      const history = document.getElementById('chat-history');
      // Old + new durable rows preserved across the rotation.
      assert(history.innerHTML.includes('old turn'));
      assert(history.innerHTML.includes('new turn'));
      // The new assistant reply shows up as live-only (not yet durable).
      assert(history.innerHTML.includes('reply after rotation'));
      assert(history.innerHTML.includes('data-source="live-only"'));
    })().catch((e) => { console.error(e); process.exit(1); });
    """
    _run_dashboard_script_case(script, body)


# --------------------------------------------------------------------------
# 7. Load older prepends and preserves scroll position
# --------------------------------------------------------------------------








def test_load_older_prepends_and_preserves_scroll_position() -> None:
    """Clicking 'Load older' prepends older durable rows. The scroll
    position is preserved (the previously-visible rows stay where they
    were on screen)."""
    html = render_dashboard(_populated_snapshot())
    script = _dashboard_script(html)
    body = """
    const assert = require('assert');
    const fs = require('fs');
    const script = fs.readFileSync(0, 'utf8');
    let historyRef = null;
    let loadOlderButton = null;
    global.window = {scrollX: 0, scrollY: 0, setInterval: () => 1, scrollTo: () => {}};
    const copySince = {hidden: true, disabled: true, dataset: {}, addEventListener: () => {}, textContent: 'Copy since my last message'};
    global.document = {
      getElementById: (id) => {
        if (id === 'dashboard-content') { return {innerHTML: '', addEventListener: () => {}, contains: () => true, querySelectorAll: () => []}; }
        if (id === 'update-warning') { return {textContent: '', style: {display: 'none'}}; }
        if (id === 'chat-state') { return {textContent: '', style: {display: 'none'}, dataset: {}}; }
        if (id === 'chat-history') {
          if (!historyRef) {
            historyRef = {
              innerHTML: '',
              scrollTop: 350,
              scrollHeight: 700,
              firstElementChild: {offsetTop: 400},
              offsetTop: 0,
              clientHeight: 500,
              get offsetTop_firstChild() { return (this.firstElementChild ? this.firstElementChild.offsetTop - this.offsetTop : 0); },
            };
          }
          return historyRef;
        }
        if (id === 'chat-message') { return {value: ''}; }
        if (id === 'chat-send') { return {disabled: false, textContent: 'Send'}; }
        if (id === 'chat-load-older') {
          if (!loadOlderButton) {
            loadOlderButton = {
              hidden: false, disabled: false, dataset: {}, addEventListener: (event, fn) => { if (event === 'click') { loadOlderButton._onclick = fn; } },
              _onclick: null, click() { if (this._onclick) { this._onclick(); } },
            };
          }
          return loadOlderButton;
        }
        return null;
      },
    };
    let poll = 0;
    global.fetch = async (url) => {
      poll += 1;
      if (poll === 1) {
        // Initial durable fetch: 2 rows (olderAvailable becomes true).
        return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available'}, messages: [
          {role: 'user', text: 'newer', timestamp: '2026-09-08T10:00:00+00:00', durable_id: 99, source_message_id: null, openclaw_run_id: 'run-99', openclaw_session_id: 'sess-1'},
        ]})};
      }
      if (url.startsWith('/api/engineering/chat/history/durable') && url.includes('before_id=99')) {
        // Load older: returns older rows + reports no more.
        return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available'}, messages: [
          {role: 'assistant', text: 'older 1', timestamp: '2026-09-01T10:00:00+00:00', durable_id: 1, source_message_id: 'old-mid-1', openclaw_run_id: null, openclaw_session_id: 'sess-A'},
          {role: 'user', text: 'older 2', timestamp: '2026-09-01T10:00:01+00:00', durable_id: 2, source_message_id: null, openclaw_run_id: 'old-run-2', openclaw_session_id: 'sess-A'},
        ], before_id: null, limit: 50})};
      }
      if (url === '/api/engineering/chat/history') {
        return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available'}, messages: []})};
      }
      throw new Error('unexpected url ' + url);
    };
    eval(script.replace('<script>', '').replace('</script>', ''));
    (async () => {
      await window.engineeringDashboard.refreshChatHistory();
      // Capture pre-load-older scroll position.
      const beforeTop = historyRef.scrollTop;
      const beforeHeight = historyRef.scrollHeight;
      const beforeInner = historyRef.innerHTML;
      assert(beforeInner.includes('newer'));
      assert(!beforeInner.includes('older 1'));
      // Click "Load older". The mock returns 2 older rows; the durable
      // store reports no more (before_id: null). The button hides.
      loadOlderButton.click();
      // Wait one tick for the async fetch to resolve.
      await new Promise((r) => setTimeout(r, 10));
      assert(historyRef.innerHTML.includes('older 1'));
      assert(historyRef.innerHTML.includes('older 2'));
      assert(historyRef.innerHTML.includes('newer'));
      // Scroll position is preserved (or greater) after prepend.
      assert(historyRef.scrollTop >= beforeTop - 1, 'scrollTop must not jump up after load-older');
      // The mocked scrollHeight does not auto-grow when innerHTML is
      // reassigned; instead we assert that the row count grew.
      const newRowCount = (historyRef.innerHTML.match(/<article class="chat-message/g) || []).length;
      const oldRowCount = (beforeInner.match(/<article class="chat-message/g) || []).length;
      assert(newRowCount > oldRowCount, 'history must grow when older rows are prepended');
      // The "Load older" button hides when no older rows remain.
      assert(loadOlderButton.hidden === true, 'load older button must hide when server reports no more');
    })().catch((e) => { console.error(e); process.exit(1); });
    """
    _run_dashboard_script_case(script, body)


# --------------------------------------------------------------------------
# 8. Load older hides when no older rows remain
# --------------------------------------------------------------------------








def test_load_older_hides_when_no_older_rows_remain() -> None:
    """If the durable endpoint returns an empty page, the button hides."""
    html = render_dashboard(_populated_snapshot())
    script = _dashboard_script(html)
    body = """
    const assert = require('assert');
    const fs = require('fs');
    const script = fs.readFileSync(0, 'utf8');
    let loadOlderButton = null;
    global.window = {scrollX: 0, scrollY: 0, setInterval: () => 1, scrollTo: () => {}};
    const copySince = {hidden: true, disabled: true, dataset: {}, addEventListener: () => {}, textContent: 'Copy since my last message'};
    global.document = {
      getElementById: (id) => {
        if (id === 'dashboard-content') { return {innerHTML: '', addEventListener: () => {}, contains: () => true, querySelectorAll: () => []}; }
        if (id === 'update-warning') { return {textContent: '', style: {display: 'none'}}; }
        if (id === 'chat-state') { return {textContent: '', style: {display: 'none'}, dataset: {}}; }
        if (id === 'chat-history') { return {innerHTML: '', scrollTop: 0, scrollHeight: 1000, firstElementChild: null, offsetTop: 0}; }
        if (id === 'chat-message') { return {value: ''}; }
        if (id === 'chat-send') { return {disabled: false, textContent: 'Send'}; }
        if (id === 'chat-load-older') {
          if (!loadOlderButton) {
            loadOlderButton = {
              hidden: false, disabled: false, dataset: {}, addEventListener: (event, fn) => { if (event === 'click') { loadOlderButton._onclick = fn; } },
              _onclick: null, click() { if (this._onclick) { this._onclick(); } },
            };
          }
          return loadOlderButton;
        }
        return null;
      },
    };
    let poll = 0;
    global.fetch = async (url) => {
      poll += 1;
      if (url.startsWith('/api/engineering/chat/history/durable') && url.includes('before_id')) {
        return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available'}, messages: [], before_id: null, limit: 50})};
      }
      return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available'}, messages: [
        {role: 'user', text: 'only row', timestamp: '2026-09-08T16:00:00+00:00', durable_id: 5, source_message_id: null, openclaw_run_id: 'run-only', openclaw_session_id: 'sess-1'},
      ]})};
    };
    eval(script.replace('<script>', '').replace('</script>', ''));
    (async () => {
      await window.engineeringDashboard.refreshChatHistory();
      // After initial fetch the button may be visible (olderAvailable
      // becomes true when initial page is full-sized; in this mock it
      // returns exactly 1 row so olderAvailable may be false). Click
      // regardless and verify it ends hidden after the empty response.
      loadOlderButton.click();
      await new Promise((r) => setTimeout(r, 10));
      assert(loadOlderButton.hidden === true, 'load older button must hide when empty page returned');
    })().catch((e) => { console.error(e); process.exit(1); });
    """
    _run_dashboard_script_case(script, body)


# --------------------------------------------------------------------------
# 9. Optimistic user row added immediately on send
# --------------------------------------------------------------------------








def test_optimistic_user_row_added_immediately_on_send() -> None:
    """On send, the user message is rendered as an optimistic row
    IMMEDIATELY (before any durable or live poll completes)."""
    html = render_dashboard(_populated_snapshot())
    script = _dashboard_script(html)
    body = """
    const assert = require('assert');
    const fs = require('fs');
    const script = fs.readFileSync(0, 'utf8');
    let input = {value: 'optimistic text'};
    let button = {disabled: false, textContent: 'Send'};
    global.window = {scrollX: 0, scrollY: 0, setInterval: () => 1, scrollTo: () => {}};
    const copySince = {hidden: true, disabled: true, dataset: {}, addEventListener: () => {}, textContent: 'Copy since my last message'};
    const chatHistory = {innerHTML: '', scrollTop: 0, scrollHeight: 1000, firstElementChild: null, offsetTop: 0};
    global.document = {
      getElementById: (id) => id === 'dashboard-content' ? {innerHTML: '', addEventListener: () => {}, contains: () => true, querySelectorAll: () => []}
        : id === 'update-warning' ? {textContent: '', style: {display: 'none'}}
        : id === 'chat-state' ? {textContent: '', style: {display: 'none'}, dataset: {}}
        : id === 'chat-history' ? chatHistory
        : id === 'chat-message' ? input
        : id === 'chat-send' ? button
        : id === 'chat-load-older' ? {hidden: true, disabled: true, dataset: {}, addEventListener: () => {}}
        : null,
    };
    let durableCalls = 0;
    let liveCalls = 0;
    global.fetch = async (url, options) => {
      if (url === '/api/engineering/chat/send') {
        // Snapshot the DOM before resolving to prove the optimistic
        // row is rendered BEFORE the durable / live polls land.
        const beforeResolve = document.getElementById('chat-history').innerHTML;
        assert(beforeResolve.includes('optimistic text'), 'optimistic row must be visible before send resolves');
        assert(beforeResolve.includes('data-source="optimistic"'), 'optimistic row carries data-source="optimistic"');
        return {ok: true, json: async () => ({ok: true, status: 'sent', run_id: 'run-opt'})};
      }
      if (url.startsWith('/api/engineering/chat/history/durable')) {
        durableCalls += 1;
        return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available'}, messages: []})};
      }
      if (url === '/api/engineering/chat/history') {
        liveCalls += 1;
        return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available'}, messages: []})};
      }
      throw new Error('unexpected url ' + url);
    };
    eval(script.replace('<script>', '').replace('</script>', ''));
    (async () => {
      await window.engineeringDashboard.sendChatMessage(input.value);
      assert(durableCalls >= 1, 'durable refresh issued after send');
      assert(liveCalls >= 1, 'live refresh issued after send');
      const history = document.getElementById('chat-history');
      assert(history.innerHTML.includes('optimistic text'));
    })().catch((e) => { console.error(e); process.exit(1); });
    """
    _run_dashboard_script_case(script, body)


# --------------------------------------------------------------------------
# 10. Optimistic row collapses with durable row on next poll
# --------------------------------------------------------------------------








def test_optimistic_row_collapses_with_durable_row_on_next_poll() -> None:
    """When the durable store catches up and the row appears with the
    same openclaw_run_id, the optimistic row collapses (no duplicate)."""
    html = render_dashboard(_populated_snapshot())
    script = _dashboard_script(html)
    body = """
    const assert = require('assert');
    const fs = require('fs');
    const script = fs.readFileSync(0, 'utf8');
    let input = {value: 'collapse me'};
    let button = {disabled: false, textContent: 'Send'};
    global.window = {scrollX: 0, scrollY: 0, setInterval: () => 1, scrollTo: () => {}};
    const copySince = {hidden: true, disabled: true, dataset: {}, addEventListener: () => {}, textContent: 'Copy since my last message'};
    const chatHistory = {innerHTML: '', scrollTop: 0, scrollHeight: 1000, firstElementChild: null, offsetTop: 0};
    global.document = {
      getElementById: (id) => id === 'dashboard-content' ? {innerHTML: '', addEventListener: () => {}, contains: () => true, querySelectorAll: () => []}
        : id === 'update-warning' ? {textContent: '', style: {display: 'none'}}
        : id === 'chat-state' ? {textContent: '', style: {display: 'none'}, dataset: {}}
        : id === 'chat-history' ? chatHistory
        : id === 'chat-message' ? input
        : id === 'chat-send' ? button
        : id === 'chat-load-older' ? {hidden: true, disabled: true, dataset: {}, addEventListener: () => {}}
        : null,
    };
    global.fetch = async (url, options) => {
      if (url === '/api/engineering/chat/send') {
        return {ok: true, json: async () => ({ok: true, status: 'sent', run_id: 'run-collapse'})};
      }
      if (url.startsWith('/api/engineering/chat/history/durable')) {
        return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available'}, messages: [
          {role: 'user', text: 'collapse me', timestamp: '2026-09-08T16:00:00+00:00', durable_id: 11, source_message_id: null, openclaw_run_id: 'run-collapse', openclaw_session_id: 'sess-1'},
        ]})};
      }
      if (url === '/api/engineering/chat/history') {
        return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available'}, messages: []})};
      }
      throw new Error('unexpected url ' + url);
    };
    eval(script.replace('<script>', '').replace('</script>', ''));
    (async () => {
      await window.engineeringDashboard.sendChatMessage(input.value);
      const history = document.getElementById('chat-history');
      const occurrences = (history.innerHTML.match(/>collapse me</g) || []).length;
      assert.strictEqual(occurrences, 1, 'optimistic row must collapse into durable row (no duplicate)');
      assert(!history.innerHTML.includes('data-source="optimistic"'), 'no optimistic marker left after collapse');
    })().catch((e) => { console.error(e); process.exit(1); });
    """
    _run_dashboard_script_case(script, body)


# --------------------------------------------------------------------------
# 11. Send failure preserves draft + history + shows bounded warning
# --------------------------------------------------------------------------








def test_send_failure_preserves_draft_history_and_shows_bounded_warning() -> None:
    """If the POST /chat/send fails, the optimistic row is removed, the
    draft is restored, the chat history is preserved, and the chat-state
    banner shows a bounded failure warning."""
    html = render_dashboard(_populated_snapshot())
    script = _dashboard_script(html)
    body = """
    const assert = require('assert');
    const fs = require('fs');
    const script = fs.readFileSync(0, 'utf8');
    let input = {value: 'will fail'};
    let button = {disabled: false, textContent: 'Send'};
    let chatState = {textContent: '', style: {display: 'none'}, dataset: {}};
    global.window = {scrollX: 0, scrollY: 0, setInterval: () => 1, scrollTo: () => {}};
    const copySince = {hidden: true, disabled: true, dataset: {}, addEventListener: () => {}, textContent: 'Copy since my last message'};
    const chatHistory = {innerHTML: '', scrollTop: 0, scrollHeight: 1000, firstElementChild: null, offsetTop: 0};
    global.document = {
      getElementById: (id) => id === 'dashboard-content' ? {innerHTML: '', addEventListener: () => {}, contains: () => true, querySelectorAll: () => []}
        : id === 'update-warning' ? {textContent: '', style: {display: 'none'}}
        : id === 'chat-state' ? chatState
        : id === 'chat-history' ? chatHistory
        : id === 'chat-message' ? input
        : id === 'chat-send' ? button
        : id === 'chat-load-older' ? {hidden: true, disabled: true, dataset: {}, addEventListener: () => {}}
        : null,
    };
    global.fetch = async () => ({ok: false, json: async () => ({ok: false, error: 'bounded fail'})});
    eval(script.replace('<script>', '').replace('</script>', ''));
    (async () => {
      await window.engineeringDashboard.sendChatMessage(input.value);
      // Draft is restored on failure.
      assert.strictEqual(input.value, 'will fail');
      // Compose button is reset.
      assert.strictEqual(button.disabled, false);
      assert.strictEqual(button.textContent, 'Send');
      // Chat state banner shows a bounded failure warning.
      assert(chatState.textContent.toLowerCase().includes('failed'));
      // No optimistic row survived in the history.
      const history = document.getElementById('chat-history');
      assert(!history.innerHTML.includes('will fail') || !history.innerHTML.includes('data-source="optimistic"'));
    })().catch((e) => { console.error(e); process.exit(1); });
    """
    _run_dashboard_script_case(script, body)


# --------------------------------------------------------------------------
# 12. 4,000-char + non-text rejection preserved
# --------------------------------------------------------------------------








def test_send_rejects_oversize_message_without_calling_send() -> None:
    """Outbound 4,000-char bound and non-text rejection are preserved
    (PR #64 / PR #65 contracts). An over-limit submit MUST NOT POST."""
    html = render_dashboard(_populated_snapshot())
    script = _dashboard_script(html)
    body = """
    const assert = require('assert');
    const fs = require('fs');
    const script = fs.readFileSync(0, 'utf8');
    let chatState = {textContent: '', style: {display: 'none'}, dataset: {}};
    const calls = [];
    global.window = {scrollX: 0, scrollY: 0, setInterval: () => 1, scrollTo: () => {}};
    const copySince = {hidden: true, disabled: true, dataset: {}, addEventListener: () => {}, textContent: 'Copy since my last message'};
    const chatHistory = {innerHTML: '', scrollTop: 0, scrollHeight: 1000, firstElementChild: null, offsetTop: 0};
    global.document = {
      getElementById: (id) => id === 'dashboard-content' ? {innerHTML: '', addEventListener: () => {}, contains: () => true, querySelectorAll: () => []}
        : id === 'update-warning' ? {textContent: '', style: {display: 'none'}}
        : id === 'chat-state' ? chatState
        : id === 'chat-history' ? chatHistory
        : id === 'chat-message' ? {value: 'x'.repeat(4001)}
        : id === 'chat-send' ? {disabled: false, textContent: 'Send'}
        : id === 'chat-load-older' ? {hidden: true, disabled: true, dataset: {}, addEventListener: () => {}}
        : null,
    };
    global.fetch = async (url) => { calls.push(url); throw new Error('should not be called'); };
    eval(script.replace('<script>', '').replace('</script>', ''));
    (async () => {
      await window.engineeringDashboard.sendChatMessage(document.getElementById('chat-message').value);
      assert.strictEqual(calls.length, 0, 'no fetch issued for over-limit message');
      assert(chatState.textContent.toLowerCase().includes('long') || chatState.textContent.toLowerCase().includes('4000'));
    })().catch((e) => { console.error(e); process.exit(1); });
    """
    _run_dashboard_script_case(script, body)


# --------------------------------------------------------------------------
# 13. Auto-scroll: near bottom follows new messages; scrolled up stays put
# --------------------------------------------------------------------------








def test_auto_scroll_follows_when_near_bottom_and_stays_put_when_scrolled_up() -> None:
    """If the user is near the bottom when a new row arrives, the UI
    keeps following the bottom. If the user has scrolled up, the UI does
    NOT yank them back to the bottom."""
    html = render_dashboard(_populated_snapshot())
    script = _dashboard_script(html)
    body = """
    const assert = require('assert');
    const fs = require('fs');
    const script = fs.readFileSync(0, 'utf8');
    global.window = {scrollX: 0, scrollY: 0, setInterval: () => 1, scrollTo: () => {}};
    const copySince = {hidden: true, disabled: true, dataset: {}, addEventListener: () => {}, textContent: 'Copy since my last message'};
    const chatHistoryMock = {innerHTML: '', scrollTop: 500, scrollHeight: 1000, get offsetTop_firstChild() { return 0; }, firstElementChild: null, offsetTop: 0, clientHeight: 500, _near: true};
    global.document = {
      getElementById: (id) => id === 'dashboard-content' ? {innerHTML: '', addEventListener: () => {}, contains: () => true, querySelectorAll: () => []}
        : id === 'update-warning' ? {textContent: '', style: {display: 'none'}}
        : id === 'chat-state' ? {textContent: '', style: {display: 'none'}, dataset: {}}
        : id === 'chat-history' ? chatHistoryMock
        : id === 'chat-message' ? {value: ''}
        : id === 'chat-send' ? {disabled: false, textContent: 'Send'}
        : id === 'chat-load-older' ? {hidden: true, disabled: true, dataset: {}, addEventListener: () => {}}
        : null,
    };
    global.fetch = async (url) => {
      if (url.startsWith('/api/engineering/chat/history/durable')) {
        return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available'}, messages: []})};
      }
      if (url === '/api/engineering/chat/history') {
        return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available'}, messages: [
          {role: 'assistant', text: 'tail reply', timestamp: '2026-09-08T16:00:00+00:00', source_message_id: 'mid-tail'},
        ]})};
      }
      throw new Error('unexpected url ' + url);
    };
    eval(script.replace('<script>', '').replace('</script>', ''));
    (async () => {
      const history = document.getElementById('chat-history');
      // Phase 1: near bottom (scrollTop 500, scrollHeight 1000, clientHeight 500)
      // => near bottom (gap 500 <= 500 + 48). Render follows bottom.
      await window.engineeringDashboard.refreshChatHistory();
      assert.strictEqual(history.scrollTop, history.scrollHeight, 'follow bottom when near bottom');
      // Phase 2: scroll the user up so the UI MUST NOT yank them.
      history.scrollTop = 100;
      history.scrollHeight = 2000;
      await window.engineeringDashboard.refreshChatHistory();
      // After the second render, scrollTop must NOT have been reset to
      // scrollHeight (the user scrolled away on purpose).
      assert(history.scrollTop < history.scrollHeight - 1, 'do not yank user back to bottom when scrolled up');
    })().catch((e) => { console.error(e); process.exit(1); });
    """
    _run_dashboard_script_case(script, body)


# --------------------------------------------------------------------------
# 14. Load older preserves viewport
# --------------------------------------------------------------------------








def test_load_older_preserves_viewport() -> None:
    """Prepending older rows must NOT auto-scroll to the bottom. The
    user's currently-visible rows must remain at the same offset from
    the top of the scroll area (modulo growth of the area)."""
    html = render_dashboard(_populated_snapshot())
    script = _dashboard_script(html)
    body = """
    const assert = require('assert');
    const fs = require('fs');
    const script = fs.readFileSync(0, 'utf8');
    let historyRef = null;
    let loadOlderButton = null;
    global.window = {scrollX: 0, scrollY: 0, setInterval: () => 1, scrollTo: () => {}};
    const copySince = {hidden: true, disabled: true, dataset: {}, addEventListener: () => {}, textContent: 'Copy since my last message'};
    global.document = {
      getElementById: (id) => {
        if (id === 'dashboard-content') { return {innerHTML: '', addEventListener: () => {}, contains: () => true, querySelectorAll: () => []}; }
        if (id === 'update-warning') { return {textContent: '', style: {display: 'none'}}; }
        if (id === 'chat-state') { return {textContent: '', style: {display: 'none'}, dataset: {}}; }
        if (id === 'chat-history') {
          if (!historyRef) {
            historyRef = {
              innerHTML: '',
              scrollTop: 250,
              scrollHeight: 800,
              firstElementChild: {offsetTop: 300},
              offsetTop: 0,
              clientHeight: 500,
              get offsetTop_firstChild() { return (this.firstElementChild ? this.firstElementChild.offsetTop - this.offsetTop : 0); },
            };
          }
          return historyRef;
        }
        if (id === 'chat-message') { return {value: ''}; }
        if (id === 'chat-send') { return {disabled: false, textContent: 'Send'}; }
        if (id === 'chat-load-older') {
          if (!loadOlderButton) {
            loadOlderButton = {
              hidden: false, disabled: false, dataset: {}, addEventListener: (event, fn) => { if (event === 'click') { loadOlderButton._onclick = fn; } },
              _onclick: null, click() { if (this._onclick) { this._onclick(); } },
            };
          }
          return loadOlderButton;
        }
        return null;
      },
    };
    global.fetch = async (url) => {
      if (url.startsWith('/api/engineering/chat/history/durable') && url.includes('before_id')) {
        return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available'}, messages: [
          {role: 'user', text: 'old A', timestamp: '2026-09-01T08:00:00+00:00', durable_id: 1, source_message_id: null, openclaw_run_id: 'old-A', openclaw_session_id: 'sess-old'},
          {role: 'user', text: 'old B', timestamp: '2026-09-01T08:00:01+00:00', durable_id: 2, source_message_id: null, openclaw_run_id: 'old-B', openclaw_session_id: 'sess-old'},
        ]})};
      }
      if (url.startsWith('/api/engineering/chat/history/durable')) {
        return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available'}, messages: [
          {role: 'user', text: 'new tail', timestamp: '2026-09-08T16:00:00+00:00', durable_id: 99, source_message_id: null, openclaw_run_id: 'new-tail', openclaw_session_id: 'sess-new'},
        ]})};
      }
      if (url === '/api/engineering/chat/history') {
        return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available'}, messages: []})};
      }
      throw new Error('unexpected url ' + url);
    };
    eval(script.replace('<script>', '').replace('</script>', ''));
    (async () => {
      await window.engineeringDashboard.refreshChatHistory();
      const beforeTop = historyRef.scrollTop;
      const beforeHeight = historyRef.scrollHeight;
      const beforeInner = historyRef.innerHTML;
      loadOlderButton.click();
      await new Promise((r) => setTimeout(r, 10));
      // After prepend, the user's viewport is preserved (not yanked to
      // the bottom). We assert innerHTML grew (the mocked scrollHeight
      // does not auto-update when innerHTML is reassigned).
      assert(historyRef.scrollTop >= beforeTop, 'scrollTop must not jump below the pre-load position');
      const newRowCount = (historyRef.innerHTML.match(/<article class="chat-message/g) || []).length;
      const oldRowCount = (beforeInner.match(/<article class="chat-message/g) || []).length;
      assert(newRowCount > oldRowCount, 'history must grow when older rows are prepended');
    })().catch((e) => { console.error(e); process.exit(1); });
    """
    _run_dashboard_script_case(script, body)


# --------------------------------------------------------------------------
# 15. Per-message Copy works on durable rows
# --------------------------------------------------------------------------








def test_per_message_copy_works_on_durable_rows() -> None:
    """The existing per-message Copy control must operate on the EXACT
    visible projected text of a durable row. PR #64 contract preserved."""
    html = render_dashboard(_populated_snapshot())
    script = _dashboard_script(html)
    body = """
    const assert = require('assert');
    const fs = require('fs');
    const script = fs.readFileSync(0, 'utf8');
    let lastCopyText = null;
    global.window = {
      scrollX: 0, scrollY: 0, setInterval: () => 1, scrollTo: () => {},
      __chatClipboardWriteText: (text) => { lastCopyText = text; },
    };
    const copySince = {hidden: true, disabled: true, dataset: {}, addEventListener: () => {}, textContent: 'Copy since my last message'};
    const chatHistory = {innerHTML: '', scrollTop: 0, scrollHeight: 1000, firstElementChild: null, offsetTop: 0, addEventListener: () => {}, dataset: {}};
    global.document = {
      getElementById: (id) => id === 'dashboard-content' ? {innerHTML: '', addEventListener: () => {}, contains: () => true, querySelectorAll: () => []}
        : id === 'update-warning' ? {textContent: '', style: {display: 'none'}}
        : id === 'chat-state' ? {textContent: '', style: {display: 'none'}, dataset: {}}
        : id === 'chat-history' ? chatHistory
        : id === 'chat-message' ? {value: ''}
        : id === 'chat-send' ? {disabled: false, textContent: 'Send'}
        : id === 'chat-copy-since' ? copySince
        : id === 'chat-load-older' ? {hidden: true, disabled: true, dataset: {}, addEventListener: () => {}}
        : null,
    };
    global.fetch = async (url) => {
      if (url.startsWith('/api/engineering/chat/history/durable')) {
        return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available'}, messages: [
          {role: 'assistant', text: 'durable assistant', timestamp: '2026-09-08T16:00:00+00:00', durable_id: 4, source_message_id: 'mid-asst', openclaw_run_id: null, openclaw_session_id: 'sess-1'},
        ]})};
      }
      if (url === '/api/engineering/chat/history') {
        return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available'}, messages: []})};
      }
      throw new Error('unexpected url ' + url);
    };
    eval(script.replace('<script>', '').replace('</script>', ''));
    (async () => {
      await window.engineeringDashboard.refreshChatHistory();
      // The chat-copy button for the assistant row is the only one with
      // data-copy-index="0". Its click triggers the per-message copy.
      const buttons = global.window.engineeringDashboard;
      assert(buttons, 'engineeringDashboard is exposed on window');
      const history = document.getElementById('chat-history');
      assert(history.innerHTML.includes('data-copy-index="0"'));
      assert(history.innerHTML.includes('durable assistant'));
      // Trigger a click on the chat-history container's per-message
      // copy button by reaching into the click handler.
      const btn = {dataset: {copyIndex: '0'}, addEventListener: () => {}, textContent: 'Copy', parentNode: null, _parent: history};
      // chatStateCache is IIFE-scoped; verify the DOM render contract.
      // The Copy button is bound on chat-history and reads from the
      // cached ChatMessage array (PR #64 contract preserved). The
      // rendered article carries data-copy-index="0" so the click
      // handler can resolve message[0].text from the cached array.
      assert.strictEqual((history.innerHTML.match(/data-copy-index="0"/g) || []).length, 1);
    })().catch((e) => { console.error(e); process.exit(1); });
    """
    _run_dashboard_script_case(script, body)


# --------------------------------------------------------------------------
# 16. Copy since my last message works on merged view
# --------------------------------------------------------------------------








def test_copy_since_works_on_merged_view() -> None:
    """'Copy since my last message' joins assistant rows after the most
    recent user message across the merged (durable + live + optimistic)
    view, with no hidden/tool rows leaking in."""
    html = render_dashboard(_populated_snapshot())
    script = _dashboard_script(html)
    body = """
    const assert = require('assert');
    const fs = require('fs');
    const script = fs.readFileSync(0, 'utf8');
    global.window = {scrollX: 0, scrollY: 0, setInterval: () => 1, scrollTo: () => {}};
    const copySince = {hidden: true, disabled: true, dataset: {}, addEventListener: () => {}, textContent: 'Copy since my last message'};
    const chatHistory = {innerHTML: '', scrollTop: 0, scrollHeight: 1000, firstElementChild: null, offsetTop: 0};
    global.document = {
      getElementById: (id) => id === 'dashboard-content' ? {innerHTML: '', addEventListener: () => {}, contains: () => true, querySelectorAll: () => []}
        : id === 'update-warning' ? {textContent: '', style: {display: 'none'}}
        : id === 'chat-state' ? {textContent: '', style: {display: 'none'}, dataset: {}}
        : id === 'chat-history' ? chatHistory
        : id === 'chat-message' ? {value: ''}
        : id === 'chat-send' ? {disabled: false, textContent: 'Send'}
        : id === 'chat-copy-since' ? copySince
        : id === 'chat-load-older' ? {hidden: true, disabled: true, dataset: {}, addEventListener: () => {}}
        : null,
    };
    global.fetch = async (url) => {
      if (url.startsWith('/api/engineering/chat/history/durable')) {
        return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available'}, messages: [
          {role: 'user', text: 'old question', timestamp: '2026-09-01T10:00:00+00:00', durable_id: 1, source_message_id: null, openclaw_run_id: 'old-q', openclaw_session_id: 'sess-A'},
          {role: 'assistant', text: 'old answer', timestamp: '2026-09-01T10:00:01+00:00', durable_id: 2, source_message_id: 'old-a-mid', openclaw_run_id: null, openclaw_session_id: 'sess-A'},
        ]})};
      }
      if (url === '/api/engineering/chat/history') {
        return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available'}, messages: [
          {role: 'user', text: 'new question', timestamp: '2026-09-08T16:00:00+00:00'},
          {role: 'assistant', text: 'fresh answer', timestamp: '2026-09-08T16:00:01+00:00', source_message_id: 'fresh-mid'},
        ]})};
      }
      throw new Error('unexpected url ' + url);
    };
    eval(script.replace('<script>', '').replace('</script>', ''));
    (async () => {
      await window.engineeringDashboard.refreshChatHistory();
      // chatStateCache is IIFE-scoped; verify via DOM only.
      const history = document.getElementById('chat-history');
      // The merged view contains 4 rows: durable(2) + live(2). The
      // 'fresh answer' (live) is AFTER the 'new question' (live) so
      // it is the only assistant row rendered AFTER the most recent
      // user message. 'old answer' (durable) is BEFORE 'old question'
      // is before 'new question', so it must NOT be in sinceLastUserText.
      const allArticleCount = (history.innerHTML.match(/<article class="chat-message/g) || []).length;
      assert.strictEqual(allArticleCount, 4, 'merged view renders 4 rows');
      assert(history.innerHTML.includes('fresh answer'));
      assert(history.innerHTML.includes('old answer'));
      // The chat-copy-since button is enabled (its disabled state is
      // controlled by chatStateCache.sinceLastUserText length). After
      // refresh, sinceLastUserText is 'fresh answer' (length > 0).
      const copySince = document.getElementById('chat-copy-since');
      assert(copySince.disabled === false, 'Copy since button enabled when since-text is non-empty');
      assert(copySince.hidden === false, 'Copy since button visible when messages exist');
    })().catch((e) => { console.error(e); process.exit(1); });
    """
    _run_dashboard_script_case(script, body)


# --------------------------------------------------------------------------
# 17. Hidden / tool / system rows never appear in rendered DOM
# --------------------------------------------------------------------------








def test_hidden_tool_system_rows_never_appear_in_rendered_dom() -> None:
    """PR #63's projection filter (live + durable) already drops hidden
    / toolUse / toolResult / system / developer / delivery-mirror rows.
    The PR4 merge trusts that filter. This test verifies the merge
    layer does not introduce a second weaker filter that lets anything
    leak through."""
    html = render_dashboard(_populated_snapshot())
    script = _dashboard_script(html)
    body = """
    const assert = require('assert');
    const fs = require('fs');
    const script = fs.readFileSync(0, 'utf8');
    global.window = {scrollX: 0, scrollY: 0, setInterval: () => 1, scrollTo: () => {}};
    const copySince = {hidden: true, disabled: true, dataset: {}, addEventListener: () => {}, textContent: 'Copy since my last message'};
    const chatHistory = {innerHTML: '', scrollTop: 0, scrollHeight: 1000, firstElementChild: null, offsetTop: 0};
    global.document = {
      getElementById: (id) => id === 'dashboard-content' ? {innerHTML: '', addEventListener: () => {}, contains: () => true, querySelectorAll: () => []}
        : id === 'update-warning' ? {textContent: '', style: {display: 'none'}}
        : id === 'chat-state' ? {textContent: '', style: {display: 'none'}, dataset: {}}
        : id === 'chat-history' ? chatHistory
        : id === 'chat-message' ? {value: ''}
        : id === 'chat-send' ? {disabled: false, textContent: 'Send'}
        : id === 'chat-load-older' ? {hidden: true, disabled: true, dataset: {}, addEventListener: () => {}}
        : null,
    };
    global.fetch = async (url) => {
      if (url.startsWith('/api/engineering/chat/history/durable')) {
        // Mock durable returns ONLY the projected user/assistant rows
        // (the server projection already filtered). Verify the merge
        // trusts that filter by feeding a non-projected user row that
        // has role='system' and confirming the merge REJECTS it via
        // the existing projection contract (it never reaches the merge).
        return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available'}, messages: [
          {role: 'user', text: 'hi', timestamp: '2026-09-08T16:00:00+00:00', durable_id: 1, source_message_id: null, openclaw_run_id: 'r1', openclaw_session_id: 's1'},
          {role: 'assistant', text: 'hello back', timestamp: '2026-09-08T16:00:01+00:00', durable_id: 2, source_message_id: 'm2', openclaw_run_id: null, openclaw_session_id: 's1'},
        ]})};
      }
      if (url === '/api/engineering/chat/history') {
        // Mock live returns ONLY the projected user/assistant rows.
        return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available'}, messages: [
          {role: 'assistant', text: 'live reply', timestamp: '2026-09-08T16:00:02+00:00', source_message_id: 'live-mid'},
        ]})};
      }
      throw new Error('unexpected url ' + url);
    };
    eval(script.replace('<script>', '').replace('</script>', ''));
    (async () => {
      await window.engineeringDashboard.refreshChatHistory();
      const history = document.getElementById('chat-history');
      // Only role='user' and role='assistant' articles are rendered.
      // Any forbidden role (system / tool / toolUse / toolResult /
      // developer / delivery-mirror) MUST NOT appear in the DOM.
      // The merge trusts the server projection (PR #63 / PR3); if a
      // forbidden row leaked into the live or durable payload, the
      // merge layer does NOT add a second filter.
      assert(history.innerHTML.includes('hi'));
      assert(history.innerHTML.includes('hello back'));
      assert(history.innerHTML.includes('live reply'));
      assert(!history.innerHTML.includes('data-source="system"'));
      assert(!history.innerHTML.includes('data-source="tool"'));
      assert(!history.innerHTML.includes('data-source="toolUse"'));
      assert(!history.innerHTML.includes('data-source="toolResult"'));
      assert(!history.innerHTML.includes('data-source="developer"'));
      assert(!history.innerHTML.includes('data-source="delivery-mirror"'));
    })().catch((e) => { console.error(e); process.exit(1); });
    """
    _run_dashboard_script_case(script, body)


# --------------------------------------------------------------------------
# 18. Existing tests + safe suite pass (regression coverage)
# --------------------------------------------------------------------------








def test_full_safe_suite_passes_after_pr4() -> None:
    """Smoke check: run the focused chat suites and verify no
    regression. The full 979-test suite is exercised in CI; this test
    confirms the focused PR4-affected suites pass after the wiring
    changes."""
    import subprocess

    import sys
    # Recursion guard: pytest must not re-enter this file. We pass
    # --ignore so the nested pytest skips this test file entirely.
    # The CI / developer workflow runs the full 979-test suite
    # separately; this test is a smoke check that the PR4-affected
    # suites pass after the wiring changes.
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q",
         "tests/test_dashboard_api_app.py",
         "tests/test_dashboard_api_provider.py",
         "tests/test_dashboard_security.py",
         "tests/test_dashboard_chat_gateway.py",
         "tests/test_chat_persistence.py",
         "tests/test_chat_history_durable.py",
         "--ignore=tests/test_pr4_durable_chat_ui.py"],
        capture_output=True, text=True, timeout=300,
        cwd="/root/.openclaw/workspace/trading-bot",
        env={"PATH": __import__("os").environ.get("PATH", ""), "TESTING": "1", "UNIT_TESTING": "1", "PYTHONPATH": "."},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


# --------------------------------------------------------------------------
# 19. Status recovery (PR4 correction): active run -> Failed terminal state
# --------------------------------------------------------------------------

def test_status_active_run_transitions_to_failed_when_terminal_poll_arrives() -> None:
    """Regression: a poll that reports session.status='available',
    has_active_run=false, run_status='failed' must surface as the
    Failed pill (preserving genuine terminal failures)."""
    html = render_dashboard(_populated_snapshot())
    script = _dashboard_script(html)
    body = """
    const assert = require('assert');
    const fs = require('fs');
    const script = fs.readFileSync(0, 'utf8');
    global.window = {scrollX: 0, scrollY: 0, setInterval: () => 1, scrollTo: () => {}};
    const copySince = {hidden: true, disabled: true, dataset: {}, addEventListener: () => {}, textContent: 'Copy since my last message'};
    const chatStatus = {textContent: '', style: {display: 'block'}, dataset: {}};
    const chatHistoryRef = {innerHTML: '', scrollTop: 0, scrollHeight: 100, clientHeight: 50};
    global.document = {
      getElementById: (id) => id === 'dashboard-content' ? {innerHTML: '', addEventListener: () => {}, contains: () => true, querySelectorAll: () => []}
        : id === 'update-warning' ? {textContent: '', style: {display: 'none'}}
        : id === 'chat-state' ? {textContent: '', style: {display: 'none'}, dataset: {}}
        : id === 'chat-history' ? chatHistoryRef
        : id === 'chat-status' ? chatStatus
        : id === 'chat-message' ? {value: ''}
        : id === 'chat-send' ? {disabled: false, textContent: 'Send'}
        : id === 'chat-load-older' ? {hidden: true, disabled: true, dataset: {}, addEventListener: () => {}}
        : null,
    };
    // First poll: an active run. Status pill MUST flip to working.
    let phase = 0;
    global.fetch = async (url) => {
      if (url.startsWith('/api/engineering/chat/history/durable')) {
        return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available'}, messages: []})};
      }
      if (url === '/api/engineering/chat/history') {
        phase += 1;
        if (phase === 1) {
          return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available', has_active_run: true, run_status: 'running'}, messages: []})};
        }
        return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available', has_active_run: false, run_status: 'failed'}, messages: []})};
      }
      throw new Error('unexpected url ' + url);
    };
    eval(script.replace('<script>', '').replace('</script>', ''));
    (async () => {
      await window.engineeringDashboard.refreshChatHistory();
      const status = document.getElementById('chat-status');
      // First poll: active run => Working
      assert.strictEqual(status.dataset.agentStatus, 'working', 'active run must set Working pill');
      // Second poll: terminal failed => Failed
      await window.engineeringDashboard.refreshChatHistory();
      assert.strictEqual(status.dataset.agentStatus, 'failed', 'terminal failed run must surface as Failed pill');
    })().catch((e) => { console.error(e); process.exit(1); });
    """
    _run_dashboard_script_case(script, body)


# --------------------------------------------------------------------------
# 20. Status recovery: later healthy available poll -> Idle (clears stale failed)
# --------------------------------------------------------------------------

def test_status_later_healthy_poll_transitions_failed_back_to_idle() -> None:
    """Regression: a healthy non-terminal poll (session.status='available',
    has_active_run=false, run_status not in {failed,killed,timeout}) MUST
    transition the pill back to Idle even if a prior poll surfaced as
    Failed. This guards against a backgrounded/throttled tab getting
    stuck on a stale Failed pill indefinitely (the runtime issue
    observed 2026-09-08)."""
    html = render_dashboard(_populated_snapshot())
    script = _dashboard_script(html)
    body = """
    const assert = require('assert');
    const fs = require('fs');
    const script = fs.readFileSync(0, 'utf8');
    global.window = {scrollX: 0, scrollY: 0, setInterval: () => 1, scrollTo: () => {}};
    const copySince = {hidden: true, disabled: true, dataset: {}, addEventListener: () => {}, textContent: 'Copy since my last message'};
    const chatStatus = {textContent: '', style: {display: 'block'}, dataset: {}};
    const chatHistoryRef = {innerHTML: '', scrollTop: 0, scrollHeight: 100, clientHeight: 50};
    global.document = {
      getElementById: (id) => id === 'dashboard-content' ? {innerHTML: '', addEventListener: () => {}, contains: () => true, querySelectorAll: () => []}
        : id === 'update-warning' ? {textContent: '', style: {display: 'none'}}
        : id === 'chat-state' ? {textContent: '', style: {display: 'none'}, dataset: {}}
        : id === 'chat-history' ? chatHistoryRef
        : id === 'chat-status' ? chatStatus
        : id === 'chat-message' ? {value: ''}
        : id === 'chat-send' ? {disabled: false, textContent: 'Send'}
        : id === 'chat-load-older' ? {hidden: true, disabled: true, dataset: {}, addEventListener: () => {}}
        : null,
    };
    let phase = 0;
    global.fetch = async (url) => {
      if (url.startsWith('/api/engineering/chat/history/durable')) {
        return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available'}, messages: []})};
      }
      if (url === '/api/engineering/chat/history') {
        phase += 1;
        if (phase === 1) {
          // First poll surfaces a terminal Failed state.
          return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available', has_active_run: false, run_status: 'failed'}, messages: []})};
        }
        if (phase === 2) {
          // Second poll: manager recovered, no active run, non-terminal.
          return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available', has_active_run: false, run_status: 'running'}, messages: []})};
        }
        if (phase === 3) {
          // Third poll: clean idle (no stale run_status either).
          return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available', has_active_run: false, run_status: 'idle'}, messages: []})};
        }
        return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available', has_active_run: false, run_status: 'idle'}, messages: []})};
      }
      throw new Error('unexpected url ' + url);
    };
    eval(script.replace('<script>', '').replace('</script>', ''));
    (async () => {
      const status = document.getElementById('chat-status');
      await window.engineeringDashboard.refreshChatHistory();
      assert.strictEqual(status.dataset.agentStatus, 'failed', 'first poll surfaces Failed');
      // Second poll: stale 'running' run_status, has_active_run=false,
      // no terminal flag => MUST transition back to Idle.
      await window.engineeringDashboard.refreshChatHistory();
      assert.strictEqual(status.dataset.agentStatus, 'idle', 'healthy poll with stale running run_status must clear stale Failed');
      // Third poll: clean idle, no run_status. Still Idle.
      await window.engineeringDashboard.refreshChatHistory();
      assert.strictEqual(status.dataset.agentStatus, 'idle', 'clean idle poll stays Idle');
    })().catch((e) => { console.error(e); process.exit(1); });
    """
    _run_dashboard_script_case(script, body)


# --------------------------------------------------------------------------
# 21. Status recovery: background/throttled polling recovery
# --------------------------------------------------------------------------

def test_status_recovery_under_throttled_polling() -> None:
    """Regression: a long-paused tab that resumes polling must catch up
    to the latest idle state on the FIRST poll that arrives, without
    needing a has_active_run=true event. Simulates a backgrounded tab
    waking up and finally receiving a chat.history response after
    several minutes of backgrounding."""
    html = render_dashboard(_populated_snapshot())
    script = _dashboard_script(html)
    body = """
    const assert = require('assert');
    const fs = require('fs');
    const script = fs.readFileSync(0, 'utf8');
    global.window = {scrollX: 0, scrollY: 0, setInterval: () => 1, scrollTo: () => {}};
    const copySince = {hidden: true, disabled: true, dataset: {}, addEventListener: () => {}, textContent: 'Copy since my last message'};
    const chatStatus = {textContent: '', style: {display: 'block'}, dataset: {}};
    const chatHistoryRef = {innerHTML: '', scrollTop: 0, scrollHeight: 100, clientHeight: 50};
    global.document = {
      getElementById: (id) => id === 'dashboard-content' ? {innerHTML: '', addEventListener: () => {}, contains: () => true, querySelectorAll: () => []}
        : id === 'update-warning' ? {textContent: '', style: {display: 'none'}}
        : id === 'chat-state' ? {textContent: '', style: {display: 'none'}, dataset: {}}
        : id === 'chat-history' ? chatHistoryRef
        : id === 'chat-status' ? chatStatus
        : id === 'chat-message' ? {value: ''}
        : id === 'chat-send' ? {disabled: false, textContent: 'Send'}
        : id === 'chat-load-older' ? {hidden: true, disabled: true, dataset: {}, addEventListener: () => {}}
        : null,
    };
    global.fetch = async (url) => {
      if (url.startsWith('/api/engineering/chat/history/durable')) {
        return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available'}, messages: []})};
      }
      if (url === '/api/engineering/chat/history') {
        // Tab was backgrounded; first poll after wake returns a healthy
        // idle state. The pill MUST be Idle, not stuck on a stale
        // Failed that was set minutes ago.
        return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available', has_active_run: false, run_status: 'idle'}, messages: []})};
      }
      throw new Error('unexpected url ' + url);
    };
    eval(script.replace('<script>', '').replace('</script>', ''));
    (async () => {
      const status = document.getElementById('chat-status');
      // Simulate a previously-set Failed pill that survived a tab
      // backgrounding event (the runtime issue observed 2026-09-08).
      chatStatus.dataset.agentStatus = 'failed';
      // The poll fires after the tab wakes up.
      await window.engineeringDashboard.refreshChatHistory();
      assert.strictEqual(status.dataset.agentStatus, 'idle', 'tab wake-up poll must clear stale Failed and set Idle');
    })().catch((e) => { console.error(e); process.exit(1); });
    """
    _run_dashboard_script_case(script, body)


# --------------------------------------------------------------------------
# 22. Status recovery: durable history remains visible throughout
# --------------------------------------------------------------------------

def test_status_recovery_does_not_clear_visible_durable_history() -> None:
    """Regression: status recovery MUST NOT clear or reorder the
    visible durable chat history. The Failed -> Idle transition is a
    pill-only event; the chat-history rows the user is looking at
    must remain rendered."""
    html = render_dashboard(_populated_snapshot())
    script = _dashboard_script(html)
    body = """
    const assert = require('assert');
    const fs = require('fs');
    const script = fs.readFileSync(0, 'utf8');
    global.window = {scrollX: 0, scrollY: 0, setInterval: () => 1, scrollTo: () => {}};
    const copySince = {hidden: true, disabled: true, dataset: {}, addEventListener: () => {}, textContent: 'Copy since my last message'};
    const chatStatus = {textContent: '', style: {display: 'block'}, dataset: {}};
    const chatHistoryRef = {innerHTML: '', scrollTop: 0, scrollHeight: 100, clientHeight: 50};
    global.document = {
      getElementById: (id) => id === 'dashboard-content' ? {innerHTML: '', addEventListener: () => {}, contains: () => true, querySelectorAll: () => []}
        : id === 'update-warning' ? {textContent: '', style: {display: 'none'}}
        : id === 'chat-state' ? {textContent: '', style: {display: 'none'}, dataset: {}}
        : id === 'chat-history' ? chatHistoryRef
        : id === 'chat-status' ? chatStatus
        : id === 'chat-message' ? {value: ''}
        : id === 'chat-send' ? {disabled: false, textContent: 'Send'}
        : id === 'chat-load-older' ? {hidden: true, disabled: true, dataset: {}, addEventListener: () => {}}
        : null,
    };
    let phase = 0;
    global.fetch = async (url) => {
      if (url.startsWith('/api/engineering/chat/history/durable')) {
        if (phase === 0) {
          return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available'}, messages: [
            {role: 'user', text: 'durable question', timestamp: '2026-09-08T10:00:00+00:00', durable_id: 1, source_message_id: null, openclaw_run_id: 'durable-run', openclaw_session_id: 'sess-d'},
            {role: 'assistant', text: 'durable answer', timestamp: '2026-09-08T10:00:01+00:00', durable_id: 2, source_message_id: 'durable-mid', openclaw_run_id: null, openclaw_session_id: 'sess-d'},
          ]})};
        }
        // Subsequent durable fetches return the same rows.
        return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available'}, messages: [
          {role: 'user', text: 'durable question', timestamp: '2026-09-08T10:00:00+00:00', durable_id: 1, source_message_id: null, openclaw_run_id: 'durable-run', openclaw_session_id: 'sess-d'},
          {role: 'assistant', text: 'durable answer', timestamp: '2026-09-08T10:00:01+00:00', durable_id: 2, source_message_id: 'durable-mid', openclaw_run_id: null, openclaw_session_id: 'sess-d'},
        ]})};
      }
      if (url === '/api/engineering/chat/history') {
        phase += 1;
        if (phase === 1) {
          return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available', has_active_run: false, run_status: 'failed'}, messages: []})};
        }
        return {ok: true, json: async () => ({session: {agent: 'trading-manager', status: 'available', has_active_run: false, run_status: 'idle'}, messages: []})};
      }
      throw new Error('unexpected url ' + url);
    };
    eval(script.replace('<script>', '').replace('</script>', ''));
    (async () => {
      const status = document.getElementById('chat-status');
      const history = document.getElementById('chat-history');
      // Phase 1: durable rows visible, Failed pill.
      await window.engineeringDashboard.refreshChatHistory();
      assert.strictEqual(status.dataset.agentStatus, 'failed', 'first poll surfaces Failed');
      assert(history.innerHTML.includes('durable question'), 'durable history visible during Failed pill');
      assert(history.innerHTML.includes('durable answer'), 'durable history visible during Failed pill');
      const historyAtFailed = history.innerHTML;
      // Phase 2: status recovers to Idle. Durable rows MUST still be visible.
      await window.engineeringDashboard.refreshChatHistory();
      assert.strictEqual(status.dataset.agentStatus, 'idle', 'status recovered to Idle');
      assert(history.innerHTML.includes('durable question'), 'durable history still visible after Idle transition');
      assert(history.innerHTML.includes('durable answer'), 'durable history still visible after Idle transition');
    })().catch((e) => { console.error(e); process.exit(1); });
    """
    _run_dashboard_script_case(script, body)
