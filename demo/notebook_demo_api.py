"""Isolated, mutable API for previewing the real Notebook Agent frontend.

The server is intentionally dependency-free and in-memory. It exercises the
same HTTP contracts as the product UI while avoiding production persistence,
authentication, queues, and model calls. The three seeded answers paraphrase
the public captions at the cited timestamps; unsupported questions fail closed.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import RLock
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse
from uuid import uuid4


HOST = "127.0.0.1"
PORT = 8000
MAX_BODY_BYTES = 64 * 1024

CAPABILITIES = {
    "archive": False,
    "browser_companion": True,
    "chat": True,
    "max_save_batch_size": 10,
    "save_enabled": False,
    "summary_generation": False,
    "supported_platforms": ["youtube"],
    "transcript_pagination": True,
    "web_login_channels": ["email"],
}

ITEMS = [
    {
        "public_id": "demo-how-to-talk-to-users",
        "platform": "youtube",
        "kind": "video",
        "url": "https://www.youtube.com/watch?v=MT4Ig2uqjTc",
        "title": "How to Talk to Users",
        "author": "Eric Migicovsky",
        "published_at": None,
        "duration_sec": None,
        "lang": "en",
        "description": None,
        "tags": ["产品调研", "用户访谈"],
        "chapters": [],
        "cover_url": "https://i.ytimg.com/vi/MT4Ig2uqjTc/hqdefault.jpg",
        "saved_at": "2026-08-20T09:30:00Z",
        "why_saved": "本地演示数据 · 准备用户访谈 #产品调研",
        "text_source": "youtube_captions",
        "lifecycle": "ready",
        "error_code": None,
        "available_actions": ["edit_why_saved"],
        "latest_dispatch_public_id": None,
        "summary": None,
    },
    {
        "public_id": "demo-neural-network",
        "platform": "youtube",
        "kind": "video",
        "url": "https://www.youtube.com/watch?v=aircAruvnKk",
        "title": "But what is a neural network?",
        "author": "3Blue1Brown",
        "published_at": None,
        "duration_sec": None,
        "lang": "en",
        "description": None,
        "tags": ["AI入门", "神经网络"],
        "chapters": [],
        "cover_url": "https://i.ytimg.com/vi/aircAruvnKk/hqdefault.jpg",
        "saved_at": "2026-08-19T14:20:00Z",
        "why_saved": "本地演示数据 · 复习基础概念 #AI入门",
        "text_source": "youtube_captions",
        "lifecycle": "ready",
        "error_code": None,
        "available_actions": ["edit_why_saved"],
        "latest_dispatch_public_id": None,
        "summary": None,
    },
    {
        "public_id": "demo-effective-practice",
        "platform": "youtube",
        "kind": "video",
        "url": "https://www.youtube.com/watch?v=f2O6mQkFiiw",
        "title": "How to practice effectively...for just about anything",
        "author": "TED-Ed",
        "published_at": None,
        "duration_sec": None,
        "lang": "en",
        "description": None,
        "tags": ["学习方法"],
        "chapters": [],
        "cover_url": "https://i.ytimg.com/vi/f2O6mQkFiiw/hqdefault.jpg",
        "saved_at": "2026-08-18T08:10:00Z",
        "why_saved": "本地演示数据 · 提炼练习方法 #学习方法",
        "text_source": "youtube_captions",
        "lifecycle": "ready",
        "error_code": None,
        "available_actions": ["edit_why_saved"],
        "latest_dispatch_public_id": None,
        "summary": None,
    },
]

ANSWER_TEMPLATES = {
    "user_interviews": {
        "assistant_text": (
            "最常见的三个错误是：\n"
            "1. 把访谈变成产品推销，反而收不到真实信息；\n"
            "2. 问假设性的未来，而不是追问最近一次真实经历；\n"
            "3. 自己讲得太多，没有真正倾听和记录。\n\n"
            "更稳妥的做法，是围绕已经发生的具体事件追问时间、行为和投入。"
        ),
        "citations": [
            {
                "title": "How to Talk to Users · Eric Migicovsky",
                "excerpt": "用户访谈不是推销产品的时机，目标是提取信息。",
                "url": "https://www.youtube.com/watch?v=MT4Ig2uqjTc&t=217",
                "start_sec": 217,
            },
            {
                "title": "How to Talk to Users · Eric Migicovsky",
                "excerpt": "追问最近一次发生的具体经历，而不是让用户回答假设问题。",
                "url": "https://www.youtube.com/watch?v=MT4Ig2uqjTc&t=496",
                "start_sec": 496,
            },
            {
                "title": "How to Talk to Users · Eric Migicovsky",
                "excerpt": "克制自己表达的欲望，认真倾听并做笔记。",
                "url": "https://www.youtube.com/watch?v=MT4Ig2uqjTc&t=336",
                "start_sec": 336,
            },
        ],
    },
    "neural_network": {
        "assistant_text": (
            "这段视频把识别过程拆成三步：\n"
            "1. 28×28 的图片被展开成 784 个输入神经元，每个激活值表示一个像素的灰度；\n"
            "2. 激活值逐层向前传播，网络逐渐组合出更高层的特征；\n"
            "3. 输出层有 10 个神经元，分别对应数字 0—9，激活最亮的节点就是预测结果。"
        ),
        "citations": [
            {
                "title": "But what is a neural network? · 3Blue1Brown",
                "excerpt": "28×28 图像对应 784 个输入神经元，激活值表示像素灰度。",
                "url": "https://www.youtube.com/watch?v=aircAruvnKk&t=183",
                "start_sec": 183,
            },
            {
                "title": "But what is a neural network? · 3Blue1Brown",
                "excerpt": "输出层包含 10 个神经元，分别代表十个数字。",
                "url": "https://www.youtube.com/watch?v=aircAruvnKk&t=226",
                "start_sec": 226,
            },
            {
                "title": "But what is a neural network? · 3Blue1Brown",
                "excerpt": "激活从一层传到下一层，最亮的输出节点给出网络的选择。",
                "url": "https://www.youtube.com/watch?v=aircAruvnKk&t=322",
                "start_sec": 322,
            },
        ],
    },
    "effective_practice": {
        "assistant_text": (
            "不要只累计时长，更要提高每次练习的质量：\n"
            "1. 固定练习节奏，保持高度专注；\n"
            "2. 选择略高于当前熟练度的任务，针对能力边缘反复练；\n"
            "3. 把练习拆成多次短时段，并在重复之间安排休息。\n\n"
            "因此，一次有效练习应当目标单一、难度适中，并且可以被持续复盘。"
        ),
        "citations": [
            {
                "title": "How to practice effectively · TED-Ed",
                "excerpt": "掌握技能不仅取决于练习小时数，也取决于练习的质量和有效性。",
                "url": "https://www.youtube.com/watch?v=f2O6mQkFiiw&t=135",
                "start_sec": 135,
            },
            {
                "title": "How to practice effectively · TED-Ed",
                "excerpt": "有效练习应当稳定、专注，并瞄准当前能力边缘。",
                "url": "https://www.youtube.com/watch?v=f2O6mQkFiiw&t=144",
                "start_sec": 144,
            },
            {
                "title": "How to practice effectively · TED-Ed",
                "excerpt": "频繁重复并穿插休息，把练习拆成有限时长的日常时段。",
                "url": "https://www.youtube.com/watch?v=f2O6mQkFiiw&t=198",
                "start_sec": 198,
            },
        ],
    },
}

CONVERSATION_SEEDS = [
    {
        "thread_id": "demo-thread-user-interviews",
        "conversation_id": "demo-conversation-user-interviews",
        "title": "用户访谈最容易犯什么错？",
        "preview": "别把访谈变成产品推销；追问已经发生的具体经历。",
        "updated_at": "2026-08-20T10:20:00Z",
        "question": "用户访谈时，最容易犯哪些错误？",
        "answer_key": "user_interviews",
    },
    {
        "thread_id": "demo-thread-neural-network",
        "conversation_id": "demo-conversation-neural-network",
        "title": "神经网络如何识别手写数字？",
        "preview": "784 个输入神经元接收像素，10 个输出对应数字。",
        "updated_at": "2026-08-20T10:10:00Z",
        "question": "神经网络怎样识别手写数字？",
        "answer_key": "neural_network",
    },
    {
        "thread_id": "demo-thread-effective-practice",
        "conversation_id": "demo-conversation-effective-practice",
        "title": "怎样安排一次更有效的练习？",
        "preview": "稳定、专注，并把练习放在当前能力边缘。",
        "updated_at": "2026-08-20T10:00:00Z",
        "question": "怎样安排一次更有效的练习？",
        "answer_key": "effective_practice",
    },
]

TRANSCRIPTS_BY_ITEM = {
    "demo-how-to-talk-to-users": [
        {
            "ordinal": 1,
            "start_sec": 217,
            "end_sec": 245,
            "text": "用户访谈不是推销产品的时机，目标是提取信息。",
            "source_url": "https://www.youtube.com/watch?v=MT4Ig2uqjTc&t=217",
        },
        {
            "ordinal": 2,
            "start_sec": 336,
            "end_sec": 365,
            "text": "克制自己表达的欲望，认真倾听并做笔记。",
            "source_url": "https://www.youtube.com/watch?v=MT4Ig2uqjTc&t=336",
        },
        {
            "ordinal": 3,
            "start_sec": 496,
            "end_sec": 530,
            "text": "追问最近一次发生的具体经历，而不是让用户回答假设问题。",
            "source_url": "https://www.youtube.com/watch?v=MT4Ig2uqjTc&t=496",
        },
    ],
    "demo-neural-network": [
        {
            "ordinal": 1,
            "start_sec": 183,
            "end_sec": 207,
            "text": "28×28 图像对应 784 个输入神经元，激活值表示像素灰度。",
            "source_url": "https://www.youtube.com/watch?v=aircAruvnKk&t=183",
        },
        {
            "ordinal": 2,
            "start_sec": 226,
            "end_sec": 250,
            "text": "输出层包含 10 个神经元，分别代表十个数字。",
            "source_url": "https://www.youtube.com/watch?v=aircAruvnKk&t=226",
        },
        {
            "ordinal": 3,
            "start_sec": 322,
            "end_sec": 350,
            "text": "激活从一层传到下一层，最亮的输出节点给出网络的选择。",
            "source_url": "https://www.youtube.com/watch?v=aircAruvnKk&t=322",
        },
    ],
    "demo-effective-practice": [
        {
            "ordinal": 1,
            "start_sec": 135,
            "end_sec": 144,
            "text": "掌握技能不仅取决于练习小时数，也取决于练习的质量和有效性。",
            "source_url": "https://www.youtube.com/watch?v=f2O6mQkFiiw&t=135",
        },
        {
            "ordinal": 2,
            "start_sec": 144,
            "end_sec": 174,
            "text": "有效练习应当稳定、专注，并瞄准当前能力边缘。",
            "source_url": "https://www.youtube.com/watch?v=f2O6mQkFiiw&t=144",
        },
        {
            "ordinal": 3,
            "start_sec": 198,
            "end_sec": 228,
            "text": "频繁重复并穿插休息，把练习拆成有限时长的日常时段。",
            "source_url": "https://www.youtube.com/watch?v=f2O6mQkFiiw&t=198",
        },
    ],
}


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _turn(question: str, answer_key: str, created_at: str) -> dict[str, Any]:
    answer = ANSWER_TEMPLATES[answer_key]
    return {
        "user_text": question,
        "assistant_text": answer["assistant_text"],
        "status": "ok",
        "error_code": None,
        "citations": deepcopy(answer["citations"]),
        "action_results": [],
        "created_at": created_at,
    }


def _answer_key(question: str) -> str | None:
    normalized = question.casefold()
    matches = (
        ("user_interviews", ("访谈", "用户调研", "推销")),
        ("neural_network", ("神经网络", "手写数字", "像素", "784")),
        ("effective_practice", ("有效练习", "练习", "复习", "学习方法")),
    )
    return next((key for key, words in matches if any(word in normalized for word in words)), None)


class DemoState:
    """Thread-safe in-memory state reset for every server process."""

    def __init__(self) -> None:
        self._lock = RLock()
        self.items = deepcopy(ITEMS)
        self.conversations: list[dict[str, Any]] = []
        self.turns_by_thread: dict[str, dict[str, Any]] = {}
        self.thread_by_conversation: dict[str, str] = {}
        for seed in CONVERSATION_SEEDS:
            history = {key: seed[key] for key in (
                "thread_id", "conversation_id", "title", "preview", "updated_at"
            )}
            self.conversations.append(history)
            self.thread_by_conversation[seed["conversation_id"]] = seed["thread_id"]
            self.turns_by_thread[seed["thread_id"]] = {
                "thread_id": seed["thread_id"],
                "conversation_id": seed["conversation_id"],
                "turns": [_turn(seed["question"], seed["answer_key"], seed["updated_at"])],
            }

    def list_conversations(self, limit: int) -> list[dict[str, Any]]:
        with self._lock:
            ordered = sorted(
                self.conversations,
                key=lambda item: item["updated_at"],
                reverse=True,
            )
            return deepcopy(ordered[:limit])

    def get_turns(self, thread_id: str) -> dict[str, Any] | None:
        with self._lock:
            value = self.turns_by_thread.get(thread_id)
            return deepcopy(value) if value is not None else None

    def create_conversation(self, conversation_id: str) -> dict[str, Any]:
        with self._lock:
            existing_thread = self.thread_by_conversation.get(conversation_id)
            if existing_thread is not None:
                return self._response(existing_thread, "ok", "", [])
            thread_id = f"demo-thread-{uuid4().hex}"
            timestamp = _now()
            self.conversations.insert(0, {
                "thread_id": thread_id,
                "conversation_id": conversation_id,
                "title": "新的检索",
                "preview": "等待第一个问题",
                "updated_at": timestamp,
            })
            self.thread_by_conversation[conversation_id] = thread_id
            self.turns_by_thread[thread_id] = {
                "thread_id": thread_id,
                "conversation_id": conversation_id,
                "turns": [],
            }
            return self._response(thread_id, "ok", "", [])

    def send_message(self, conversation_id: str, question: str) -> dict[str, Any] | None:
        with self._lock:
            thread_id = self.thread_by_conversation.get(conversation_id)
            if thread_id is None:
                return None
            timestamp = _now()
            answer_key = _answer_key(question)
            if answer_key is None:
                answer_text = (
                    "当前三个示例视频中没有找到足够依据。"
                    "可以尝试询问用户访谈、神经网络或有效练习。"
                )
                status = "not_found"
                citations: list[dict[str, Any]] = []
            else:
                template = ANSWER_TEMPLATES[answer_key]
                answer_text = str(template["assistant_text"])
                status = "ok"
                citations = deepcopy(template["citations"])
            turn = {
                "user_text": question,
                "assistant_text": answer_text,
                "status": status,
                "error_code": None,
                "citations": citations,
                "action_results": [],
                "created_at": timestamp,
            }
            self.turns_by_thread[thread_id]["turns"].append(turn)
            history = next(item for item in self.conversations if item["thread_id"] == thread_id)
            history.update({
                "title": question if len(question) <= 28 else f"{question[:28]}…",
                "preview": answer_text if len(answer_text) <= 42 else f"{answer_text[:42]}…",
                "updated_at": timestamp,
            })
            return self._response(thread_id, status, answer_text, citations)

    def delete_conversation(self, thread_id: str) -> bool:
        with self._lock:
            turns = self.turns_by_thread.pop(thread_id, None)
            if turns is None:
                return False
            conversation_id = str(turns["conversation_id"])
            self.thread_by_conversation.pop(conversation_id, None)
            self.conversations = [
                item for item in self.conversations if item["thread_id"] != thread_id
            ]
            return True

    def list_items(self) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy(self.items)

    def get_item(self, public_id: str) -> dict[str, Any] | None:
        with self._lock:
            item = next((entry for entry in self.items if entry["public_id"] == public_id), None)
            return deepcopy(item) if item is not None else None

    def update_why_saved(self, public_id: str, why_saved: str | None) -> dict[str, Any] | None:
        with self._lock:
            item = next((entry for entry in self.items if entry["public_id"] == public_id), None)
            if item is None:
                return None
            item["why_saved"] = why_saved
            return deepcopy(item)

    @staticmethod
    def _response(
        thread_id: str,
        status: str,
        text: str,
        citations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "status": status,
            "text": text,
            "citations": deepcopy(citations),
            "action_results": [],
            "thread_id": thread_id,
            "error_code": None,
        }


class DemoApiHandler(BaseHTTPRequestHandler):
    server_version = "NotebookAgentDemo/2.0"
    demo_state: DemoState

    def _json(self, status: int, payload: object) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, code: str, message: str) -> None:
        self._json(status, {"code": code, "message": message})

    def _body(self) -> dict[str, Any] | None:
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._error(400, "invalid_request", "Content-Length is invalid")
            return None
        if size > MAX_BODY_BYTES:
            self._error(413, "request_too_large", "Demo request body is too large")
            return None
        try:
            payload = json.loads(self.rfile.read(size) or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._error(400, "invalid_json", "Request body must be valid JSON")
            return None
        if not isinstance(payload, dict):
            self._error(400, "invalid_request", "Request body must be an object")
            return None
        return payload

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/v1/health":
            self._json(200, {"status": "ok", "mode": "local-ui-demo"})
            return
        if path == "/api/v1/auth/session":
            self._json(200, {
                "authenticated": True,
                "expires_at": "2099-12-31T23:59:59Z",
                "login_channel": "email",
            })
            return
        if path == "/api/v1/capabilities":
            self._json(200, CAPABILITIES)
            return
        if path == "/api/v1/conversations":
            query = parse_qs(parsed.query)
            try:
                limit = max(1, min(100, int(query.get("limit", ["30"])[0])))
            except ValueError:
                self._error(400, "invalid_request", "Conversation limit must be a number")
                return
            self._json(200, {
                "items": self.demo_state.list_conversations(limit),
                "next_cursor": None,
            })
            return

        conversation_prefix = "/api/v1/conversations/"
        if path.startswith(conversation_prefix) and path.endswith("/turns"):
            thread_id = unquote(path[len(conversation_prefix) : -len("/turns")])
            conversation = self.demo_state.get_turns(thread_id)
            if conversation is None:
                self._error(404, "not_found", "Conversation not found")
            else:
                self._json(200, conversation)
            return

        if path == "/api/v1/library/items":
            self._list_library(parsed.query)
            return

        item_prefix = "/api/v1/library/items/"
        if path.startswith(item_prefix):
            item_path = unquote(path[len(item_prefix) :])
            public_id = item_path.split("/", 1)[0]
            item = self.demo_state.get_item(public_id)
            if item is None:
                self._error(404, "not_found", "Library item not found")
                return
            if item_path.endswith("/transcript"):
                self._json(200, {
                    "blocks": deepcopy(TRANSCRIPTS_BY_ITEM[public_id]),
                    "next_cursor": None,
                })
            else:
                self._json(200, item)
            return

        self._error(404, "not_found", "Demo endpoint not found")

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        path = urlparse(self.path).path
        prefix = "/api/v1/conversations/"
        if not path.startswith(prefix):
            self._error(404, "not_found", "Demo endpoint not found")
            return
        suffix = unquote(path[len(prefix) :])
        if suffix.endswith("/reset"):
            conversation_id = suffix[: -len("/reset")]
            if not conversation_id:
                self._error(400, "invalid_request", "Conversation ID is required")
                return
            self._json(200, self.demo_state.create_conversation(conversation_id))
            return
        if suffix.endswith("/messages"):
            conversation_id = suffix[: -len("/messages")]
            payload = self._body()
            if payload is None:
                return
            message_id = payload.get("message_id")
            text = payload.get("text")
            if not isinstance(message_id, str) or not message_id.strip():
                self._error(400, "invalid_request", "Message ID is required")
                return
            if not isinstance(text, str) or not text.strip():
                self._error(400, "invalid_request", "Message text is required")
                return
            if len(text) > 2_000:
                self._error(413, "request_too_large", "Message is too long for the demo")
                return
            response = self.demo_state.send_message(conversation_id, text.strip())
            if response is None:
                self._error(404, "not_found", "Conversation not found")
            else:
                self._json(200, response)
            return
        self._error(404, "not_found", "Demo endpoint not found")

    def do_DELETE(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        path = urlparse(self.path).path
        prefix = "/api/v1/conversations/"
        if not path.startswith(prefix):
            self._error(404, "not_found", "Demo endpoint not found")
            return
        thread_id = unquote(path[len(prefix) :])
        if "/" in thread_id or not self.demo_state.delete_conversation(thread_id):
            self._error(404, "not_found", "Conversation not found")
            return
        self.send_response(204)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def do_PATCH(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        path = urlparse(self.path).path
        prefix = "/api/v1/library/items/"
        if not path.startswith(prefix):
            self._error(404, "not_found", "Demo endpoint not found")
            return
        public_id = unquote(path[len(prefix) :])
        if "/" in public_id:
            self._error(404, "not_found", "Demo endpoint not found")
            return
        payload = self._body()
        if payload is None:
            return
        why_saved = payload.get("why_saved")
        if why_saved is not None and not isinstance(why_saved, str):
            self._error(400, "invalid_request", "why_saved must be text or null")
            return
        item = self.demo_state.update_why_saved(public_id, why_saved)
        if item is None:
            self._error(404, "not_found", "Library item not found")
        else:
            self._json(200, item)

    def _list_library(self, query_string: str) -> None:
        query = parse_qs(query_string)
        selected = self.demo_state.list_items()
        search = query.get("search", [""])[0].strip().casefold()
        collection = query.get("collection", [""])[0].strip().casefold()
        lifecycle = query.get("lifecycle", [""])[0].strip()
        if search:
            selected = [
                item for item in selected
                if search in " ".join(
                    str(item.get(key) or "") for key in ("title", "author", "why_saved")
                ).casefold()
            ]
        if collection:
            selected = [
                item for item in selected
                if f"#{collection}" in str(item.get("why_saved") or "").casefold()
            ]
        if lifecycle:
            selected = [item for item in selected if item["lifecycle"] == lifecycle]
        sort = query.get("sort", ["saved_desc"])[0]
        if sort == "saved_asc":
            selected.sort(key=lambda item: item["saved_at"])
        elif sort == "title_asc":
            selected.sort(key=lambda item: str(item["title"]).casefold())
        else:
            selected.sort(key=lambda item: item["saved_at"], reverse=True)
        try:
            page = max(1, int(query.get("page", ["1"])[0]))
            page_size = max(1, min(100, int(query.get("page_size", ["20"])[0])))
        except ValueError:
            self._error(400, "invalid_request", "Page values must be numbers")
            return
        start = (page - 1) * page_size
        self._json(200, {
            "items": selected[start : start + page_size],
            "total": len(selected),
            "page": page,
            "page_size": page_size,
            "is_true_first_empty": False,
        })

    def log_message(self, _format: str, *args: object) -> None:
        # User-entered questions and search strings must not leak to the console.
        return


def create_server(
    host: str = HOST,
    port: int = PORT,
    state: DemoState | None = None,
) -> ThreadingHTTPServer:
    demo_state = state or DemoState()

    class BoundDemoApiHandler(DemoApiHandler):
        pass

    BoundDemoApiHandler.demo_state = demo_state
    return ThreadingHTTPServer((host, port), BoundDemoApiHandler)


def main() -> None:
    server = create_server()
    print(
        f"Notebook Agent local product demo listening on http://{HOST}:{PORT}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
