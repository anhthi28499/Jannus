"""Build Claude prompt from GitHub event + graph state (review loop, RAG, human input)."""

from __future__ import annotations

import json
import logging
from typing import Any

from jannus.agents.state import JannusState
from jannus.config import Settings

logger = logging.getLogger("jannus.prompt_builder")

SAFETY_RULES = """
QUY TẮC BẮT BUỘC (tuân thủ nghiêm):
1. LUÔN tạo branch mới cho thay đổi; KHÔNG commit trực tiếp lên main/master nếu đó là branch bảo vệ.
2. Đặt tên branch rõ ràng, ví dụ: fix/gh-issue-<số> hoặc fix/ci-<mô-tả-ngắn>.
3. Sau khi sửa, chạy test/linter phù hợp với project để xác nhận không làm hỏng thêm.
4. Khi đã sẵn sàng, push branch và tạo Pull Request (dùng `gh pr create` nếu có GitHub CLI và quyền).
5. KHÔNG xóa file không liên quan; KHÔNG thay đổi biến môi trường production hay secrets.
6. Nếu không đủ thông tin để sửa an toàn, ghi rõ trong commit message hoặc PR body thay vì đoán mò.
""".strip()


def _json_snippet(obj: Any, max_len: int = 8000) -> str:
    try:
        s = json.dumps(obj, ensure_ascii=False, indent=2, default=str)
    except TypeError:
        s = str(obj)
    if len(s) > max_len:
        return s[: max_len - 20] + "\n... [truncated]"
    return s


def _comment_matches_trigger(body: str, keywords: list[str]) -> bool:
    if not body.strip() or not keywords:
        return False
    lower = body.lower()
    for kw in keywords:
        if kw.lower() in lower:
            return True
    return False


def build_base_prompt(
    event: str,
    payload: dict[str, Any],
    *,
    trigger_keywords: list[str] | None = None,
) -> str | None:
    repo = payload.get("repository") or {}
    full_name = repo.get("full_name") or "unknown/repo"
    html_url = repo.get("html_url") or ""

    if event == "ping":
        return None

    if event == "push":
        ref = payload.get("ref") or ""
        commits = payload.get("commits") or []
        messages = []
        for c in commits[:20]:
            if isinstance(c, dict):
                messages.append(c.get("message", "").strip().split("\n")[0])
        head = (
            f"Có push mới vào repository `{full_name}`.\n"
            f"Ref: {ref}\nURL: {html_url}\n"
        )
        if messages:
            head += "Commit messages (rút gọn):\n" + "\n".join(f"- {m}" for m in messages) + "\n"
        head += (
            "\nRepo đã được clone/cập nhật trong workspace. Làm việc trong working tree hiện tại.\n"
            "Đọc diff/commit vừa có (nếu cần), chạy test/lint phù hợp. Nếu phát hiện lỗi hoặc CI sẽ fail, "
            "sửa trên branch mới và chuẩn bị PR.\n"
        )
        return f"{SAFETY_RULES}\n\n{head}"

    if event == "issues":
        action = payload.get("action")
        issue = payload.get("issue") or {}
        if action not in ("opened", "reopened", "labeled", "assigned"):
            return None
        title = issue.get("title") or ""
        body = issue.get("body") or ""
        num = issue.get("number")
        url = issue.get("html_url") or ""
        text = (
            f"Issue trên GitHub `{full_name}` (#{num}): {title}\n"
            f"URL: {url}\n"
            f"Mô tả:\n{body}\n\n"
            "Hãy phân tích codebase trong thư mục hiện tại, triển khai fix hoặc cải tiến phù hợp trên branch mới, "
            "chạy kiểm tra cần thiết, push và tạo PR.\n"
        )
        return f"{SAFETY_RULES}\n\n{text}"

    if event == "issue_comment":
        action = payload.get("action")
        if action != "created":
            return None
        comment = payload.get("comment") or {}
        comment_body = comment.get("body") or ""
        kws = trigger_keywords or []
        if not _comment_matches_trigger(comment_body, kws):
            return None
        issue = payload.get("issue") or {}
        title = issue.get("title") or ""
        issue_body = issue.get("body") or ""
        num = issue.get("number")
        url = issue.get("html_url") or ""
        text = (
            f"Có comment yêu cầu xử lý trên issue/PR `{full_name}` (#{num}): {title}\n"
            f"URL: {url}\n"
            f"Mô tả issue:\n{issue_body}\n\n"
            f"Comment:\n{comment_body}\n\n"
            "Hãy phân tích codebase, thực hiện thay đổi phù hợp trên branch mới, "
            "chạy kiểm tra cần thiết, push và tạo PR.\n"
        )
        return f"{SAFETY_RULES}\n\n{text}"

    if event == "workflow_run":
        wr = payload.get("workflow_run") or {}
        status = wr.get("status")
        conclusion = wr.get("conclusion")
        name = wr.get("name") or wr.get("display_title") or "workflow"
        html = wr.get("html_url") or ""
        branch = (wr.get("head_branch") or "") or ""
        if status != "completed":
            return None
        if conclusion not in ("failure", "cancelled", "timed_out", "action_required"):
            return None
        text = (
            f"Workflow GitHub Actions đã kết thúc với kết quả: {conclusion}.\n"
            f"Tên: {name}\nBranch: {branch}\nURL log: {html}\n"
            f"Repository: {full_name}\n\n"
            "Hãy xem log CI (qua `gh run view` hoặc mở URL), tái hiện lỗi local nếu có thể, "
            "sửa code trên branch mới, chạy lại test/lint, push và tạo PR mô tả rõ nguyên nhân.\n"
        )
        return f"{SAFETY_RULES}\n\n{text}"

    if event == "check_suite":
        cs = payload.get("check_suite") or {}
        conclusion = cs.get("conclusion")
        if conclusion not in ("failure", "timed_out", "cancelled", "action_required"):
            return None
        head_branch = cs.get("head_branch") or ""
        text = (
            f"Check suite kết thúc: {conclusion}.\n"
            f"Branch: {head_branch}\nRepo: {full_name}\n\n"
            "Hãy điều tra (log CI, test local), sửa trên branch mới và tạo PR.\n"
        )
        return f"{SAFETY_RULES}\n\n{text}"

    text = (
        f"Sự kiện GitHub: `{event}`.\n"
        f"Payload (rút gọn JSON):\n{_json_snippet(payload)}\n\n"
        "Hãy đánh giá xem có cần hành động trên codebase không; nếu có, làm trên branch mới và PR.\n"
    )
    return f"{SAFETY_RULES}\n\n{text}"


