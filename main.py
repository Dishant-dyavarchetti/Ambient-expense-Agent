import logging
import os
from fastapi import FastAPI, Request, HTTPException
import uvicorn

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.cli.fast_api import get_fast_api_app
from app.agent import app as adk_app

# 1. Logging: Use standard Python logging for console logs
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 2. Telemetry: Set otel_to_cloud=False
# We can use the built-in get_fast_api_app and set otel_to_cloud=False
# However, the user specifically requested to normalize the fully-qualified 
# subscription path down to a short name to keep session records readable.
# The easiest way to achieve both is to define a custom endpoint that parses the 
# Pub/Sub envelope and triggers the agent using Runner.

app = FastAPI()
session_service = InMemorySessionService()

@app.post("/")
async def handle_pubsub(request: Request):
    """
    Accepts Pub/Sub trigger messages and feeds them into the workflow.
    """
    try:
        envelope = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    if not envelope or "message" not in envelope:
        raise HTTPException(status_code=400, detail="Missing message payload")

    # Gotcha: Normalize fully-qualified subscription path down to a short name
    # e.g., projects/my-project/subscriptions/my-sub -> my-sub
    subscription = envelope.get("subscription", "default_sub")
    short_sub_name = subscription.split("/")[-1]
    
    message_id = envelope["message"].get("messageId", "unknown")
    
    # Use short subscription name for readable session records
    session_id = f"{short_sub_name}-{message_id}"
    user_id = "pubsub_system"
    
    logger.info(f"Processing Pub/Sub message. Session ID: {session_id}")
    
    try:
        await session_service.create_session(
            app_name=adk_app.name,
            user_id=user_id,
            session_id=session_id
        )
    except Exception as e:
        # Session might already exist if this is a retry from Pub/Sub
        logger.warning(f"Session creation issue (might already exist): {e}")

    runner = Runner(
        agent=adk_app.root_agent,
        app_name=adk_app.name,
        session_service=session_service
    )

    from google.genai import types
    import json
    
    # Feed the envelope into the workflow.
    # The workflow's parse_event node (START node) expects the text to be a JSON string.
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=types.Content(
            role="user",
            parts=[types.Part.from_text(text=json.dumps(envelope))]
        )
    ):
        if event.is_final_response():
            logger.info(f"Workflow completed. Final response: {event.output}")

    return {"status": "success", "session_id": session_id}

@app.post("/resume/{session_id}")
async def resume_workflow(session_id: str, request: Request):
    """
    Accepts human approval or rejection to resume the paused workflow.
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    decision = payload.get("decision")
    if not decision:
        raise HTTPException(status_code=400, detail="Missing 'decision' in payload")

    user_id = "pubsub_system"
    logger.info(f"Resuming session: {session_id} with decision: {decision}")

    runner = Runner(
        agent=adk_app.root_agent,
        app_name=adk_app.name,
        session_service=session_service
    )

    from google.genai import types

    # We create a function response targeting the interrupt_id="approval_decision"
    # and provide the human's reply.
    resume_message = types.Content(
        role="user",
        parts=[
            types.Part(
                function_response=types.FunctionResponse(
                    id="approval_decision",
                    name="request_input",
                    response={"reply": decision}
                )
            )
        ]
    )

    async for event in runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=resume_message
    ):
        if event.is_final_response():
            logger.info(f"Workflow completed. Final response: {event.output}")

    return {"status": "resumed", "session_id": session_id, "decision": decision}

if __name__ == "__main__":
    # Serve on port 8080 as requested
    uvicorn.run(app, host="0.0.0.0", port=8080)
