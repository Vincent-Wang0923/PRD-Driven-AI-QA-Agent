# PRD-Driven AI QA Agent

An independent QA automation prototype for a React and Flask authentication application. The agent reads a product requirements document (PRD), generates browser test cases with an LLM, runs them through the real React interface, and produces evidence-based reports. The included authentication application is a test target for demonstrating the agent.

## What it tests

- Registration and login through the browser, including required-field and boundary cases.
- Whether values entered in React reach the expected Flask endpoint with the correct JSON fields.
- Request method, content type, response status, login `user_id`, visible messages, and browser route.
- Multi-step flows such as registration followed by login or duplicate registration.

The LLM designs the cases from the PRD and converts them into browser actions. Playwright executes those actions in Microsoft Edge and checks the observed behavior. Failed cases receive screenshots and bug reports. Accounts successfully registered during a run are removed from the local SQLite database when execution ends.

## Example result

In the run on October 8, 2026, the agent executed 28 cases: 26 passed and 2 failed. Both failed cases exposed the same intentionally seeded defect: the registration page submitted a five-character username even though the PRD requires the frontend to block usernames shorter than six characters. The registration and login integration checks passed, including endpoint selection, field mapping, and expected HTTP responses.

See the [generated test report](ai_generated_report/Test_Report_Auto.md) and a [failure screenshot](ai_generated_report/evidence/TC-REG-003.png). Results may vary because the test cases are generated for each run.

## Project layout

| Path | Purpose |
| --- | --- |
| [`ai_agent_plugin/ai_qa_agent.py`](ai_agent_plugin/ai_qa_agent.py) | Test generation, browser execution, evidence collection, reporting, and test-account cleanup |
| [`ai_agent_plugin/config.json`](ai_agent_plugin/config.json) | PRD path, application paths and URLs, database path, and startup commands |
| [`docs/PRD_and_Requirements.md`](docs/PRD_and_Requirements.md) | Requirements used to generate tests |
| `auth-frontend/` | React application under test |
| `auth-backend/` | Flask and SQLite application under test |
| `ai_generated_report/` | Generated cases, reports, and failure screenshots |

The [architecture document](ai_agent_plugin/ai_architecture_document.md) describes the execution flow in more detail.

## Run locally

The current setup is intended for Windows with Python, Node.js/npm, and Microsoft Edge installed. A DeepSeek API key with available balance is required. From the project root in Command Prompt:

```cmd
python -m pip install -r auth-backend\requirements.txt
python -m pip install -r ai_agent_plugin\requirements.txt
cd auth-frontend
npm ci
cd ..
set DEEPSEEK_API_KEY=your_key_here
python ai_agent_plugin\ai_qa_agent.py
```

The agent starts the frontend and backend when their configured ports are not already in use. It writes `Test_Cases_Auto.md`, `Test_Report_Auto.md`, failure-specific `BUG-AI-*_Auto.md` files, and screenshots under `ai_generated_report/`. The console shows test progress, failure reasons, execution totals, and the number of test accounts removed.

Edit [`config.json`](ai_agent_plugin/config.json) to change the project paths, service URLs, or startup commands. Paths are relative to the project root. The `{python}` command token uses the interpreter running the agent. Keep the API key in your environment; do not add it to the repository.

## Scope

This is a proof of concept for the included authentication flow. Its browser actions and prompts currently assume the application's login and registration fields, routes, and endpoints. The generated browser plan is executed directly, so test selection and coverage can vary across runs. The agent is a command-line tool; it is not a VS Code extension.
