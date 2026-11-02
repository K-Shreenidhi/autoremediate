# AutoRemediate: security model

## What the agent cannot do

| Control | Enforced by | How it is checked |
|---|---|---|
| The model cannot call tools. It only returns JSON (a diagnosis and text edits); the graph code decides what runs. | agent/graph.py | Design: there is no tool-calling channel for an injected instruction to use. |
| Edits to tests and config files are refused. | repo_server (PROTECTED paths) | tools/check_edit_tool.py, tools/mcp_smoke_test.py |
| Edits that introduce shell commands, network access, eval/exec, URLs, env reads, file deletion or dynamic imports are rejected before anything is applied. | agent/policy.py | tools/check_phase5.py (section A and the gullible-model graph check) |
| Edits that break Python syntax are refused. | repo_server (ast.parse) | tools/check_edit_tool.py |
| Tests run in a Docker container with no network, a read-only workspace, a non-root user, 512 MB memory, 1 CPU and 128 processes. | repo_server (run_tests) | tools/check_phase5.py (section B) |
| Nothing is published without human approval, and the approval is bound to the exact diff (SHA-256). A changed diff is refused. | graph (interrupt) + publish_branch | tools/check_phase3.py, tools/check_phase5.py |
| Branches can only be created under autoremediate/. | publish_branch | tools/check_phase3.py |
| Each incident has a budget: LLM calls, wall-clock time, prompt size. | agent/graph.py | tools/check_phase5.py |
| Log, CI and repository text is marked untrusted in tool results and in the prompt. | MCP servers, agent/baseline.py | Prompt-injection incidents (inc_005, inc_101 to inc_103) |
| Provider outages, rate limits, Docker problems and budget stops are never counted as a bad patch. | agent/graph.py | tools/check_phase5.py |

## Known limitations

- The edit policy is a denylist. Obfuscated code can slip past it. The human approval step is the final control, so review every diff.
- The sandbox protects the host from the tests. It does not make a malicious patch safe to deploy.
- The Docker daemon is trusted. Anyone who can reach it can bypass the sandbox.
- Prompt-injection resistance of a real model is measured, not guaranteed: see evals/run_graph_eval.py on inc_101 to inc_103.
- The model's self-reported confidence is not calibrated and is never used for decisions.
- Setting AUTOREMEDIATE_SANDBOX=host runs tests on the host without isolation. For development only.