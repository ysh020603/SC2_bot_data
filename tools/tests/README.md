# Tools Tests

This folder keeps lightweight maintenance tests that are useful during prompt
and launcher cleanup, but are not game-running experiment tools.

## Files

- `test_executor_prompt_tags.py`
  - Verifies executor prompt tag shortening.
  - Verifies parsed short tags are mapped back to real unit tags.
  - Verifies conflict hints expose action names only.
- `test_ordering_agent_prompt.py`
  - Verifies Ordering Agent prompts include the current strategy step.
  - Verifies the prompt still tells the model not to add or remove actions.
- `test_naming_agent_prompt.py`
  - Verifies Naming Agent prompts include jargon and upgrade category hints.
  - Verifies Terran entity validation accepts exact canonical names only.

## Run

From the repository root:

```powershell
python -m pytest tools/tests -q
```

These tests avoid launching StarCraft II. They may still need the repository
root on `PYTHONPATH` depending on the active Python environment.