def _rag_context(settings: Settings, state: JannusState) -> str:
    if not settings.rag_enabled or not state.get("repo_local_path"):
        return ""
    try:
        from jannus.rag.retriever import retrieve_context

        q = (state.get("planner_summary") or "") + " " + json.dumps(state.get("payload") or {}, default=str)[:2000]
        return retrieve_context(settings, state["repo_local_path"], q[:4000])
    except Exception as e:
        logger.warning("RAG optional context failed: %s", e)
        return ""


def build_prompt_for_graph(settings: Settings, state: JannusState) -> dict[str, Any]:
    """Return partial state update with ``prompt`` or ``skip_graph`` / ``error``."""
    if state.get("skip_graph"):
        return {}
    event = state.get("event") or ""
    payload = state.get("payload") or {}
    kws = settings.parsed_trigger_keywords()
    base = build_base_prompt(event, payload, trigger_keywords=kws)
    if base is None:
        return {"skip_graph": True, "error": "no prompt for this event"}

    extra_parts: list[str] = []
    if state.get("planner_summary"):
        extra_parts.append(f"[Planner]\n{state['planner_summary']}\n")
    rag = _rag_context(settings, state)
    if rag:
        extra_parts.append(f"[Optional context from repo index]\n{rag}\n")
    if state.get("review_feedback"):
        extra_parts.append(f"[Reviewer feedback — address this]\n{state['review_feedback']}\n")
    if state.get("human_response"):
        extra_parts.append(f"[Human guidance]\n{state['human_response']}\n")

    prefix = "\n".join(extra_parts) if extra_parts else ""
    full = f"{prefix}\n{base}" if prefix else base

    if settings.webhook_dry_run:
        logger.info("WEBHOOK_DRY_RUN prompt (%s chars):\n%s", len(full), full[:4000])

    out: dict[str, Any] = {"prompt": full}
    if state.get("human_response"):
        out["human_response"] = None
    return out
