# Security Model & Policy

AutoRemediate is designed with defense-in-depth security principles. Because incident diagnosis requires ingesting untrusted telemetry (production logs, alerts, external error traces, and repository code), the system treats external inputs as potentially hostile and enforces layered execution constraints.

## Security Controls

| Control | Implementation | Enforced By |
|---|---|---|
| **No Tool-Calling Authority** | The model outputs structured JSON only (`Diagnosis` + `FileEdit[]`), never direct tool calls. The state graph controls execution. | `agent/graph.py` |
| **Protected Paths** | Test suites (`test_*.py`, `conftest.py`, `pytest.ini`), configuration files, and `.git/` cannot be modified or deleted. | `mcp_servers/repo_server.py` |
| **Static Code Policy** | Prohibits edits introducing shell execution (`os.system`, `subprocess`), network calls (`socket`, `requests`, `urllib`), eval/exec, URLs, or dynamic module loads. Only newly introduced AST nodes are evaluated. | `agent/policy.py` |
| **Syntax Validation** | Proposed edits must parse cleanly via Python AST before application to the workspace. | `mcp_servers/repo_server.py` (`ast.parse`) |
| **Isolated Sandbox Execution** | Tests run in a hardened Docker container: read-only rootfs, non-root user (`nobody`), no network access (`--network none`), resource caps (512MB RAM, 1 CPU, 128 pids). | `infra/sandbox/Dockerfile` & `mcp_servers/repo_server.py` |
| **Cryptographic Diff Binding** | Human approvals are bound to the SHA-256 fingerprint of the unified diff. If files change post-approval, publishing is aborted. | `mcp_servers/repo_server.py` (`diff_fingerprint`, `publish_branch`) |
| **Restricted Git Publishing** | Publishing branches are strictly constrained to names matching the regex `autoremediate/<name>`. | `mcp_servers/repo_server.py` |
| **Resource & Execution Budgets** | Hard limits are placed on LLM calls, total prompt tokens, and wall-clock execution time per incident to mitigate denial-of-wallet / looping. | `agent/graph.py` |
| **Failure Classification** | Provider rate limits (`429`), infrastructure errors, Docker failures, and budget exhausts are tagged `infra_error`/`budget_exceeded` and never mislabeled as bad code edits. | `agent/graph.py` |

## Threat Model & Known Limitations

1. **Denylist vs. Allowlist in Edit Policy**:
   - The edit scanner evaluates new AST nodes against known dangerous patterns. Highly obfuscated Python code (e.g. nested `getattr(__import__(...))` tricks) could theoretically bypass static checks. Therefore, human review via LangGraph interrupt remains the final mandatory gate before production branch publication.
2. **Sandbox Scope**:
   - The Docker sandbox isolates test execution from the host operating system. It verifies whether tests pass, but does not guarantee semantic safety of business logic.
3. **Indirect Prompt Injections**:
   - Log files from production may contain adversary-controlled strings designed to alter agent behavior. Log data is strictly flagged as `UNTRUSTED_LOG_DATA`. As demonstrated in benchmark incident `inc_005`, policy enforcement blocks injection payloads from altering tests or executing shell commands.

## Reporting a Vulnerability

If you discover a security vulnerability within AutoRemediate, please open a private security advisory on GitHub or contact the maintainers directly.
