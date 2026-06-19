# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Ambient expense-approval agent implemented as an ADK 2.0 graph workflow."""

import base64
import json
import re
from typing import Literal

from google.adk.agents import LlmAgent
from google.adk.agents.context import Context
from google.adk.apps import App, ResumabilityConfig
from google.adk.events.event import Event
from google.adk.events.request_input import RequestInput
from google.adk.workflow import Workflow
from google.genai import types
from pydantic import BaseModel, Field

from .config import config

# ---------------------------------------------------------------------------
# Pydantic schemas for structured data flow between nodes
# ---------------------------------------------------------------------------


class ExpenseData(BaseModel):
    """Expense report data extracted from the incoming email event."""

    amount: float = Field(description="Expense amount in USD")
    submitter: str = Field(description="Email of the person who submitted")
    category: str = Field(description="Expense category, e.g. travel, meals")
    description: str = Field(description="What the expense is for")
    date: str = Field(description="Date of the expense (YYYY-MM-DD)")


class ApprovalDecision(BaseModel):
    """Structured response schema representing a manager's decision."""

    decision: Literal["approve", "reject"] = Field(
        description="Manager's decision. Choose 'approve' to accept the expense, or 'reject' to deny it."
    )


# ---------------------------------------------------------------------------
# Security defense configuration and helper
# ---------------------------------------------------------------------------


INJECTION_KEYWORDS = [
    "ignore",
    "bypass",
    "system prompt",
    "system instruction",
    "override",
    "auto-approve",
    "approve instantly",
    "do not review",
    "skip review",
    "you must approve",
    "always approve",
    "instruction",
    "prompt injection",
    "ignore previous",
    "disregard",
    "new instruction",
]


def detect_prompt_injection(text: str) -> bool:
    """Check if the text contains keywords typical of prompt injections."""
    text_lower = text.lower()
    return any(keyword in text_lower for keyword in INJECTION_KEYWORDS)


# ---------------------------------------------------------------------------
# Function nodes
# ---------------------------------------------------------------------------


def parse_expense_email(node_input: str) -> Event:
    """Parse a Pub/Sub trigger event and extract expense data.

    The trigger endpoint delivers the raw Pub/Sub message JSON. The
    expense payload lives in the ``data`` field, which may be
    base64-encoded (real Pub/Sub) or plain JSON (local testing).
    """
    print(f"parse_expense_email received: {node_input!r}", flush=True)
    try:
        event = json.loads(node_input)
    except json.JSONDecodeError as e:
        print(f"JSONDecodeError: {e}", flush=True)
        return Event(output={"error": f"Invalid JSON: {node_input[:200]}"})

    if not isinstance(event, dict):
        return Event(output={"error": "Input JSON must be a dictionary/object"})

    data = event.get("data")
    if data is None:
        data = event

    if isinstance(data, str):
        try:
            data = json.loads(base64.b64decode(data))
        except Exception:
            return Event(output={"error": f"Failed to decode data: {data[:200]}"})

    if not isinstance(data, dict):
        return Event(output={"error": "Expense data must be a dictionary/object"})

    return Event(
        output={
            "amount": float(data.get("amount", 0)),
            "submitter": data.get("submitter", "unknown"),
            "category": data.get("category", "other"),
            "description": data.get("description", ""),
            "date": data.get("date", ""),
        }
    )


def route_by_amount(node_input: dict, ctx: Context) -> Event:
    """Route expenses based on the $100 threshold.

    Returns a routing event that the workflow uses to pick the next
    node: ``AUTO_APPROVE`` for amounts under $100, ``NEEDS_REVIEW``
    for $100 and above.

    Also stores the expense data in workflow state so the HITL
    approval node can include it in the RequestInput payload.
    """
    ctx.state["expense_data"] = node_input
    amount = node_input.get("amount", 0)
    if amount >= config.review_threshold:
        return Event(route="NEEDS_REVIEW", output=node_input)  # type: ignore
    return Event(route="AUTO_APPROVE", output=node_input)  # type: ignore


