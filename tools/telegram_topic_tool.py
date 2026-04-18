"""Telegram Topic Tool -- manage Telegram forum topic lifecycle.

Create, close, reopen, delete, rename, or list observed topics in a Telegram
forum supergroup.  Targets use the same ``telegram:<chat_id>[:<thread_id>]``
format as ``send_message``; topic lifecycle stays decoupled from message
delivery so neither tool's schema carries the other's surface area.

Errors are mapped to structured codes so LLM callers can branch
programmatically:

    no_rights        -> bot lacks admin + can_manage_topics
    topic_not_found  -> thread_id invalid or message thread missing
    chat_not_found   -> chat_id unknown to Telegram
    topic_closed     -> operation requires the topic to be open
    unknown          -> anything else

``action='list'`` is NOT authoritative -- Telegram's Bot API does not expose a
``getForumTopics`` endpoint, so we return topics observed via incoming
messages (``source='observed_sessions'``).  A freshly created topic will not
appear until someone posts in it.
"""

import asyncio
import json
import logging
from typing import Optional, Tuple

from tools.registry import registry, tool_error
from tools.send_message_tool import (
    _check_send_message,
    _parse_target_ref,
    _sanitize_error_text,
    _telegram_retry_delay,
)

logger = logging.getLogger(__name__)


TELEGRAM_TOPIC_SCHEMA = {
    "name": "telegram_topic",
    "description": (
        "Manage Telegram forum topics: create, close, reopen, delete, rename, "
        "or list observed topics. The bot must be an administrator of the forum "
        "with can_manage_topics rights.\n\n"
        "Targets use the same format as send_message: "
        "'telegram:<chat_id>' for action='create'/'list', "
        "'telegram:<chat_id>:<thread_id>' for close/reopen/delete/rename.\n\n"
        "Errors return a structured 'code' field: 'no_rights', 'topic_not_found', "
        "'chat_not_found', 'topic_closed', or 'unknown'.\n\n"
        "IMPORTANT: action='list' only returns topics observed via incoming "
        "messages (source='observed_sessions'). It is NOT authoritative -- a "
        "topic that has never received a message will not appear."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "close", "reopen", "delete", "rename", "list"],
                "description": "Topic operation to perform.",
            },
            "target": {
                "type": "string",
                "description": (
                    "Target in 'telegram:<chat_id>[:<thread_id>]' form. "
                    "create/list omit thread_id; close/reopen/delete/rename require it. "
                    "Example: 'telegram:-1001234567890' or 'telegram:-1001234567890:17585'."
                ),
            },
            "name": {
                "type": "string",
                "description": "Topic name (1-128 chars). Required for action='create' and action='rename'.",
            },
            "confirm": {
                "type": "boolean",
                "description": "Required for action='delete' (must be true). Safety guard against accidental deletion.",
            },
        },
        "required": ["action", "target"],
    },
}


def telegram_topic_tool(args, **_kw):
    """Dispatch a telegram_topic call to the right action handler."""
    action = (args.get("action") or "").strip().lower()
    target = args.get("target", "")

    if action not in {"create", "close", "reopen", "delete", "rename", "list"}:
        return tool_error(f"Unknown action '{action}'. Use one of: create, close, reopen, delete, rename, list.")

    chat_id, thread_id, err = _parse_telegram_target(target)
    if err:
        return tool_error(err)

    if action == "list":
        return _handle_list(chat_id)

    if action == "create":
        name = (args.get("name") or "").strip()
        if not name:
            return tool_error("action='create' requires 'name' (1-128 chars)")
        if thread_id is not None:
            return tool_error("action='create' target must be 'telegram:<chat_id>' without thread_id")
        return _handle_write_op("create", chat_id=chat_id, thread_id=None, name=name)

    # close / reopen / delete / rename all need thread_id
    if thread_id is None:
        return tool_error(f"action='{action}' requires 'telegram:<chat_id>:<thread_id>' target")

    if action == "rename":
        name = (args.get("name") or "").strip()
        if not name:
            return tool_error("action='rename' requires 'name' (1-128 chars)")
        return _handle_write_op("rename", chat_id=chat_id, thread_id=thread_id, name=name)

    if action == "delete":
        if args.get("confirm") is not True:
            return json.dumps({
                "error": "action='delete' requires 'confirm': true to guard against accidental deletion",
                "code": "confirm_required",
            })
        return _handle_write_op("delete", chat_id=chat_id, thread_id=thread_id)

    return _handle_write_op(action, chat_id=chat_id, thread_id=thread_id)


