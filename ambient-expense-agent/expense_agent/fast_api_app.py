# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import json
import logging
import os

from fastapi import FastAPI, Request, HTTPException
from google.adk.cli.fast_api import get_fast_api_app
from google.adk.runners import Runner
from google.genai import types

from expense_agent.agent import root_agent
from expense_agent.app_utils.telemetry import setup_telemetry
from expense_agent.app_utils.typing import Feedback

# --- Standard Logging Configuration ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("expense_agent")

setup_telemetry()

allow_origins = (
    os.getenv("ALLOW_ORIGINS", "").split(",") if os.getenv("ALLOW_ORIGINS") else None
)

# Artifact bucket for ADK (created by Terraform, passed via env var)
logs_bucket_name = os.environ.get("LOGS_BUCKET_NAME")

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
session_db_path = os.path.join(AGENT_DIR, "expense_agent", ".adk", "session.db").replace("\\", "/")
session_service_uri = f"sqlite:///{session_db_path}"
artifact_service_uri = f"gs://{logs_bucket_name}" if logs_bucket_name else None

# Telemetry: Set otel_to_cloud=False as per instructions
otel_to_cloud = False

app: FastAPI = get_fast_api_app(
    agents_dir=AGENT_DIR,
    web=True,
    artifact_service_uri=artifact_service_uri,
    allow_origins=allow_origins,
    session_service_uri=session_service_uri,
    otel_to_cloud=otel_to_cloud,
)
app.title = "ambient-expense-agent"
app.description = "API for interacting with the Agent ambient-expense-agent"

from google.adk.cli.utils.service_factory import create_session_service_from_options
session_service = create_session_service_from_options(
    base_dir=AGENT_DIR,
    session_service_uri=session_service_uri,
)


@app.post("/feedback")
def collect_feedback(feedback: Feedback) -> dict[str, str]:
    """Collect and log feedback."""
    logger.info(f"Feedback received: {feedback.model_dump()}")
    return {"status": "success"}


@app.post("/")
@app.post("/pubsub")
async def handle_pubsub(request: Request):
    """Receive Pub/Sub push messages and feed them into the graph workflow."""
    try:
        body = await request.json()
    except Exception as e:
        logger.error(f"Failed to parse JSON body: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    subscription = body.get("subscription")
    if not subscription:
        logger.error("Missing 'subscription' field in Pub/Sub message")
        raise HTTPException(status_code=400, detail="Missing 'subscription' field")

    message_dict = body.get("message")
    if not message_dict:
        logger.error("Missing 'message' field in Pub/Sub message")
        raise HTTPException(status_code=400, detail="Missing 'message' field")

    # 1. Normalize the fully-qualified subscription name down to a short name
    # e.g., projects/my-project/subscriptions/my-subscription -> my-subscription
    short_sub_name = subscription.split("/")[-1]

    # 2. Serialize message dict as input to the workflow
    payload_str = json.dumps(message_dict)

    logger.info(f"Processing Pub/Sub message from subscription: {short_sub_name}")

    try:
        # Create a new session for the normalized subscription identifier
        session = await session_service.create_session(
            user_id=short_sub_name, app_name="expense_agent"
        )
        runner = Runner(
            agent=root_agent,
            session_service=session_service,
            app_name="expense_agent",
        )
        new_message = types.Content(
            role="user",
            parts=[types.Part.from_text(text=payload_str)]
        )

        paused = False
        decision = None
        message_text = ""

        # Run the workflow agent graph
        async for event in runner.run_async(
            new_message=new_message,
            user_id=short_sub_name,
            session_id=session.id,
        ):
            # Inspect event content
            content = event.content
            if content and content.parts:
                for part in content.parts:
                    fn_call = getattr(part, "function_call", None)
                    if fn_call and getattr(fn_call, "name", None) == "adk_request_input":
                        paused = True
                    if part.text:
                        message_text += part.text + " "

            # Retrieve workflow output if available
            if event.output and isinstance(event.output, dict):
                status = event.output.get("status")
                if status in ("approved", "rejected"):
                    decision = status

        if paused:
            logger.info(f"Session {session.id} paused for manager approval.")
            return {
                "status": "paused",
                "session_id": session.id,
                "message": "Expense requires manager approval."
            }
        else:
            logger.info(f"Session {session.id} completed. Decision: {decision}")
            return {
                "status": "completed",
                "decision": decision or "auto_approved",
                "session_id": session.id,
                "message": message_text.strip()
            }

    except Exception as e:
        session_id_str = session.id if "session" in locals() else "unknown"
        logger.exception(f"Error executing workflow for session {session_id_str}")
        raise HTTPException(status_code=500, detail=str(e))


# Main execution
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080)