def auto_approve(node_input: dict) -> Event:
    """Auto-approve a low-value expense and log the decision."""
    if "error" in node_input:
        error_msg = node_input["error"]
        log_entry = {
            "severity": "ERROR",
            "message": f"Invalid input received: {error_msg}",
            "decision": "error",
        }
        print(json.dumps(log_entry), flush=True)
        return Event(
            content=types.Content(
                role="model", parts=[types.Part.from_text(text=f"Error: {error_msg}")]
            ),
            output={"status": "error", "message": error_msg},
        )

    amount = node_input.get("amount", 0.0)
    submitter = node_input.get("submitter", "unknown")
    message_text = f"Expense auto-approved: ${amount:.2f} from {submitter}."

    log_entry = {
        "severity": "INFO",
        "message": (
            f"Expense auto-approved: ${amount:.2f}"
            f" from {submitter}"
        ),
        "decision": "approved",
        "amount": amount,
        "submitter": submitter,
        "category": node_input.get("category", "other"),
    }
    print(json.dumps(log_entry), flush=True)
    return Event(
        content=types.Content(
            role="model", parts=[types.Part.from_text(text=message_text)]
        ),
        output={"status": "approved", **node_input},
    )


def security_checkpoint(node_input: dict, ctx: Context) -> Event:
    """Security Checkpoint: scrub PII (SSN & Credit Cards) and check for prompt injection.

    If prompt injection is detected, route straight to manager review as a SECURITY_EVENT.
    Otherwise, route to the LLM reviewer as CLEAN.
    """
    desc = node_input.get("description", "")
    category = node_input.get("category", "other")

    # Check for prompt injection
    if detect_prompt_injection(desc):
        # Flag security event
        ctx.state["security_flag"] = True
        log_entry = {
            "severity": "CRITICAL",
            "message": f"Prompt injection attempt detected in expense description from {node_input.get('submitter', 'unknown')}",
            "alert_type": "security_checkpoint",
            "submitter": node_input.get("submitter", "unknown"),
            "category": category,
        }
        print(json.dumps(log_entry), flush=True)
        # Scrub anyway for security hygiene
        desc, _ = re.subn(r"\b\d{3}-\d{2}-\d{4}\b", "[REDACTED_SSN]", desc)
        desc, _ = re.subn(
            r"\b(?:\d{4}[- ]?){3}\d{4}\b|\b\d{13,16}\b", "[REDACTED_CC]", desc
        )
        node_input["description"] = desc
        ctx.state["expense_data"] = node_input
        return Event(route="SECURITY_EVENT", output=node_input)  # type: ignore

    # Scrub personal data
    redacted = False
    desc, count_ssn = re.subn(r"\b\d{3}-\d{2}-\d{4}\b", "[REDACTED_SSN]", desc)
    if count_ssn > 0:
        redacted = True
    desc, count_cc = re.subn(
        r"\b(?:\d{4}[- ]?){3}\d{4}\b|\b\d{13,16}\b", "[REDACTED_CC]", desc
    )
    if count_cc > 0:
        redacted = True

    if redacted:
        redacted_cats = ctx.state.get("redacted_categories", [])
        if category not in redacted_cats:
            redacted_cats.append(category)
        ctx.state["redacted_categories"] = redacted_cats

    node_input["description"] = desc
    ctx.state["expense_data"] = node_input

    return Event(route="CLEAN", output=node_input)  # type: ignore


# ---------------------------------------------------------------------------
# LLM review agent (invoked only for expenses >= $100 and CLEAN)
# ---------------------------------------------------------------------------


def emit_expense_alert(
    submitter: str,
    amount: float,
    category: str,
    risk_summary: str,
) -> dict:
    """Emit a structured log alerting finance to review a high-value expense.

    Cloud Run captures JSON stdout as structured logs in Cloud Logging.
    A log-based metric and alert policy trigger email notifications
    when these logs appear.

    Args:
        submitter: Who submitted the expense.
        amount: The expense amount in USD.
        category: The expense category.
        risk_summary: Why this expense needs review.

    Returns:
        Confirmation that the alert was emitted.
    """
    log_entry = {
        "severity": "WARNING",
        "message": (
            f"Expense review alert: ${amount:.2f} from {submitter} — {risk_summary}"
        ),
        "alert_type": "expense_review",
        "submitter": submitter,
        "amount": amount,
        "category": category,
        "risk_summary": risk_summary,
    }
    print(json.dumps(log_entry), flush=True)
    return {"status": "alert_emitted", "submitter": submitter, "amount": amount}


