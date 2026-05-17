# Broadcast Hand-Raise Test Harness

This folder contains an isolated prototype to test:

1. Broadcast shared context to all agents.
2. Let each agent self-declare hand-raise intent.
3. Parse intent with JSON-first extraction and text fallback.
4. Print raw model output + extracted fields for fast debugging.
5. Optionally run contribution phase for top queued agent.

## Script

- `broadcast_handraise_test.py`

## Run

From project root:

```powershell
python broadcast_tests\\broadcast_handraise_test.py --question "Design a campus waste-sorting system"
```

With contribution phase:

```powershell
python broadcast_tests\\broadcast_handraise_test.py --question "Design a campus waste-sorting system" --run-contribution
```

Multi-cycle (3 rounds):

```powershell
python broadcast_tests\\broadcast_handraise_test.py --question "Design a campus waste-sorting system" --run-contribution --cycles 3
```

Compact logs (queue/selection-focused):

```powershell
python broadcast_tests\\broadcast_handraise_test.py --question "Design a campus waste-sorting system" --run-contribution --show-selected-only
```

Save full debug artifact JSON:

```powershell
python broadcast_tests\\broadcast_handraise_test.py --question "Design a campus waste-sorting system" --run-contribution --cycles 3 --save-json broadcast_tests\\artifacts\broadcast_debug.json
```

Custom model set:

```powershell
python broadcast_tests\\broadcast_handraise_test.py --question "Design a campus waste-sorting system" --models llama-3.3-70b-versatile openai/gpt-oss-120b openai/gpt-oss-20b
```

## Documented Matrix (Artifacts)

The following runs are the baseline documentation matrix and should be stored in `broadcast_tests/artifacts/`:

1. Default models, no contribution:
   - `broadcast_tests/artifacts/default_no_contribution.json`
2. Default models, contribution:
   - `broadcast_tests/artifacts/default_with_contribution.json`
3. Default models, contribution + compact logs:
   - `broadcast_tests/artifacts/default_with_contribution_compact.json`
4. All available models, no contribution:
   - `broadcast_tests/artifacts/all_models_no_contribution.json` (`--cycles 3`)
5. All available models, contribution:
   - `broadcast_tests/artifacts/all_models_with_contribution.json` (`--cycles 2`)
6. All available models, contribution + compact logs:
   - `broadcast_tests/artifacts/all_models_with_contribution_compact.json` (`--cycles 2`)

## Notes

- Requires `OPEN_ROUTER_API_KEY` for broadcast intent calls and `GROQ_API_KEY` when `--run-contribution` is used.
- The harness is independent from `DPRSession`; no changes are made to main protocol logic.
- Queue ordering is driven by self-reported priority, not facilitator score weights.
- Intent calls use OpenRouter equivalents and contribution calls use the live Groq model IDs.
- Tie-break ordering for raised hands:
  - Priority rank (high first)
  - Confidence (higher first)
  - Pointer novelty against recent turns (higher first)
  - JSON parse success and stable agent-name fallback
- Some provider responses can be rate-limited (`429`) on contribution-heavy runs; the harness records those as `"[contribution_error] ..."` in artifact JSON instead of aborting.
