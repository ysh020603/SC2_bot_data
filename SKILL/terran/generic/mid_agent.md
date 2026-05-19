# Atomic Action Execution
Do not combine actions; for example, you must split "Build Barracks and train Marines" into two separate explicit tasks.

# Single Expansion Limit
Do not include more than one expansion task in a single cycle to avoid crashing the economy.

# Priority Ordered Tasks
Order tasks by absolute resource priority, because earlier tasks claim minerals, gas, workers, and production capacity first.

# Short Cycle Usefulness
Each task should be useful within the next planning cycle or near-term execution window, not merely a distant future intention.

# Bottleneck First
The first task must address the most urgent bottleneck, such as supply block, missing prerequisite, worker shortage, or defensive collapse.
t repeat a task that failed last cycle unless the blocker has changed or the missing prerequisite is now being handled.

# Exact Target Counts
Use exact target counts when possible, such as "Train SCVs to 46" or "Train Marines to 24", so the lower layer can execute declaratively.
# Prerequisite Replacement
If a requested goal cannot execute because a prerequisite is missing, replace the goal with the missing prerequisite.

# No Repeated Dead Task
Do no

# Stable Task Count
Prefer 3 to 5 high-priority tasks per cycle; too many concurrent tasks dilute resources and make execution unstable.

# One Bottleneck Isolation
For critical tech-tree bottlenecks, issue a single isolated task for that cycle when immediate execution matters.

# Completed Task Removal
Remove tasks that are already complete instead of carrying stale goals forward.

# Inappropriate Task Removal
Remove tasks that no longer fit the current observation, especially when the bot has fallen behind or lost key infrastructure.

# Production Continuity
Preserve continuous worker and army production when stable; successful games usually maintain production while teching or expanding.

# Defense Before Tech When Behind
If army power is behind, defensive production should outrank tech, expansion, or greedy infrastructure.

# Recovery Before Growth
After worker loss, base loss, or production collapse, restore core economy and production before adding new long-term growth tasks.

# No Hidden Multi-Step Tasks
Avoid tasks that secretly require several steps, such as "transition into air tech"; split them into concrete prerequisite tasks.

# Resource Bottleneck Diagnosis
When a task repeats, identify whether the blocker is minerals, gas, supply, tech lab, production structure, or worker availability.

# Safe Expansion Check
Only add expansion tasks when army power and economy are stable enough to support them.

# Attack With Production
When adding an attack or pressure task, also maintain production tasks so the bot does not stop reinforcing.

# Advantage Is Not Idleness
When ahead, include a task that converts the advantage, such as pressure, production scaling, or replacing lost key units.

# No Micro Tasks
Do not include scouting, camera movement, unit positioning, focus fire, spell casting, or tactical micro in the task list.

# Clear Natural Language
Write each task in simple natural language that the Down Agent can translate directly.

# Avoid Ambiguous Verbs
Avoid vague verbs such as "prepare", "transition", or "improve" unless the task includes the exact structure or unit to build or train.

# One Action Per Sentence
Each task sentence must contain exactly one plan or action, even if two actions seem naturally related.

# Recovery Exit Condition
When using recovery tasks, stop adding greedy goals until the observation shows stable workers, stable bases, and at least even army power.

# Successful Pattern Preservation
If a current plan is working, keep the stable production pattern instead of changing direction unnecessarily.

# Failure Pattern Interruption
If the same failure pattern appears across cycles, interrupt it with a corrective task rather than continuing the original plan.