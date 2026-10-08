# AI QA Agent Plugin - Architecture Document

## 1. Purpose

The AI QA Agent is a fully automated test pipeline driven by the product requirements document. It reads the documented requirements, designs browser-based test cases, converts them into executable instructions, operates the real React interface, verifies the React-to-Flask integration, and produces evidence-based bug and test reports.

The frontend and backend application code remain unchanged. All test generation, execution, evidence collection, and reporting are coordinated by `ai_qa_agent.py`.

## 2. End-to-End Workflow

### Step 1: Requirement-Based Test Design

The Agent reads `docs/PRD_and_Requirements.md` and sends it to the language model. The model generates `Test_Cases_Auto.md` for the complete user journey rather than for isolated API endpoints only.

The generated coverage includes:

- Registration and login workflows
- Frontend required-field validation
- Navigation between React routes
- Mapping of visible form values into the backend request body
- API endpoint and HTTP status handling
- Backend messages displayed through the frontend
- Functional, boundary, and exception cases directly supported by the PRD

The prompt explicitly excludes unsupported assumptions such as undocumented security rules, maximum lengths, character restrictions, case-sensitivity behavior, and performance requirements.

### Step 2: AI-Generated Browser Plan

The Agent asks the model to convert the Markdown cases directly into a JSON array of browser actions. The requested actions are:

- `navigate`
- `fill`
- `clear`
- `submit`

The prompt tells the model to use the username and password fields, the Register and Login buttons, and the documented authentication endpoints. As in the original Agent, the returned JSON is parsed and executed directly without a separate validation layer.

Test data may contain `{{RUN_ID}}`. The Agent replaces this token with a value unique to the current run so that AI-generated accounts do not conflict with accounts created during earlier runs.

### Step 3: Real Browser Execution

The Agent starts the Flask backend and React development server when they are not already running. It then launches Microsoft Edge through Playwright and performs the AI-generated actions against the visible React application.

For every submission, the Agent captures and verifies:

- The browser route
- The values entered in the username and password fields
- Whether frontend validation correctly blocks an incomplete request
- The actual backend request URL and HTTP method
- The request Content-Type
- The actual JSON request body
- The backend response status and body
- The message displayed by the React interface
- The route reached after submission

The request body must exactly match the values entered through the React form. This allows the Agent to detect incorrect field mapping, missing values, extra values, incorrect endpoints, and response-handling problems that direct backend requests cannot detect.

Each test runs in a separate browser context. A failed test receives a full-page screenshot in `ai_generated_report/evidence`.

### Step 4: Evidence-Based Bug Reporting

When a browser test fails, the Agent sends the PRD, generated test case, observed request and response data, browser state, assertion error, and screenshot path to the language model.

The model generates an English Markdown bug report named `BUG-AI-{test_id}_Auto.md`. The reporting prompt requires the model to use only supplied evidence and prohibits invented root causes, security claims, severity policies, and undocumented requirements.

### Step 5: Final Test Report

After execution, the Agent sends the complete result set to the language model. The model generates `Test_Report_Auto.md` with:

- An executive summary
- A test execution matrix
- A factual bug summary
- React-to-Flask integration coverage
- A QA conclusion

The model is instructed to distinguish product failures from automation or environment failures and to avoid unsupported conclusions.

## 3. Runtime Components

| Component | Responsibility |
| --- | --- |
| `ai_qa_agent.py` | Coordinates test generation, service startup, browser execution, evidence capture, bug reporting, and final reporting |
| `config.json` | Supplies project paths, service URLs, and startup commands; the original values remain the defaults |
| DeepSeek API | Generates test cases, converts them into constrained browser plans, and writes evidence-grounded reports |
| Playwright | Operates Microsoft Edge and observes the real React-to-Flask interaction |
| React application | Provides the user interface exercised by the Agent |
| Flask application | Handles authentication requests generated through the React interface |
| SQLite | Stores authentication test data created by the application |
| Markdown and JSON | Store human-readable reports and machine-executable plans |

## 4. Output Files

The Agent writes generated artifacts to `ai_generated_report`:

- `Test_Cases_Auto.md`
- `BUG-AI-{test_id}_Auto.md`
- `Test_Report_Auto.md`
- `evidence/{test_id}.png` for failed browser tests

All generated text is requested in concise professional English without emojis, decorative symbols, canned AI phrases, or filler.

## 5. Reliability Controls

The language model designs the tests and returns the browser action array requested by the prompt. Pass and fail results come from observed browser and network evidence rather than from the model's opinion.

The Agent also applies the following controls:

- Requirements are limited to statements supported by the PRD.
- The JSON prompt defines the browser action format expected by the executor.
- Request bodies are compared directly with visible form input.
- Failures retain structured evidence and screenshots.
- Bug reports and the final report receive the raw execution evidence.
- Services started by the Agent are stopped after the run.
- Accounts successfully registered during the run are removed from the configured SQLite database during cleanup.
- The console shows test progress, failure reasons, the execution totals, and the cleanup count.

## 6. Dependencies and Execution

Install the Agent dependencies from `ai_agent_plugin`:

```text
python -m pip install -r requirements.txt
```

Run the complete pipeline from the same directory:

```text
python ai_qa_agent.py
```

The current Windows environment uses the installed Microsoft Edge browser, so a separate Playwright browser download is not required.

Project paths, service URLs, and startup commands can be changed in `config.json`. Paths are relative to the project directory. The `{python}` command token uses the Python interpreter running the Agent.

Set `DEEPSEEK_API_KEY` in the environment before running the Agent. In Command Prompt, use `set DEEPSEEK_API_KEY=your_key_here`; in PowerShell, use `$env:DEEPSEEK_API_KEY = "your_key_here"`. The DeepSeek account must also have sufficient balance.