def _parse_telegram_target(target: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Parse 'telegram:<chat_id>[:<thread_id>]' -> (chat_id, thread_id, error)."""
    if not target:
        return None, None, "'target' is required"
    parts = target.split(":", 1)
    platform = parts[0].strip().lower()
    if platform != "telegram":
        return None, None, f"telegram_topic only supports telegram targets, got '{platform}'"
    if len(parts) < 2 or not parts[1].strip():
        return None, None, "target must be 'telegram:<chat_id>[:<thread_id>]'"
    chat_id, thread_id, is_explicit = _parse_target_ref("telegram", parts[1].strip())
    if not is_explicit or not chat_id:
        return None, None, f"Could not parse '{parts[1].strip()}' as telegram chat_id or chat_id:thread_id"
    return chat_id, thread_id, None


def _load_telegram_token() -> Tuple[Optional[str], Optional[str]]:
    """Return (token, error) for the configured Telegram bot."""
    try:
        from gateway.config import Platform, load_gateway_config
        config = load_gateway_config()
    except Exception as e:
        return None, _sanitize_error_text(f"Failed to load gateway config: {e}")

    pconfig = config.platforms.get(Platform.TELEGRAM)
    if not pconfig or not pconfig.enabled or not pconfig.token:
        return None, "Telegram is not configured. Set TELEGRAM_TOKEN in ~/.hermes/config.yaml or env."
    return pconfig.token, None


def _classify_error(err: Exception) -> str:
    """Map a Telegram API exception to a stable error code."""
    text = str(err).lower()
    if "topic_closed" in text:
        return "topic_closed"
    if "not enough rights" in text or "chat_admin_required" in text or "administrator rights" in text:
        return "no_rights"
    if "message thread not found" in text or "topic_id_invalid" in text or "thread not found" in text:
        return "topic_not_found"
    if "chat not found" in text:
        return "chat_not_found"
    return "unknown"


async def _with_retry(coro_factory, *, attempts: int = 3):
    """Retry transient Telegram failures (429 / 5xx / timeouts)."""
    for attempt in range(attempts):
        try:
            return await coro_factory()
        except Exception as exc:
            delay = _telegram_retry_delay(exc, attempt)
            if delay is None or attempt >= attempts - 1:
                raise
            logger.warning(
                "Transient Telegram topic failure (attempt %d/%d), retrying in %.1fs: %s",
                attempt + 1, attempts, delay, _sanitize_error_text(exc),
            )
            await asyncio.sleep(delay)


async def _run_topic_op(token: str, op: str, *, chat_id: str, thread_id: Optional[str], name: Optional[str]):
    """Invoke the underlying python-telegram-bot call for a single op."""
    from telegram import Bot

    bot = Bot(token=token)
    int_chat_id = int(chat_id)

    if op == "create":
        topic = await _with_retry(
            lambda: bot.create_forum_topic(chat_id=int_chat_id, name=name)
        )
        return {
            "success": True,
            "platform": "telegram",
            "action": "create",
            "chat_id": chat_id,
            "thread_id": str(topic.message_thread_id),
            "name": topic.name,
        }

    int_thread_id = int(thread_id)
    if op == "close":
        await _with_retry(lambda: bot.close_forum_topic(chat_id=int_chat_id, message_thread_id=int_thread_id))
    elif op == "reopen":
        await _with_retry(lambda: bot.reopen_forum_topic(chat_id=int_chat_id, message_thread_id=int_thread_id))
    elif op == "delete":
        await _with_retry(lambda: bot.delete_forum_topic(chat_id=int_chat_id, message_thread_id=int_thread_id))
    elif op == "rename":
        await _with_retry(
            lambda: bot.edit_forum_topic(chat_id=int_chat_id, message_thread_id=int_thread_id, name=name)
        )
    else:
        return {"error": f"Unknown op: {op}", "code": "unknown"}

    return {
        "success": True,
        "platform": "telegram",
        "action": op,
        "chat_id": chat_id,
        "thread_id": str(int_thread_id),
    }


def _handle_write_op(op: str, *, chat_id: str, thread_id: Optional[str], name: Optional[str] = None) -> str:
    """Execute a write op (create/close/reopen/delete/rename) and JSON-encode the result."""
    token, err = _load_telegram_token()
    if err:
        return tool_error(err)

    try:
        from model_tools import _run_async
        result = _run_async(_run_topic_op(token, op, chat_id=chat_id, thread_id=thread_id, name=name))
    except ImportError:
        return json.dumps({
            "error": "python-telegram-bot not installed. Run: pip install python-telegram-bot",
            "code": "unknown",
        })
    except Exception as e:
        return json.dumps({
            "error": _sanitize_error_text(f"Telegram {op} failed: {e}"),
            "code": _classify_error(e),
        })
    return json.dumps(result)


def _handle_list(chat_id: str) -> str:
    """Return topics observed via incoming messages for a given chat_id."""
    topics = _list_topics_from_sessions(chat_id)
    return json.dumps({
        "success": True,
        "platform": "telegram",
        "action": "list",
        "chat_id": chat_id,
        "source": "observed_sessions",
        "topics": topics,
        "note": (
            "Telegram Bot API does not expose a topic enumeration method; this list "
            "only includes topics where a message has been observed by the gateway."
        ),
    })


def _list_topics_from_sessions(chat_id: str) -> list:
    """Read sessions.json and collect {thread_id, name} entries matching chat_id."""
    try:
        from hermes_cli.config import get_hermes_home
    except Exception:
        return []

    sessions_path = get_hermes_home() / "sessions" / "sessions.json"
    if not sessions_path.exists():
        return []

    try:
        with open(sessions_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.debug("telegram_topic list: failed to read sessions.json: %s", e)
        return []

    target_chat = str(chat_id)
    seen = {}
    for _key, session in data.items():
        origin = session.get("origin") or {}
        if origin.get("platform") != "telegram":
            continue
        if str(origin.get("chat_id", "")) != target_chat:
            continue
        thread_id = origin.get("thread_id")
        if not thread_id:
            continue
        tkey = str(thread_id)
        if tkey in seen:
            continue
        seen[tkey] = {
            "thread_id": tkey,
            "name": origin.get("chat_topic") or f"topic {thread_id}",
            "chat_name": origin.get("chat_name"),
        }
    return list(seen.values())


registry.register(
    name="telegram_topic",
    toolset="messaging",
    schema=TELEGRAM_TOPIC_SCHEMA,
    handler=telegram_topic_tool,
    check_fn=_check_send_message,
    emoji="🧵",
)