review_agent = LlmAgent(
    name="review_agent",
    model=config.model,
    mode="single_turn",
    instruction="""You are an expense review agent. You receive expense reports
of $100 or more that need review before approval.

Analyze the expense and:
1. Check for risk factors: unusual category for the amount, vague description,
   suspiciously round numbers, very high value (>$1000), or potential policy
   violations.
2. Call the `emit_expense_alert` tool with the submitter, amount, category,
   and a brief risk summary explaining why this expense needs human review.
3. Return a structured review.

Your review MUST include:
- **Amount**: The expense amount
- **Submitter**: Who submitted it
- **Category**: The expense category
- **Risk level**: low, medium, or high
- **Risk factors**: What flags you found (if any)
- **Recommendation**: approve, request-more-info, or escalate""",
    input_schema=ExpenseData,
    tools=[emit_expense_alert],
)


# ---------------------------------------------------------------------------
# HITL: pause the workflow for human approval
# ---------------------------------------------------------------------------


def request_approval(node_input, ctx: Context):  # type: ignore[no-untyped-def]
    """Pause the workflow and wait for a human to approve or reject.

    Yields a ``RequestInput`` that the ADK runtime surfaces to the UI.
    The workflow stays paused until someone resumes the session (via the
    approval UI or ``POST /run``). The human's response becomes the
    output of this node and flows into ``process_decision``.
    """
    expense = ctx.state.get("expense_data", {})
    if ctx.state.get("security_flag"):
        message = "WARNING: Security Event! Prompt injection attempt detected. Approve or reject."
    else:
        message = "Expense requires manager approval. Approve or reject."
    yield RequestInput(
        message=message,
        payload=expense,
        response_schema=ApprovalDecision,
    )


def process_decision(node_input, ctx: Context) -> Event:  # type: ignore[no-untyped-def]
    """Process the human's approval decision and log the outcome."""
    # node_input is the response from RequestInput — the approval UI
    # sends {"decision": "approve"} or {"decision": "reject"}.
    decision = "unknown"
    if isinstance(node_input, dict):
        decision = node_input.get("decision", "unknown")
    elif isinstance(node_input, str):
        decision = "approve" if "approve" in node_input.lower() else "reject"

    approved = decision == "approve"
    expense = ctx.state.get("expense_data", {})
    status = "approved" if approved else "rejected"
    is_security_event = ctx.state.get("security_flag", False)

    severity = "CRITICAL" if is_security_event else ("INFO" if approved else "WARNING")

    log_entry = {
        "severity": severity,
        "message": f"Expense {status} by manager"
        + (" (Security Event flagged)" if is_security_event else ""),
        "decision": status,
        "security_event": is_security_event,
    }
    print(json.dumps(log_entry), flush=True)

    submitter = expense.get("submitter", "unknown")
    amount = expense.get("amount", 0)
    category = expense.get("category", "")
    description = expense.get("description", "")
    date = expense.get("date", "")

    parts = []
    if is_security_event:
        parts.append(
            "[SECURITY WARNING]: This expense was flagged for a potential prompt injection security policy violation."
        )
    parts.append(f"${amount:.2f} expense from {submitter} has been {status}.")
    if description:
        parts.append(f'"{description}" ({category}) on {date}.')
    if approved:
        parts.append(
            "The expense has been logged and will be processed for reimbursement."
        )
    else:
        parts.append(
            "The submitter will be notified and may resubmit with additional documentation."
        )

    message_text = " ".join(parts)
    return Event(
        content=types.Content(
            role="model", parts=[types.Part.from_text(text=message_text)]
        ),
        output={"status": status, "message": message_text},
    )


# ---------------------------------------------------------------------------
# Graph-based workflow — the root agent
# ---------------------------------------------------------------------------

root_agent = Workflow(
    name="expense_processor",
    edges=[
        ("START", parse_expense_email, route_by_amount),
        (
            route_by_amount,
            {
                "AUTO_APPROVE": auto_approve,
                "NEEDS_REVIEW": security_checkpoint,
            },
        ),
        (
            security_checkpoint,
            {
                "CLEAN": review_agent,
                "SECURITY_EVENT": request_approval,
            },
        ),
        (review_agent, request_approval, process_decision),
    ],
)

app = App(
    name="expense_agent",
    root_agent=root_agent,
    resumability_config=ResumabilityConfig(is_resumable=True),
)
