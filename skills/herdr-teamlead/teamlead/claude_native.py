"""Claude Code's completed-turn evidence, read from its own session transcript.

Claude Code appends JSONL to `<config>/projects/<slug>/<session-id>.jsonl`, one
row per API content block: rows sharing `message.id` are one assistant message
ordered by `apiBlockIndex`, and every row carrying a `uuid` links to the one
before it through `parentUuid`. Blocks of one message repeat that message's
`stop_reason`, which is null on a block written before the message settled.
Verified on Claude Code 2.1.263 under Herdr 0.8.2.

Nothing here reads rendered pane text. A completed final message is the last
main-chain assistant message: `end_turn`, built from text and thinking blocks
alone, with no user turn after it. Bookkeeping rows (`system`, `attachment`,
`mode`, `ai-title`, `last-prompt`, ...) carry no task, so they neither complete
nor reset a turn -- but a row that DECLARES itself a turn is held to the turn
contract even when it carries none of a turn's fields. Parallel tool calls put
a `user` tool-result row BETWEEN two blocks of one assistant message; a user
row that is not tool output is a new turn and ends the pending message. A
broken parent chain, a subagent row on the main chain, a foreign session id,
contradictory completion metadata, or an unreadable shape stays unconfirmed.
"""

import os
from pathlib import Path

#: Content blocks that produce no rendered message text. Every other block type
#: -- `tool_use` above all -- means the message is still doing work, so its
#: text is not a final answer.
SILENT_BLOCKS = ("thinking", "redacted_thinking")

#: Row types that speak for the worker or the operator, and are therefore held
#: to the turn contract in `main_chain`.
TURN_TYPES = ("user", "assistant")

#: Directory depth from the sessions root to the transcript's own directory:
#: `<config>/projects/<slug>/<session-id>.jsonl`.
SESSION_DEPTH = 1


def sessions_root():
    """Where Claude Code keeps per-project transcripts."""
    return Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))) / "projects"


def transcript_name(session):
    return session + ".jsonl"


def tool_output(body):
    """True for a user row that carries nothing but tool results."""
    content = body.get("content")
    return (isinstance(content, list) and bool(content)
            and all(isinstance(block, dict) and block.get("type") == "tool_result"
                    for block in content))


def main_chain(rows, session):
    """The session's own non-subagent rows in order; a rewound file is None."""
    chain, head = [], None
    for row in rows:
        kind, body = row.get("type"), row.get("message")
        turn = kind in TURN_TYPES
        # A declared turn is a turn even with every one of its fields missing.
        # Reading such a row as bookkeeping would let it slip past the chain
        # and leave an earlier answer standing as the latest completed one.
        carries_turn = turn or "uuid" in row or "message" in row
        identifier = row.get("sessionId")
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
            if carries_turn:
                return None
            continue
        if not isinstance(row["uuid"], str) or not row["uuid"] or row.get("parentUuid") != head:
            return None
        if turn and (not isinstance(body, dict) or body.get("role") != kind):
            return None
        # Bookkeeping never speaks as the worker or the operator.
        if not turn and isinstance(body, dict) and body.get("role") in TURN_TYPES:
            return None
        head = row["uuid"]
        chain.append(row)
    return chain


def final_message(rows, session):
    """The latest completed assistant message's text, joined block by block."""
    chain = main_chain(rows, session)
    if chain is None:
        return None
    message, reason, text, blocks, complete, usable = None, None, "", 0, False, False
    for row in chain:
        kind = row.get("type")
        if kind == "user":
            # A tool result belongs to the message that asked for it. Every
            # other user row opens a new turn, and so does any row that follows
            # a completed answer.
            if complete or not tool_output(row["message"]):
                message, reason, text, blocks, complete, usable = None, None, "", 0, False, False
            continue
        if kind != "assistant":
            continue
        body, index = row["message"], row.get("apiBlockIndex")
        if not isinstance(body.get("id"), str) or not body["id"] or type(index) is not int:
            return None
        if body["id"] != message:
            # A message whose first block is missing was truncated or replaced.
            if index != 0:
                return None
            message, reason, text, usable = body["id"], None, "", True
        elif index != blocks:
            return None
        settled = body.get("stop_reason")
        if settled is not None:
            # Blocks of one message repeat its outcome. A null block has not
            # settled yet; two different outcomes are contradictory evidence,
            # never a completed answer one sibling can vouch for.
            if reason is not None and settled != reason:
                return None
            reason = settled
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
            complete = bool(text) and reason == "end_turn"
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
        content = row["message"].get("content")
        if isinstance(content, str):
            latest = content
        elif (isinstance(content, list) and content
                and all(isinstance(block, dict) and block.get("type") == "text"
                        and isinstance(block.get("text"), str) for block in content)):
            latest = "".join(block["text"] for block in content)
        else:
            return None
    return latest
