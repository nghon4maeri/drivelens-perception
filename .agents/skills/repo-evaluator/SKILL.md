---
name: repo-evaluator
description: >-
  Performs comprehensive repository audits evaluating architecture alignment, code quality, test health, documentation integrity, and environment compliance.
  Use when asked to evaluate, score, audit, or review the overall quality and readiness of the repository.
---

# Repository Quality Evaluator Skill

This skill provides an end-to-end framework to audit, score, and evaluate codebases against engineering standards, architectural contracts, test robustness, and project guidelines.

## Evaluation Dimensions

A repository is evaluated across 5 key dimensions:

1. **Architecture & Contract Alignment (30%)**
   - Single source of truth verification: do components adhere to `docs/design/CONTRACTS.md` and `docs/architecture_diagram.md`?
   - Minimal architecture: no unnecessary layers, premature abstractions, or undeclared patterns.
   - Component boundaries: clear separation between core algorithms, storage, API, and CLI.

2. **Code Quality & Maintainability (25%)**
   - Linting & formatting: clean report from `python -m ruff check src tests`.
   - Modularity: functions have single responsibilities; low cyclomatic complexity.
   - Type annotations: comprehensive typing across interfaces and models.
   - Error management: graceful recovery and informative error messages.

3. **Test Suite Health & Coverage (20%)**
   - Test execution: `python -m pytest tests -q --basetemp=output/pytest-tmp`.
   - Coverage: unit tests for core logic, edge case tests, integration/pilot test separation.
   - Determinism: tests pass reliably without race conditions or shared state contamination.

4. **Documentation & Process Discipline (15%)**
   - "Docs first, code later": changes in design are recorded in design docs and decisions log before code changes.
   - Plan synchronization: `PLAN.md` reflects current work-in-progress, completed slices, and pending items.
   - Setup clarity: setup runbooks, environment variable examples (`.env.example`), and CLI documentation.

5. **Environment & Platform Compliance (10%)**
   - Windows non-ASCII path support: handling unicode directory paths (using `PIL.Image` or `cv2.imencode`).
   - Storage isolation: caches, temporary files, models, and artifacts isolated to project drive (`data/`, `output/`).
   - Secret hygiene: no credentials, API keys, or machine-specific tokens committed to VCS.

---

## Audit Execution Protocol

### Phase 1: Automated Health Checks
Run the project's diagnostic toolchain:
```powershell
# Activate storage isolation helper if on PowerShell
. .\scripts\use_project_storage.ps1

# Run linter check
python -m ruff check src tests

# Run test suite
python -m pytest tests -q --basetemp=output/pytest-tmp

# Frontend type checks (if web exists)
if (Test-Path src/web) { cd src/web; npx tsc --noEmit; cd ../.. }
```

### Phase 2: Design & Contract Cross-Inspection
1. Read `docs/README.md` to identify source-of-truth hierarchy.
2. Read `docs/design/CONTRACTS.md` and compare specified function signatures and models against actual `src/` implementations.
3. Check for any undocumented deviations or unapproved shortcuts.

### Phase 3: Project State & Decision Traceability
1. Inspect `PLAN.md` to see the current active slice and completion statuses.
2. Inspect `docs/research-pack/06_project_state/01_confirmed_decisions.md` to verify all design decisions are logged.
3. Check `docs/design/LOCAL_SINGLE_USER_LIMITS.md` for local assumptions.

### Phase 4: Generate the Audit Report
Generate a Markdown scorecard structured as follows:

```markdown
# Repository Quality Audit Report

## Overall Score: [A / B / C / D / F] (Score: X/100)

### Dimension Breakdown
| Dimension | Weight | Score | Status |
| :--- | :---: | :---: | :--- |
| 1. Architecture & Contract Alignment | 30% | X/30 | [Pass / Warning / Fail] |
| 2. Code Quality & Linting | 25% | X/25 | [Pass / Warning / Fail] |
| 3. Test Suite Health | 20% | X/20 | [Pass / Warning / Fail] |
| 4. Documentation & Roadmap | 15% | X/15 | [Pass / Warning / Fail] |
| 5. Environment & Platform Compliance | 10% | X/10 | [Pass / Warning / Fail] |

### Key Strengths
- ...

### Identified Issues & Technical Debt
- **[CRITICAL]**: ...
- **[MODERATE]**: ...
- **[MINOR]**: ...

### Actionable Recommendations
1. ...
2. ...
```
