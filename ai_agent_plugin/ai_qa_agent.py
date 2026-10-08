import os
import json
import re
import sqlite3
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse
from openai import OpenAI
from playwright.sync_api import sync_playwright

PLUGIN_DIR=Path(__file__).resolve().parent
PROJECT_DIR=PLUGIN_DIR.parent
CONFIG_PATH=PLUGIN_DIR / "config.json"
CONFIG=json.loads(CONFIG_PATH.read_text(encoding="utf-8")) if CONFIG_PATH.exists() else {}
REPORT_DIR=PROJECT_DIR / "ai_generated_report"
EVIDENCE_DIR=REPORT_DIR / "evidence"
PRD_PATH=PROJECT_DIR / CONFIG.get("prd_path", "docs/PRD_and_Requirements.md")
FRONTEND_DIR=PROJECT_DIR / CONFIG.get("frontend_dir", "auth-frontend")
BACKEND_DIR=PROJECT_DIR / CONFIG.get("backend_dir", "auth-backend")
DB_PATH=PROJECT_DIR / CONFIG.get("database_path", "auth-backend/demo.db")
FRONTEND_URL=CONFIG.get("frontend_url", "http://127.0.0.1:3000").rstrip("/")
BACKEND_URL=CONFIG.get("backend_url", "http://localhost:5000").rstrip("/")
FRONTEND_COMMAND=CONFIG.get("frontend_start_command", ["node", "node_modules/react-scripts/bin/react-scripts.js", "start"])
BACKEND_COMMAND=[sys.executable if part == "{python}" else part for part in CONFIG.get("backend_start_command", [sys.executable, "-m", "flask", "--app", "app", "run", "--port", "5000", "--no-debugger", "--no-reload"])]
RUN_ID=str(int(time.time()))
client=None

def call_llm(prompt, temperature):
    global client
    if client is None:
        api_key=os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError("Set DEEPSEEK_API_KEY before running the Agent.")
        client=OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
    response=client.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
    )
    return response.choices[0].message.content.strip()

def strip_code_fence(text):
    text=text.strip()
    text=re.sub(r"^```(?:json|markdown)?\s*", "", text, flags=re.IGNORECASE)
    text=re.sub(r"\s*```$", "", text)
    return text.strip()

def replace_run_id(value):
    if isinstance(value, str):
        value=re.sub(
            r"\{\{RUN_ID_(\d+)\}\}",
            lambda match: RUN_ID[-int(match.group(1)):].zfill(int(match.group(1))),
            value,
        )
        return value.replace("{{RUN_ID}}", RUN_ID)
    if isinstance(value, list):
        return [replace_run_id(item) for item in value]
    if isinstance(value, dict):
        return {key: replace_run_id(item) for key, item in value.items()}
    return value

