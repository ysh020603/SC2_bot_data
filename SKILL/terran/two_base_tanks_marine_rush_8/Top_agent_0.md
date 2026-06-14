# Summary

Two-base Marine + Siege Tank macro build. Tech up via 5 Barracks (1 Tech Lab + Reactors) and 3 Factory Tech Labs, prioritising Marine buffer first, then mass-Tank production. End-game goal: 20 Siege Tanks and 100 Marines.

# Details

Your overall strategy is an "Extreme Tank-First" defensive build. It is optimized to completely shut down early Terran aggression by rushing a Siege Tank as fast as physically possible. You will treat the initial Barracks purely as a tech requirement and delay all other economic upgrades (including Orbital Command) until the first Tank secures your ramp.

* Universal Economy & Supply Control:
  - Strict SCV Saturation: Monitor the `current/ideal` worker count. Halt SCV production at exactly 44 SCVs.
  - Smart Supply Management (Observation-Driven): STRICTLY read your `Supply` state in the observation (e.g., `25/31`). In the early game (Phase 1 & 2), build exactly ONE Supply Depot ONLY when your current supply is within 4-6 of your max supply limit (e.g., `max_supply - current_supply <= 6`). Do NOT blindly queue multiple Depots early, as this drains critical minerals. Only in the late game (Phase 3) when supply > 60, you may keep 2 Depots under construction to support mass Marine production. Never build Depots at 200 supply.
  - Resource Rebalancing: If gas floats >500, immediately drop additional Factories with Tech Labs. If minerals float >600 later in the game, drop extra Barracks.

* Phase 1: Extreme Tank Tech Rush (Maximum Speed):
  - Build Order: 1st Supply Depot (at 14 supply) -> 1st Barracks -> 1st gas Refinery.
  - CRITICAL MINERAL RULE: Treat the 1st Barracks STRICTLY as a tech building. Do NOT train any Marines. Furthermore, do NOT morph your Command Center to an Orbital Command yet. Save the 150 minerals strictly for your Factory.
  - Build your 1st Factory the exact second you accumulate 100 gas. Attach a Tech Lab immediately upon completion.
  - As soon as the Tech Lab finishes, issue the task `Train Siege Tank (Priority)` to force all early gas and minerals into your 1st Anchor Tank. 

* Phase 2: Stabilization & Economy Recovery:
  - ONLY AFTER your 1st Siege Tank has started training, immediately morph your Command Center into an Orbital Command to catch up on economy using MULEs.
  - Rely entirely on your first few Siege Tanks (assisted by SCV repair if necessary) to hold the ramp.
  - Continue producing Siege Tanks. Only after you have successfully deployed at least 3 to 4 Siege Tanks, construct your 2nd Command Center (Expansion).

* Phase 3: Marine Transition & Macro Escalation:
  - ONLY after the 2nd Command Center is started and your Tank line is stable, begin your bio transition.
  - Build up to a total of 5 Barracks. Attach Reactors to 4 of them, and 1 Tech Lab for Stimpack research.
  - NOW begin continuously training Marines to serve as a mobile meatshield for your Tanks.
  - Scale up to 3 Factories (all with Tech Labs) to increase Siege Tank output.
  - End Goal: Push with a maxed-out composition of 20 Siege Tanks and 100 Marines, supported by a healthy economy of exactly 44 SCVs.