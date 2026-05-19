# Phase Discipline
Always classify the game into a clear phase (`early`, `mid`, or `late`) before giving focus, so downstream planning has a stable context.

# Focus As Directive
Write the focus as a concise commander directive for the next planning cycle, not as a detailed task list.

# Bottleneck Awareness
Name the most important current bottleneck in the focus, such as supply, workers, tech prerequisite, production capacity, or defense.

# Stability Before Ambition
If the bot is behind or unstable, prioritize stabilization in the focus before mentioning ambitious tech, expansion, or attack goals.

# Advantage Conversion
When the bot is clearly ahead, the focus should include converting the lead into progress instead of continuing passive growth indefinitely.

# Recovery Mode Trigger
If workers, bases, or army power drop sharply, switch the focus to recovery mode until the position is stable again.

# No Micro Planning
Do not ask for scouting, positioning tricks, spell usage, or detailed combat micro; keep the focus on high-level operational direction.

# No Long Wishlists
Do not fill the focus with many future goals. A short, prioritized paragraph is more useful than a broad strategic wishlist.

# Previous State Awareness
When the same goal keeps failing, the focus should point to the missing prerequisite or blocker instead of repeating the goal.

# Measurable Focus
When possible, express the focus with measurable thresholds, such as target workers, army supply, production structures, or recovery conditions.

# Risk Recognition
If enemy power or losses indicate danger, the focus must explicitly acknowledge risk rather than continuing the normal plan.

# Resume Condition
When switching into recovery or defense mode, include the condition for returning to the normal plan, such as equal army power or stable workers.

# Strategy Consistency
Respect the selected strategy, but do not let strategy identity override obvious survival, economy, or production emergencies.

# Concise JSON Mindset
Because Top Agent must output only `phase` and `focus`, keep all guidance compact enough to fit inside the `focus` string cleanly.