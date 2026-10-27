---
name: code-review
description: >-
  Reviews code for correctness, security, performance, style, maintainability, and project compliance.
  Use when asked to review pull requests, diffs, newly written modules, or perform a code quality inspection.
---

# Code Review Skill

This skill guides rigorous, actionable code reviews for Python and TypeScript/JavaScript codebases, enforcing production standards, clean architecture, and project-specific constraints.

## Review Principles

1. **Correctness & Robustness**: Verify edge cases, boundary conditions, null/None checks, exception handling, and resource cleanup (contexts, file handles, connections).
2. **Contract & Interface Conformance**: Verify that function signatures, types, inputs/outputs, and error models strictly match documented contracts.
3. **Environment & Platform Portability**:
   - On Windows: check non-ASCII path handling (avoid `cv2.imread`/`cv2.imwrite` with raw unicode paths; use `PIL.Image` or `cv2.imencode`).
   - Storage isolation: verify scratch data, temp files, and caches stay within designated workspace storage (`data/`, `output/`), never polluting root system drives (`C:`).
4. **Security & Safety**:
   - Check input sanitization, injection vectors (SQL, command, path traversal).
   - Ensure sensitive credentials, API keys, and secrets are read from environment variables (`.env`) and never committed.
5. **Maintainability & Minimal Architecture**:
   - Avoid over-engineering, unnecessary abstractions, or patterns not defined in project contracts.
   - Maintain documentation integrity: docstrings at module level, descriptive comments for non-obvious logic.

## Review Process

### Step 1: Context & Scope Analysis
- Identify which files were added, modified, or deleted.
- Check corresponding specifications or contracts (e.g. `docs/design/CONTRACTS.md`).
- Note whether documentation was updated alongside code modifications.

### Step 2: Automated Verification
Run automated linters and type checkers:
```bash
# Python linting
python -m ruff check <paths>

# Python tests for affected area
python -m pytest <test_paths> -q --basetemp=output/pytest-tmp

# Frontend type check (if applicable)
cd src/web && npx tsc --noEmit
```

### Step 3: Deep Inspection Checklist
- [ ] **Type Annotations**: Are signatures fully typed? Are types accurate and not overly permissive (`Any`)?
- [ ] **Error Handling**: Are specific exceptions caught rather than bare `except:`? Are errors logged or raised with actionable messages?
- [ ] **Async / Concurrency**: Are async functions awaited properly? Are tasks cancelled safely?
- [ ] **Data Mutations**: Are mutable defaults avoided in function definitions? Are side-effects minimized?
- [ ] **Test Coverage**: Do new code branches have corresponding test cases covering both happy path and failure modes?

### Step 4: Structured Review Output
Format review feedback with clear severity labels:
- **[BLOCKER]**: Critical bug, data loss risk, contract violation, or security vulnerability. Must be resolved before merge.
- **[WARNING]**: Code smell, missing error handling, potential performance issue, or missing test case.
- **[SUGGESTION]**: Stylistic improvement, minor refactoring, or readability enhancement.
- **[PRAISE]**: Well-designed pattern or elegant solution.
