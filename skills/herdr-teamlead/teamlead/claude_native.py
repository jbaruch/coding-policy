"""Claude Code's completed-turn evidence, read from its own session transcript.

Claude Code appends JSONL to `<config>/projects/<slug>/<session-id>.jsonl`, one
row per API content block: rows sharing `message.id` are one assistant message
ordered by `apiBlockIndex`, and every row carrying a `uuid` links to the one
before it through `parentUuid`. Verified on Claude Code 2.1.263 under Herdr
0.8.2.

Nothing here reads rendered pane text. A completed final message is the last
main-chain assistant message: `end_turn`, built from text and thinking blocks
alone, with no user turn after it. Bookkeeping rows (`system`, `attachment`,
`mode`, `ai-title`, `last-prompt`, ...) carry no task, so they neither complete
nor reset a turn. Parallel tool calls put a `user` tool-result row BETWEEN two
blocks of one assistant message, so a user row ends the turn only after a
message that already completed. A broken parent chain, a subagent row on the
main chain, a foreign session id, or an unreadable shape stays unconfirmed.
"""

import os
from pathlib import Path

#: Content blocks that produce no rendered message text. Every other block type
#: -- `tool_use` above all -- means the message is still doing work, so its
#: text is not a final answer.
SILENT_BLOCKS = ("thinking", "redacted_thinking")

#: Directory depth from the sessions root to the transcript's own directory:
#: `<config>/projects/<slug>/<session-id>.jsonl`.
SESSION_DEPTH = 1


def sessions_root():
    """Where Claude Code keeps per-project transcripts."""
    return Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))) / "projects"


def transcript_name(session):
    return session + ".jsonl"


def main_chain(rows, session):
    """The session's own non-subagent rows in order; a rewound file is None."""
    chain, head = [], None
    for row in rows:
        identifier = row.get("sessionId")
        carries_turn = "uuid" in row or "message" in row
        if identifier is None:
            # Snapshot bookkeeping is written without a session stamp; a row
            # that carries a turn without one is not this session's evidence.
            if carries_turn:
                return None
            continue
        if identifier != session:
            return None
        if row.get("isSidechain") is True:
            continue
        if "uuid" not in row:
            if "message" in row:
                return None
            continue
        if not isinstance(row["uuid"], str) or not row["uuid"] or row.get("parentUuid") != head:
            return None
        head = row["uuid"]
        chain.append(row)
    return chain


def final_message(rows, session):
    """The latest completed assistant message's text, joined block by block."""
    chain = main_chain(rows, session)
    if chain is None:
        return None
    message, text, blocks, complete, usable = None, "", 0, False, False
    for row in chain:
        kind = row.get("type")
        if kind not in ("user", "assistant"):
            body = row.get("message")
            # Bookkeeping never speaks as the worker or the operator.
            if isinstance(body, dict) and body.get("role") in ("user", "assistant"):
                return None
            continue
        body, index = row.get("message"), row.get("apiBlockIndex")
        if not isinstance(body, dict) or body.get("role") != kind:
            return None
        if kind == "user":
            # A tool result inside a still-running message is not a new turn;
            # a user row after a completed answer always is.
            if complete:
                message, text, blocks, complete, usable = None, "", 0, False, False
            continue
        if not isinstance(body.get("id"), str) or not body["id"] or type(index) is not int:
            return None
        if body["id"] != message:
            # A message whose first block is missing was truncated or replaced.
            if index != 0:
                return None
            message, text, usable = body["id"], "", True
        elif index != blocks:
            return None
        blocks, complete = index + 1, False
        content = body.get("content")
        if not isinstance(content, list) or not content:
            return None
        for block in content:
            if not isinstance(block, dict):
                return None
            if block.get("type") == "text":
                if not isinstance(block.get("text"), str):
                    return None
                text += block["text"]
            elif block.get("type") not in SILENT_BLOCKS:
                usable = False
        if usable:
            complete = bool(text) and body.get("stop_reason") == "end_turn"
    return text if complete else None


def prompt_text(rows, session):
    """The latest human-typed prompt; injected and quoted rows never count."""
    chain = main_chain(rows, session)
    if chain is None:
        return None
    latest = None
    for row in chain:
        origin = row.get("origin")
        if (row.get("type") != "user" or row.get("isMeta") is True
                or row.get("promptSource") != "typed"
                or not isinstance(origin, dict) or origin.get("kind") != "human"):
            continue
        body = row.get("message")
        if not isinstance(body, dict) or body.get("role") != "user":
            return None
        content = body.get("content")
        if isinstance(content, str):
            latest = content
        elif (isinstance(content, list) and content
                and all(isinstance(block, dict) and block.get("type") == "text"
                        and isinstance(block.get("text"), str) for block in content)):
            latest = "".join(block["text"] for block in content)
        else:
            return None
    return latest
