import asyncio
import json
import os
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from expense_agent.agent import root_agent

def extract_final_response(events):
    for event in reversed(events):
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    return {
                        "role": "model",
                        "parts": [{"text": part.text}]
                    }
    return None

async def run_case(case_id, prompt_text):
    print(f"Running evaluation case: {case_id}...")
    session_service = InMemorySessionService()
    user_id = "test_user"
    app_name = "expense_agent"
    
    session = await session_service.create_session(user_id=user_id, app_name=app_name)
    runner = Runner(agent=root_agent, session_service=session_service, app_name=app_name)
    
    new_message = types.Content(
        role="user",
        parts=[types.Part.from_text(text=prompt_text)]
    )
    
    paused_fc = None
    async for event in runner.run_async(
        new_message=new_message,
        user_id=user_id,
        session_id=session.id,
    ):
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.function_call and part.function_call.name == "adk_request_input":
                    paused_fc = part.function_call
                    break
                    
    if paused_fc:
        # Determine decision based on case_id
        decision = "reject" if "injection" in case_id else "approve"
        print(f"  Workflow paused on HITL for case {case_id}. Simulating manager decision: {decision}")
        
        resume_part = types.Part(
            function_response=types.FunctionResponse(
                name="adk_request_input",
                id=paused_fc.id,
                response={"decision": decision}
            )
        )
        resume_message = types.Content(
            role="user",
            parts=[resume_part]
        )
        
        async for event in runner.run_async(
            new_message=resume_message,
            user_id=user_id,
            session_id=session.id,
        ):
            pass
            
    # Load all session events
    full_session = await session_service.get_session(user_id=user_id, app_name=app_name, session_id=session.id)
    
    # Map events to turns
    turns = []
    current_turn = None
    for event in full_session.events:
        if event.author == "user":
            if current_turn is not None:
                turns.append(current_turn)
            current_turn = {
                "turn_index": len(turns),
                "events": []
            }
        
        if not event.content:
            continue
            
        parts = []
        for part in event.content.parts:
            part_dict = {}
            if part.text is not None:
                part_dict["text"] = part.text
            if part.function_call is not None:
                part_dict["function_call"] = {
                    "name": part.function_call.name,
                    "args": part.function_call.args,
                    "id": part.function_call.id
                }
            if part.function_response is not None:
                part_dict["function_response"] = {
                    "name": part.function_response.name,
                    "id": part.function_response.id,
                    "response": part.function_response.response
                }
            parts.append(part_dict)
            
        event_dict = {
            "author": event.author,
            "content": {
                "role": event.content.role,
                "parts": parts
            }
        }
        current_turn["events"].append(event_dict)
        
    if current_turn is not None and current_turn["events"]:
        turns.append(current_turn)
        
    agent_data = {
        "agents": {
            "expense_processor": {
                "agent_id": "expense_processor",
                "instruction": "Ambient expense-approval workflow"
            }
        },
        "turns": turns
    }
    
    final_resp_content = extract_final_response(full_session.events)
    
    return {
        "eval_case_id": case_id,
        "prompt": {
            "role": "user",
            "parts": [{"text": prompt_text}]
        },
        "response": final_resp_content,
        "responses": [{"response": final_resp_content}] if final_resp_content else [],
        "agent_data": agent_data
    }

async def main():
    dataset_path = "tests/eval/datasets/basic-dataset.json"
    output_path = "artifacts/traces/generated_traces.json"
    
    with open(dataset_path, "r") as f:
        dataset = json.load(f)
        
    eval_cases = []
    for case in dataset.get("eval_cases", []):
        case_id = case["eval_case_id"]
        prompt_text = case["prompt"]["parts"][0]["text"]
        result_case = await run_case(case_id, prompt_text)
        eval_cases.append(result_case)
        
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump({"eval_cases": eval_cases}, f, indent=2)
        
    print(f"Traces successfully generated and written to {output_path}")

if __name__ == "__main__":
    asyncio.run(main())
