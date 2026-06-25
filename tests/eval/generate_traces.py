import asyncio
import json
import os
import sys

# Ensure the app path is accessible
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app.agent import app as adk_app

async def generate_traces():
    dataset_path = "tests/eval/datasets/basic-dataset.json"
    traces_dir = "artifacts/traces"
    os.makedirs(traces_dir, exist_ok=True)
    traces_path = os.path.join(traces_dir, "generated_traces.json")
    
    with open(dataset_path, "r") as f:
        dataset = json.load(f)
        
    out_cases = []
    
    for case in dataset["eval_cases"]:
        case_id = case["eval_case_id"]
        print(f"Running scenario: {case_id}")
        
        session_service = InMemorySessionService()
        runner = Runner(
            agent=adk_app.root_agent,
            app_name=adk_app.name,
            session_service=session_service,
            auto_create_session=True
        )
        
        # User prompt
        prompt_content = types.Content(**case["prompt"])
        
        # We will loop until completion
        while True:
            needs_resume = False
            async for event in runner.run_async(user_id="eval", session_id=case_id, new_message=prompt_content):
                # Check for human-in-the-loop interrupt
                if hasattr(event, "interrupt_id") and event.interrupt_id == "approval_decision":
                    print(f"  [{case_id}] Intercepted RequestInput for human approval.")
                    
                    # Automate the decision based on state
                    session = await session_service.get_session(app_name=adk_app.name, user_id="eval", session_id=case_id)
                    is_injection = session.state.get("is_prompt_injection", False)
                    expense = session.state.get("expense", {})
                    
                    if is_injection:
                        decision = "reject"
                        print(f"  [{case_id}] Auto-decision: REJECT (Prompt injection detected)")
                    else:
                        decision = "approve"
                        print(f"  [{case_id}] Auto-decision: APPROVE (Clean high-value request)")
                        
                    # Prepare resume message
                    prompt_content = types.Content(
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
                    needs_resume = True
                    break
                    
            if not needs_resume:
                break
                
        # Run finished. Now serialize the trace.
        session = await session_service.get_session(app_name=adk_app.name, user_id="eval", session_id=case_id)
        session_dict = session.model_dump()
        
        # Convert ADK session events to EvalCase agent_data format
        events = []
        for e in session_dict.get("events", []):
            # Typical event format mapping
            author = e.get("origin") or e.get("role") or "root_agent"
            content = e.get("content")
            if content:
                events.append({"author": author, "content": content})
        
        events.append({
            "author": "root_agent",
            "content": {
                "role": "model",
                "parts": [{"text": "Workflow completed"}]
            }
        })
                
        agent_data = {
            "agents": {
                "root_agent": {"agent_id": "root_agent", "instruction": ""}
            },
            "turns": [
                {
                    "turn_index": 0,
                    "events": events
                }
            ]
        }
        
        out_case = {
            "eval_case_id": case_id,
            "agent_data": agent_data
        }
        # Add response for simple extraction
        # Find the last text output from an agent
        response_text = ""
        for e in reversed(events):
            if e["author"] != "user":
                parts = e.get("content", {}).get("parts", [])
                if parts and "text" in parts[0]:
                    response_text = parts[0]["text"]
                    break
                    
        out_case["response"] = {
            "role": "model",
            "parts": [{"text": response_text or "Workflow completed"}]
        }
        out_case["prompt"] = case["prompt"]
        out_cases.append(out_case)

    # Save to traces
    with open(traces_path, "w") as f:
        json.dump({"eval_cases": out_cases}, f, indent=2)
        
    print(f"\nSaved {len(out_cases)} traces to {traces_path}")

if __name__ == "__main__":
    asyncio.run(generate_traces())
