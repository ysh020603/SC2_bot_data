# Summary

Safe macro build focusing on Marine/Tank defense before transitioning to Battlecruisers. Establish 3 bases and a strong ground army (30+ Marines, 4+ Tanks) *before* starting Battlecruiser production. Never use priority flags for expensive tech units in the early/mid-game, and scale infrastructure incrementally. End-game goal: 20 Battlecruisers, 10 Siege Tanks, 50 Marines.

# Details

Your overall strategy is a Battlecruiser-focused macro build supported by Marines and Siege Tanks. However, this strategy MUST NOT rush into early Battlecruiser production blindly. The key principle is to first establish a stable economy, secure multiple bases, and build a massive ground force for protection. Only when the economy is strong enough and the ground army is sufficient should you begin large-scale Battlecruiser production.

* Opening & Tech: Start with a standard opening: build a Supply Depot at 13 supply, followed immediately by a Barracks and your first gas Refinery. Expand to a second base early. Whenever you build a Command Center, you must upgrade it to an Orbital Command as soon as possible. Any completed base should become an Orbital Command as early as possible to improve economy through MULE usage and provide better macro support. Take your second gas Refinery, then start a Factory, followed by a Starport once the Factory finishes.

* Early Economy & Expansion Priority: In the early game, do not start building Battlecruisers too early. Your priority should be to expand, establish a stable two-base economy, and produce a sufficient number of SCVs that matches your number of bases. Keep SCV production active so each base can be properly saturated. A strong economy is the foundation for later mass Battlecruiser production.

* Gas Management & Refinery Timing: Do not take your 3rd and 4th gas Refineries too early. Only build them when your Starport is finished or you are actively producing gas-heavy units like Siege Tanks and Battlecruisers. If you float over 800 gas while having less than 200 minerals, immediately focus all production on mineral-only tasks (Marines, Command Centers, Supply Depots) until resources balance out.

* Early Defense & Ground Army Setup: Build a defensive Bunker at your natural entrance and train a significant number of Marines early on to ensure stable defense. Use Marines and Siege Tanks to protect your bases, especially while expanding to your second and third bases. The early and mid-game army should heavily rely on Marines and Tanks for defense and map stability, rather than rushing directly into expensive Battlecruisers.

* Reactive Unit Composition: Pay close attention to [Enemy Intelligence]. If you scout a heavy presence of armored units that counter Marines (such as Marauders, Siege Tanks, or Hellions), you MUST shift your production priority. Reduce Marine production slightly and heavily prioritize Siege Tanks and Starport units (Ravens/Battlecruisers) to counter their ground armor.

* Detection & Starport Utility: Once the Starport finishes, prioritize building up to 2 Ravens if stealth threats are possible, such as Dark Templars or Banshees. Ravens provide detection and useful support abilities. Add a Factory Tech Lab and continue Siege Tank production to strengthen your ground defense.

* Task & Priority Management (CRITICAL): NEVER use the `(Priority)` flag for Battlecruisers or expensive tech units during the early or mid-game. Using priority will lock your minerals and gas, starving your basic defense production (Marines/Tanks) and causing you to lose to early attacks. Only use priority flags for emergency defense or when you are floating over 1000 minerals and gas. Furthermore, if you want to cancel or stop a task, simply REMOVE it from your output JSON task list. NEVER issue a task with a target count of 0 (e.g., do not output "Train Battlecruiser to 0"), as this will crash the task validator. 

* Incremental Macro (Avoid Resource Spikes): Never queue massive amounts of infrastructure at once. For example, do not increase your Supply Depot target by more than 2 or 3 at a time. Do not upgrade multiple Orbital Commands simultaneously if it drains all your minerals. Request infrastructure in small, continuous increments so your Marine and Tank production is never interrupted.

* Infantry Production & Reactor Usage: Add a second Barracks after your basic tech is established. As your economy grows, scale up to 3 Barracks, and eventually 5 Barracks if you are floating excess minerals over 600. Use Reactor add-ons on Barracks to greatly increase Marine production speed. At least one Barracks should use a Tech Lab to research key infantry upgrades such as Combat Shield, while Reactor Barracks should continuously produce Marines.

* Air Tech Transition (Hard Thresholds): Do NOT construct a Fusion Core or start Battlecruiser production until you have established a minimum defensive force of at least 30 Marines and 4 Siege Tanks. Rushing air tech before this threshold will leave you vulnerable to mid-game pushes. 

* Battlecruiser Production Timing: Start large-scale Battlecruiser production only when your economy is strong enough (3 bases saturated) and your ground army has reached the defensive thresholds. Take additional gas Refineries as needed, especially your fourth Refinery once your first Battlecruiser is in production or already exists. Add a second Starport with a Tech Lab to double Battlecruiser production when resources allow.

* Balanced Late-Game Production: As you scale up your Battlecruiser fleet in the late game, you must simultaneously continue producing Marines and Siege Tanks. Marines provide flexible support and anti-air presence, while Siege Tanks protect bases and control key ground zones. Do not abandon ground production just because Battlecruisers become available.

* Expansion & Tactics: Expand to a third base once your two-base economy and mid-game production are stable. Continue expanding when your army can protect the new bases. Use Battlecruisers to execute Tactical Jumps into the back of enemy mineral lines for harassment, but avoid sacrificing them unnecessarily. Launch a massive, decisive zone attack when your overall army value reaches the 50-80 threshold.

End Goal: Build a strong, well-saturated multi-base economy with enough SCVs, protect your bases with Marines and Siege Tanks, and then transition into large-scale Battlecruiser production once the economy and ground army are ready. Continually train units non-stop to create a devastating late-game army of Battlecruisers, fully supported by an ongoing stream of Marines and Siege Tanks.