def port_is_open(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(0.5)
        return connection.connect_ex(("127.0.0.1", port)) == 0

def wait_for_port(port, timeout):
    deadline=time.time()+timeout
    while time.time()<deadline:
        if port_is_open(port):
            return
        time.sleep(0.5)
    raise RuntimeError(f"Timed out waiting for port {port}.")

def start_service(command, working_directory, port, timeout, environment=None):
    if port_is_open(port):
        return None

    process = subprocess.Popen(
        command,
        cwd=working_directory,
        env={**os.environ, **(environment or {})},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    try:
        wait_for_port(port, timeout)
    except Exception:
        process.terminate()
        raise
    return process

def stop_service(process):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()

def cleanup_test_accounts(usernames, baseline_user_id):
    with sqlite3.connect(DB_PATH) as connection:
        connection.executemany(
            "DELETE FROM users WHERE username = ? AND id > ?",
            [(username, baseline_user_id) for username in usernames],
        )
        removed_count=connection.total_changes
    print(f"Cleanup: removed {removed_count} of {len(usernames)} registered test account(s).")
    if removed_count != len(usernames):
        print("Warning: some registered test accounts were not removed.")

def request_matches(response, endpoint):
    return (
        response.request.method == "POST"
        and urlparse(response.url).path == endpoint
    )

def execute_submit(page, action, form_values):
    endpoint=action.get("endpoint") or ("/api/register" if action["button"] == "Register" else "/api/login")
    expected_request=action.get("expected_request", True)
    expected_path=action.get("expected_path")
    expected_messages=action.get("expected_messages", [])
    observed_requests=[]

    def record_request(request):
        if request.method == "POST" and urlparse(request.url).path == endpoint:
            observed_requests.append(request)

    page.on("request", record_request)
    button=page.get_by_role("button", name=action["button"], exact=True)

    evidence={
        "action": "submit",
        "button": action["button"],
        "expected_endpoint": endpoint,
        "entered_values": dict(form_values),
    }

    try:
        if expected_request:
            with page.expect_response(
                lambda response: request_matches(response, endpoint),
                timeout=10000,
            ) as response_info:
                button.click()
            response=response_info.value
            request = response.request
            try:
                request_body = request.post_data_json
            except Exception:
                request_body = request.post_data
            try:
                response_body = response.json()
            except Exception:
                response_body = response.text()
            content_type = request.headers.get("content-type", "")

            evidence.update({
                "actual_url": request.url,
                "actual_method": request.method,
                "actual_content_type": content_type,
                "actual_body": request_body,
                "actual_status": response.status,
                "actual_response_body": response_body,
            })

            if request.url != f"{BACKEND_URL}{endpoint}":
                raise AssertionError(
                    f"Expected request URL {BACKEND_URL}{endpoint}, received {request.url}."
                )
            if request_body != form_values:
                raise AssertionError(
                    f"Expected request body {form_values}, received {request_body}."
                )
            if "application/json" not in content_type.lower():
                raise AssertionError(
                    f"Expected an application/json request, received {content_type or 'no content type'}."
                )
            if response.status != action["expected_status"]:
                raise AssertionError(
                    f"Expected HTTP {action['expected_status']}, received HTTP {response.status}."
                )
            if (
                endpoint == "/api/login"
                and action["expected_status"] == 200
                and (
                    not isinstance(response_body, dict)
                    or response_body.get("user_id") is None
                )
            ):
                raise AssertionError(
                    f"Expected successful login response to contain user_id, received {response_body}."
                )
        else:
            button.click()
            page.wait_for_timeout(500)
            evidence["request_count"]=len(observed_requests)
            if observed_requests:
                raise AssertionError(
                    f"Expected frontend validation to block {endpoint}, but a request was sent."
                )

        for message in expected_messages:
            page.get_by_text(message, exact=True).wait_for(state="visible", timeout=5000)
        evidence["visible_messages"] = expected_messages

        if expected_path is not None:
            actual_path=urlparse(page.url).path
            evidence["actual_path"]=actual_path
            if actual_path != expected_path:
                raise AssertionError(
                    f"Expected browser path {expected_path}, received {actual_path}."
                )
        return evidence
    finally:
        page.remove_listener("request", record_request)

def execute_test_case(browser, test_case, created_usernames):
    context=browser.new_context()
    page=context.new_page()
    page.set_default_timeout(5000)
    form_values={}
    evidence=[]

    def record_registration(response):
        if response.url == f"{BACKEND_URL}/api/register" and response.status == 201:
            try:
                username=response.request.post_data_json.get("username")
                if isinstance(username, str):
                    created_usernames.add(username.strip())
            except (AttributeError, ValueError):
                pass

    page.on("response", record_registration)

    try:
        for action in test_case["actions"]:
            action_type = action["action"]
            if action_type == "navigate":
                page.goto(f"{FRONTEND_URL}{action['path']}", wait_until="networkidle")
                actual_path = urlparse(page.url).path
                evidence.append({
                    "action": "navigate",
                    "expected_path": action["path"],
                    "actual_path": actual_path,
                })
                if actual_path != action["path"]:
                    raise AssertionError(
                        f"Expected browser path {action['path']}, received {actual_path}."
                    )
                if action["path"] == "/":
                    page.get_by_role("button", name="Login", exact=True).wait_for(state="visible")
                elif action["path"] == "/register":
                    page.get_by_role("button", name="Register", exact=True).wait_for(state="visible")
                form_values = {}
            elif action_type == "fill":
                field=action["field"]
                value=action["value"]
                page.get_by_label(field.capitalize(), exact=True).fill(value)
                form_values[field]=value
                evidence.append({"action": "fill", "field": field, "value": value})
            elif action_type == "clear":
                field=action["field"]
                page.get_by_label(field.capitalize(), exact=True).clear()
                form_values[field]=""
                evidence.append({"action": "clear", "field": field})
            elif action_type == "submit":
                evidence.append(execute_submit(page, action, form_values))

        return {
            "id": test_case["id"],
            "type": test_case["type"],
            "scenario": test_case["scenario"],
            "status": "Pass",
            "evidence": evidence,
        }
    except Exception as error:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        screenshot_path=EVIDENCE_DIR / f"{re.sub(r'[^A-Za-z0-9_-]', '_', test_case['id'])}.png"
        try:
            page.screenshot(path=str(screenshot_path), full_page=True)
        except Exception:
            screenshot_path=None
        return {
            "id": test_case["id"],
            "type": test_case["type"],
            "scenario": test_case["scenario"],
            "status": "Fail",
            "error": str(error),
            "evidence": evidence,
            "screenshot": str(screenshot_path) if screenshot_path else None,
        }
    finally:
        context.close()

def generate_bug_report(prd_content, test_case, result):
    prompt = f"""
You are a QA engineer writing an evidence-based bug report.

Product requirements:
{prd_content}

Generated test case:
{json.dumps(test_case, ensure_ascii=False)}

Observed browser evidence:
{json.dumps(result, ensure_ascii=False)}

Write a concise Markdown bug report in English. Include the title, requirement,
preconditions, reproduction steps, expected result, actual result, captured
request and response evidence, user impact, and evidence file when available.
State only facts supported by the requirement and execution evidence. Do not
invent a root cause, severity policy, security issue, or unsupported product rule.
Use natural professional language, no emojis, no decorative symbols, no canned
AI phrases, and no filler. Output only Markdown.
"""
    return strip_code_fence(call_llm(prompt, 0.2))


def main():
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    print("Reading PRD and analyzing the full user journey.")
    prd_content = PRD_PATH.read_text(encoding="utf-8")

    generation_prompt = f"""
You are a senior QA automation engineer. Read this product requirements document:

{prd_content}

Create a comprehensive Markdown test case document for the complete React and
Flask authentication flow, not just isolated API endpoints. Cover registration,
login, frontend required-field validation, browser navigation, frontend-to-backend
field mapping, endpoint selection, HTTP status handling, and user-visible messages.
Include functional, boundary, and exception cases that are directly supported by
the PRD. Do not invent security, performance, character-set, maximum-length, or
case-sensitivity requirements that are absent from the PRD.
The actual backend endpoints are "/api/register" and "/api/login". Use these
exact paths in every expected request; never use "/register" or "/login" as an API endpoint.
Successful registration must expect HTTP 201. Successful login must expect HTTP 200.

Every test must be executable through the visible React interface. When a test
creates an account, use a username containing the literal token {{{{RUN_ID}}}} so
repeated runs remain independent. Put dependent operations, such as registration
followed by login or duplicate registration, in the same test case.
Use a different username prefix in every test case; reuse a username only within
the same test for login or duplicate registration. For duplicate registration,
expect HTTP 201 on the first registration and HTTP 409 on the second.
For any boundary test requiring an exact username length, use only the token format
{{{{RUN_ID_<length>}}}}, replacing <length> with the integer derived from the PRD.
Do not add a prefix or suffix to an exact-length token. Assert exact message text
only when that exact text is explicitly stated in the PRD.

Use a table with columns: ID, Type, Scenario, Browser Steps, Test Data, Expected
Frontend Behavior, Expected Request, Expected Response. Write in concise natural
professional English. Do not use emojis, decorative symbols, canned AI phrases,
or filler. Output only Markdown.
"""
    ai_generated_tcs = strip_code_fence(call_llm(generation_prompt, 0.2))
    (REPORT_DIR / "Test_Cases_Auto.md").write_text(ai_generated_tcs, encoding="utf-8")
    print("Generated Test_Cases_Auto.md.")

    parse_prompt = f"""
Convert the following browser test cases into a JSON array for an automation
runner. Output only valid JSON, without Markdown fences or commentary.

{ai_generated_tcs}

Each test object must have this exact structure:
{{
  "id": "string",
  "type": "string",
  "scenario": "string",
  "actions": [
    {{"action": "navigate", "path": "/register"}},
    {{"action": "fill", "field": "username", "value": "name_{{{{RUN_ID}}}}"}},
    {{"action": "fill", "field": "password", "value": "password"}},
    {{
      "action": "submit",
      "button": "Register",
      "endpoint": "/api/register",
      "expected_request": true,
      "expected_status": 201,
      "expected_messages": ["Registration successful"],
      "expected_path": "/"
    }}
  ]
}}

Allowed actions are navigate, fill, clear, and submit. Allowed fields are username
and password. Allowed buttons are Register and Login. A submit action that should
be stopped by frontend validation must set expected_request to false, omit
expected_status, and list the expected validation messages. All other submit
actions must set expected_request to true and include the exact endpoint and
integer expected_status. Preserve the literal {{{{RUN_ID}}}} token. Do not add
requirements or test cases that are not present in the Markdown document.
Use a different username prefix in every test case. Reuse a username only within
the same test for login or duplicate registration. In a duplicate-registration
test, the first submit must expect HTTP 201 and the second must expect HTTP 409.
Preserve any {{{{RUN_ID_<length>}}}} token as the complete username value, replacing
<length> with the exact integer required by the AI-generated boundary case. Only
include exact strings in expected_messages when they are explicitly stated in the
PRD or Markdown test case; otherwise use an empty expected_messages array.
The actual React login page path is "/" and the registration page path is
"/register". Never use "/login" as a browser path.
The actual backend endpoints are "/api/register" and "/api/login". Use these
exact endpoint values; never use "/register" or "/login" as an API endpoint.
Use expected_status 201 for successful registration and 200 for successful login.
"""
    raw_json = strip_code_fence(call_llm(parse_prompt, 0.0))
    test_cases = replace_run_id(json.loads(raw_json))

    print("Starting the application and executing browser tests.")
    backend_process = None
    frontend_process = None
    execution_results = []
    created_usernames = set()
    baseline_user_id = None
    try:
        backend_process = start_service(
            BACKEND_COMMAND,
            BACKEND_DIR,
            urlparse(BACKEND_URL).port,
            30,
        )
        with sqlite3.connect(DB_PATH) as connection:
            baseline_user_id = connection.execute("SELECT COALESCE(MAX(id), 0) FROM users").fetchone()[0]
        frontend_process = start_service(
            FRONTEND_COMMAND,
            FRONTEND_DIR,
            urlparse(FRONTEND_URL).port,
            120,
            {"BROWSER": "none"},
        )

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="msedge", headless=True)
            try:
                for index, test_case in enumerate(test_cases, 1):
                    print(f"Running test {index}/{len(test_cases)}: {test_case['id']}", flush=True)
                    result = execute_test_case(browser, test_case, created_usernames)
                    execution_results.append(result)
                    print(f"{test_case['id']}: {result['status']}")
                    if result["status"] == "Fail":
                        print(f"  Reason: {result['error']}")
                        bug_report = generate_bug_report(prd_content, test_case, result)
                        safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", test_case["id"])
                        (REPORT_DIR / f"BUG-AI-{safe_id}_Auto.md").write_text(
                            bug_report,
                            encoding="utf-8",
                        )
            finally:
                browser.close()
    finally:
        stop_service(frontend_process)
        stop_service(backend_process)
        if baseline_user_id is not None:
            cleanup_test_accounts(created_usernames, baseline_user_id)

    print("Generating the global test report.")
    total_count = len(execution_results)
    passed_count = sum(1 for result in execution_results if result["status"] == "Pass")
    failed_count = sum(1 for result in execution_results if result["status"] == "Fail")
    test_ids = [result["id"] for result in execution_results]
    print(f"Execution summary: {total_count} total, {passed_count} passed, {failed_count} failed.")
    report_prompt = f"""
You are the QA lead. Write a concise Markdown test report in English from the
execution evidence below.

Product requirements:
{prd_content}

Execution results:
{json.dumps(execution_results, ensure_ascii=False)}

Authoritative totals: {total_count} total, {passed_count} passed, {failed_count} failed.
Test case IDs: {json.dumps(test_ids)}
Use these totals exactly and include one execution-matrix row for every listed ID.
Do not omit, merge, or invent test cases.

Include an executive summary, an execution matrix, a factual bug summary, coverage
of the React-to-Flask integration, and a QA conclusion. Clearly distinguish product
failures from automation or environment failures. Use only the supplied evidence.
Do not invent causes, risks, requirements, or test results. Use natural professional
language, no emojis, no decorative symbols, no canned AI phrases, and no filler.
Output only Markdown.
"""
    final_report = strip_code_fence(call_llm(report_prompt, 0.2))
    (REPORT_DIR / "Test_Report_Auto.md").write_text(final_report, encoding="utf-8")
    print("Generated Test_Report_Auto.md.")
    print("Test execution completed.")


if __name__ == "__main__":
    main()
