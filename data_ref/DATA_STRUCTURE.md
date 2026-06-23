# StarCraft II Structured Data Graph — Data Structure Reference

*Generated: 2026-06-23*
*Source file: `data_base_add_graph.json`*

---

This document describes the structure, fields, and semantics of `data_base_add_graph.json`, a structured knowledge graph of StarCraft II: Legacy of the Void units, buildings, abilities, and upgrades.

## 1. Overview

`data_base_add_graph.json` is derived from the raw `data.json` (the game's internal data definitions). It retains the original top-level sections, replaces internal numeric identifiers with human-readable names, and enriches every object with three additional fields: **tech chains**, **natural-language descriptions**, and **inter-entity relationship edges** (the knowledge graph).

### 1.1 Top-Level Structure

| Key | Type | Count | Description |
| --- | --- | ---: | --- |
| `Ability` | list\<object\> | 683 | Skills, actions, and commands — train, build, research, cast, move, attack, morph, etc. |
| `Unit` | list\<object\> | 204 | Units and buildings across all three races (Terran, Protoss, Zerg), including transformed and morphed variants. |
| `Upgrade` | list\<object\> | 124 | Technologies and upgrades — weapon/armor tiers, ability unlocks, stat boosts, and special research. |

### 1.2 Three Enriched Fields

Every object in `Ability`, `Unit`, and `Upgrade` carries these three additional keys, regardless of whether their values are non-empty:

| Key | Type | Purpose |
| --- | --- | --- |
| `tech_chain` | list\<string\> | Tech-tree unlock paths. 100% coverage across all 1011 entities. Each string describes the building/upgrade chain needed to unlock the entity. Multipath alternatives are separate list entries. Parallel AND dependencies within a single chain use the `[path A] + [path B] -> target` notation. |
| `description` | list\<string\> | Natural-language descriptions drawn from Liquipedia summaries and CSV data. Multiple paragraphs are stored as separate list entries. |
| `relations` | list\<object\> | Inter-entity relationship edges forming the knowledge graph. Each edge is a directed triple `(subject_name, relation, object_name)` with an English description. |

---

## 2. Entity Field Reference

### 2.1 Ability (683 items, 13 fields)

Ability objects represent skills, actions, and commands available in the game. They retain searchable fields from `data.json` plus the three enriched fields.

| Field | Type | Present In | Description |
| --- | --- | :---: | --- |
| `name` | string | all | The canonical ability / action name (e.g. `Stimpack`, `BARRACKSTRAIN_MARINE`). |
| `cast_range` | number | all | How far the ability can be cast (0 for melee or self-targeted actions). |
| `energy_cost` | number | all | Energy required to use the ability (0 for most production and movement actions). |
| `allow_minimap` | bool | all | Whether the ability can be issued through minimap targeting. |
| `allow_autocast` | bool | all | Whether autocast behavior is supported. |
| `buff` | list | all | Buff identifiers or metadata applied by the ability. Often an empty list. |
| `effect` | list | all | Effect identifiers or metadata triggered by the ability. Often an empty list. |
| `cooldown` | number | all | Cooldown duration in game time (0 for most actions). |
| `target` | string or object | all | Target type descriptor. Either a plain string (`"None"`, `"Unit"`, `"Point"`, `"PointOrUnit"`) or a structured object keyed by action category: `{"Build": ...}`, `{"Train": ...}`, `{"Morph": ...}`, `{"Research": ...}`, `{"BuildOnUnit": ...}`, `{"MorphPlace": ...}`, `{"BuildInstant": ...}`, `{"TrainPlace": ...}`. Structured targets contain sub-keys like `produces_name` or `upgrade_name` that link to Unit or Upgrade names. |
| `remaps_to_ability_name` | string or null | 215 of 683 | When present, points to a more generic ability name that this specific command maps to (e.g. a race-specific morph command remapped to a standard morph ability). Null or absent otherwise. |
| `tech_chain` | list\<string\> | all | Enriched: the tech-tree path(s) required to unlock this ability. |
| `description` | list\<string\> | 312 of 683 | Enriched: natural-language description of what the ability does. Sourced from CSV `ability_csv_data_action_mapping.json`. |
| `relations` | list\<object\> | 345 of 683 | Enriched: relationship edges where this ability is the subject. |

**Example — `RESEARCH_TUNNELINGCLAWS` (Ability with `ability_requires_unit`):**

```json
{
  "name": "RESEARCH_TUNNELINGCLAWS",
  "cast_range": 0.0,
  "energy_cost": 0,
  "allow_minimap": false,
  "allow_autocast": false,
  "buff": [],
  "effect": [],
  "cooldown": 0,
  "target": "None",
  "remaps_to_ability_name": null,
  "tech_chain": ["TunnelingClaws: [Lair (RESEARCH_TUNNELINGCLAWS)] -> TunnelingClaws (RESEARCH_TUNNELINGCLAWS)"],
  "description": [],
  "relations": [
    {"subject_name": "RESEARCH_TUNNELINGCLAWS", "relation": "ability_requires_unit", "object_name": "Lair", "description": "Using RESEARCH_TUNNELINGCLAWS requires the prerequisite unit or structure Lair."}
  ]
}
```

---

### 2.2 Unit (204 items, 39 fields)

Unit objects represent units, buildings, and their transformed/morphed variants. They retain searchable fields from `data.json` plus the three enriched fields, and **additionally include the `attack_type` field** not present in earlier builds of this dataset.

| Field | Type | Present In | Description |
| --- | --- | :---: | --- |
| `name` | string | all | The canonical unit or building name (e.g. `Marine`, `Barracks`, `SiegeTank`). |
| `race` | string | all | Race: `"Terran"`, `"Protoss"`, or `"Zerg"`. |
| `supply` | number | all | Supply consumed (positive) or provided (negative). |
| `max_health` | number | all | Maximum hit points. |
| `armor` | number | all | Base armor value. |
| `sight` | number | all | Sight range. |
| `size` | number | all | Source size field from data. |
| `radius` | number | all | Collision / selection radius. |
| `minerals` | number | all | Mineral cost to build or train. |
| `gas` | number | all | Vespene gas cost to build or train. |
| `time` | number | all | Build, train, or research time in game units. |
| `attributes` | list\<string\> | all | Unit attribute tags such as `"Light"`, `"Armored"`, `"Biological"`, `"Mechanical"`, `"Structure"`, `"Psionic"`, `"Massive"`, `"Heroic"`, `"Summoned"`. |
| `weapons` | list\<object\> | 67 of 204 | Weapon definitions. Each weapon object contains: `target_type` (`"Any"`, `"Ground"`, `"Air"`), `damage_per_hit`, `damage_splash`, `attacks` (number of attacks per volley), `range`, `cooldown`, and `bonuses` (a list of bonus damage entries with `against` attribute name and `damage` value). |
| `abilities` | list\<object\> | all | Abilities available to this unit/building. Each entry contains `ability_name` and may include requirement references (`building_name`, `addon_name`, `addon_to_name`, `upgrade_name`). |
| `attack_type` | string | all | **Added in this build.** Indicates what the unit can attack: `"Both"` (air and ground, 24 units — e.g. Marine, Mothership), `"Ground"` (ground only, 40 units — e.g. Colossus, Baneling), `"Air"` (air only, 10 units — e.g. Phoenix, VikingFighter), `"None"` (cannot attack, 130 units — e.g. TechLab, BanelingCocoon, Overlord). |
| `cargo_capacity` | number | 10 of 204 | Maximum transport capacity, when the unit is a transport (e.g. Medivac, WarpPrism, Overlord). |
| `cargo_size` | number | 46 of 204 | How much cargo space this unit occupies when loaded into a transport. |
| `max_shield` | number | 49 of 204 | Maximum Protoss plasma shield value. Present only on Protoss units. |
| `max_energy` | number | 27 of 204 | Maximum energy pool, present on caster/ability-using units. |
| `start_energy` | number | 27 of 204 | Starting energy when the unit is created. |
| `speed` | number | 102 of 204 | Movement speed. Absent for immobile structures. |
| `speed_creep_mul` | number | all | Creep speed multiplier (1.0 for non-Zerg or units unaffected by creep). |
| `power_radius` | number | 16 of 204 | Protoss power field radius provided by this structure (e.g. Pylon). |
| `detection_range` | number | 8 of 204 | Detection range for detector units/structures (e.g. Observer, SporeCrawler). |
| `unit_alias_name` | string | 40 of 204 | Alternative display name, when present. Empty numeric aliases from source data are omitted. |
| `normal_mode_name` | string | 52 of 204 | For mode-switching variants, the name of the unit's normal/base form (e.g. `"Hellion"` for `"HellionTank"`). |
| `tech_alias_names` | list\<string\> | 32 of 204 | Unit names treated as equivalent for tech-tree purposes. |
| `is_structure` | bool | all | Whether this is a structure / building (81 of 204 are true). |
| `is_addon` | bool | all | Whether this is a Terran add-on (8 of 204, e.g. TechLab, Reactor). |
| `is_worker` | bool | all | Whether this is a worker unit (3 of 204: SCV, Probe, Drone). |
| `is_townhall` | bool | all | Whether this is a town hall / main base (9 of 204). |
| `is_flying` | bool | all | Whether this unit/building is flying (51 of 204). |
| `accepts_addon` | bool | all | Whether the structure can accept add-ons (3 of 204: Barracks, Factory, Starport). |
| `needs_power` | bool | all | Whether the structure requires Protoss power (13 of 204). |
| `needs_creep` | bool | all | Whether the unit/building requires creep (19 of 204). |
| `needs_geyser` | bool | all | Whether the structure must be built on a Vespene geyser (6 of 204). |
| `tech_chain` | list\<string\> | all | Enriched: the tech-tree path(s) required to unlock or build this unit. |
| `description` | list\<string\> | 118 of 204 | Enriched: natural-language description paragraphs. Sourced from Liquipedia summaries via `DATA_summary_classified/category_1_unit_attributes.json`. |
| `relations` | list\<object\> | 180 of 204 | Enriched: relationship edges where this unit is the subject. |

**Example — `Marine` (Terran, `attack_type: Both`):**

```json
{
  "name": "Marine",
  "race": "Terran",
  "supply": 1.0,
  "max_health": 45.0,
  "armor": 0.0,
  "sight": 9.0,
  "size": 0,
  "radius": 0.375,
  "minerals": 50,
  "gas": 0,
  "time": 400.0,
  "attributes": ["Light", "Biological"],
  "weapons": [{"target_type": "Any", "damage_per_hit": 6.0, "damage_splash": 0, "attacks": 1, "range": 5.0, "cooldown": 0.86, "bonuses": []}],
  "abilities": [{"ability_name": "STOP_STOP"}, {"ability_name": "MOVE_MOVE"}, {"ability_name": "PATROL_PATROL"}, {"ability_name": "HOLDPOSITION_HOLD"}, ...],
  "attack_type": "Both",
  "cargo_size": 1,
  "speed": 2.25,
  "speed_creep_mul": 1.0,
  "is_structure": false,
  "is_addon": false,
  "is_worker": false,
  "is_townhall": false,
  "is_flying": false,
  "accepts_addon": false,
  "needs_power": false,
  "needs_creep": false,
  "needs_geyser": false,
  "tech_chain": ["Marine: [SupplyDepot -> Barracks] -> Marine (BARRACKSTRAIN_MARINE)"],
  "description": ["Marines are the all-purpose infantry unit produced from a Barracks. ...", "However, Marines can take advantage of kiting and Stutter Stepping..."],
  "relations": [
    {"subject_name": "Marine", "relation": "has_ability", "object_name": "STOP_STOP", "description": "Marine has access to the ability STOP_STOP."},
    {"subject_name": "Marine", "relation": "has_ability", "object_name": "MOVE_MOVE", "description": "Marine has access to the ability MOVE_MOVE."},
    {"subject_name": "Marine", "relation": "soft_counters", "object_name": "Zealot", "description": "The Marine soft counters the Zealot by kiting and using Stimpack to maintain distance..."},
    ...
  ]
}
```

**Example — `Phoenix` (Protoss, `attack_type: Air`):**

```json
{
  "name": "Phoenix",
  "race": "Protoss",
  "attack_type": "Air",
  "weapons": [{"target_type": "Air", "damage_per_hit": 5.0, "damage_splash": 0, "attacks": 2, "range": 5.0, "cooldown": 1.1, "bonuses": [{"against": "Light", "damage": 5.0}]}],
  ...
}
```

**Example — `Colossus` (Protoss, `attack_type: Ground`):**

```json
{
  "name": "Colossus",
  "race": "Protoss",
  "attack_type": "Ground",
  "weapons": [{"target_type": "Ground", ...}],
  ...
}
```

**Example — `TechLab` (Terran, `attack_type: None`):**

```json
{
  "name": "TechLab",
  "race": "Terran",
  "attack_type": "None",
  "weapons": null,
  ...
}
```

---

### 2.3 Upgrade (124 items, 5 fields)

Upgrade objects represent researched technologies. They retain the cost field from `data.json` plus the three enriched fields.

| Field | Type | Present In | Description |
| --- | --- | :---: | --- |
| `name` | string | all | The canonical upgrade / technology name (e.g. `Stimpack`, `TerranInfantryWeaponsLevel1`, `BlinkTech`). |
| `cost` | object | all | Research cost with keys: `minerals`, `gas`, `time` (research duration in game units). |
| `tech_chain` | list\<string\> | all | Enriched: the tech-tree path(s) required to research this upgrade. |
| `description` | list\<string\> | 87 of 124 | Enriched: natural-language description of the upgrade's effect. Sourced from CSV `ability_csv_data_action_mapping.json`. |
| `relations` | list\<object\> | 72 of 124 | Enriched: relationship edges where this upgrade is the subject. |

**Example — `TerranInfantryWeaponsLevel1`:**

```json
{
  "name": "TerranInfantryWeaponsLevel1",
  "cost": {"minerals": 100, "gas": 100, "time": 2560.0},
  "tech_chain": ["TerranInfantryWeaponsLevel1: [EngineeringBay] -> TerranInfantryWeaponsLevel1 (ENGINEERINGBAYRESEARCH_TERRANINFANTRYWEAPONSLEVEL1)"],
  "description": ["Increase the damage of Terran infantry units (Ghost, Marauder, Reaper, Marine)."],
  "relations": [
    {"subject_name": "TerranInfantryWeaponsLevel1", "relation": "unlocks_unit_ability", "object_name": "EngineeringBay", "description": "..."},
    {"subject_name": "TerranInfantryWeaponsLevel1", "relation": "grants_stat_bonus", "object_name": "Ghost", "description": "..."},
    {"subject_name": "TerranInfantryWeaponsLevel1", "relation": "grants_stat_bonus", "object_name": "Marauder", "description": "..."},
    {"subject_name": "TerranInfantryWeaponsLevel1", "relation": "grants_stat_bonus", "object_name": "Reaper", "description": "..."},
    {"subject_name": "TerranInfantryWeaponsLevel1", "relation": "grants_stat_bonus", "object_name": "Marine", "description": "..."}
  ]
}
```

---

## 3. Relations — The Knowledge Graph

### 3.1 Relation Object Structure

Every relation edge in the graph has exactly four keys:

| Key | Type | Description |
| --- | --- | --- |
| `subject_name` | string | The canonical name of the entity that is the **source** (origin) of this relation. Always matches the `name` of the parent object that contains this relation. |
| `relation` | string | The relationship type — one of the 15 types listed below. |
| `object_name` | string | The canonical name of the entity that is the **target** (destination) of this relation. |
| `description` | string | A single natural-language English sentence describing this specific relationship. |

### 3.2 Total Counts

| Metric | Value |
| --- | ---: |
| Total relation edges | 3,023 |
| Relation types | 15 |
| Ability entries with non-empty relations | 345 of 683 |
| Unit entries with non-empty relations | 180 of 204 |
| Upgrade entries with non-empty relations | 72 of 124 |

### 3.3 Distribution by Entity Section

| Section | Relation Edges | Key Relation Types |
| --- | ---: | --- |
| Ability | 512 | `action_result` (326), `ability_requires_unit` (126), `ability_requires_upgrade` (60) |
| Unit | 2,228 | `has_ability` (1078), `soft_counters` (311), `synergizes_with` (223), `hard_counters` (205), `morphs_into` (123), `produces` (108), `researches` (105), `spawns` (47), `garrisons_in` (28) |
| Upgrade | 283 | `grants_stat_bonus` (207), `unlocks_unit_ability` (61), `enables_morph` (15) |

### 3.4 Origins of Relations — Data-Derived vs. LLM-Extracted

The 15 relation types come from two fundamentally different sources, distinguished by how they are created:

#### Category A: Data-Derived Relations (9 types)

These relations are **deterministically derived** from the structured data in `data.json`. No LLM is involved. The logic examines the raw fields (abilities, target, production records, tech requirements) and generates edges through rule-based inference.

They are further subdivided:

**A1. Direct extraction from raw data (3 types)**

These are read directly from the source data without any cross-referencing or inference:

| Relation | Source Logic |
| --- | --- |
| `has_ability` | For every ability listed in a Unit's `abilities` array, generate `Unit → Ability`. This is a direct one-to-many mapping. |
| `ability_requires_unit` | For every building/addon/unit prerequisite named in an Ability's requirement context (e.g. "this train ability requires a Barracks"), generate `Ability → Unit`. |
| `ability_requires_upgrade` | For every upgrade prerequisite named in an Ability's requirement context, generate `Ability → Upgrade`. |

**A2. Inferred from chaining (3 types)**

These are computed by combining information from multiple fields across the data:

| Relation | Inference Logic |
| --- | --- |
| `produces` | Match a Unit's `has_ability` edge to an Ability whose `target` is a `Train`, `Build`, `BuildOnUnit`, or `BuildInstant` action that produces another Unit. Generates `Unit → Unit`. |
| `researches` | Match a Unit's `has_ability` edge to an Ability whose `target` is a `Research` action. Generates `Unit → Upgrade`. |
| `unlocks_unit_ability` | Reverse inference: for every Unit's ability entry that has an `upgrade_name` requirement, generate `Upgrade → Unit`. This says "this upgrade unlocks/gates this ability for this unit." |

**A3. Inferred from morph/spawn actions (2 types)**

| Relation | Inference Logic |
| --- | --- |
| `morphs_into` | When an Ability's `target` is a `Morph` or `MorphPlace` action that produces a different Unit, generate `Unit → Unit` (source morphs into result). Includes burrow/unburrow, mode switches (e.g. SiegeTank ↔ SiegeTankSieged, Hellion ↔ Hellbat), and building lifts/lands. |
| `spawns` | When an Ability's `target` produces a temporary or summoned Unit, generate `Unit → Unit` (source spawns the result). Examples: InfestorTerran → InfestedTerransEgg, BroodLord → Broodling. |

**A4. Direct chaining from base relations (1 type)**

| Relation | Source Logic |
| --- | --- |
| `action_result` | Every Ability whose `target` field is a structured payload (`Train`, `Build`, `Morph`, `Research`, etc.) generates an `Ability → Unit/Upgrade` edge pointing to whatever that ability produces or researches. |

---

#### Category B: LLM-Extracted Semantic Relations (6 types)

These relations are **extracted from unstructured natural-language text** — specifically Liquipedia unit and building summary pages stored in the `DATA/` directory. An LLM (Kimi k2.5) reads paragraphs of tactical and strategic analysis and identifies explicit relationships between entities that are not present in the structured game data.

The LLM extraction follows a three-stage pipeline:

1. **Extract**: The LLM reads a single summary paragraph and extracts raw relation mentions. Raw endpoint names may be concrete entities, generic phrases, or attribute class names (e.g. `"Armored"`, `"Biological"`).
2. **Map**: The LLM normalizes raw endpoint names to canonical names from `data_base_add_graph.json` or to one of nine allowed external attribute entities (see §3.6 below).
3. **Describe**: The LLM writes a concise English sentence describing this specific relation.

The 6 LLM-extracted types are:

| Relation | Typical Direction | Extraction Logic |
| --- | --- | --- |
| `hard_counters` | `Unit → Unit` or `Unit → attribute` | Explicit bonus damage, weapon advantage (e.g. +dmg vs Armored), or a decisive one-sided matchup where the subject reliably destroys the object. |
| `soft_counters` | `Unit → Unit` or `Unit → attribute` | Tactical pressure through range, mobility, area-of-effect, cost efficiency, or general matchup favorability rather than raw damage bonus. |
| `synergizes_with` | `Unit → Unit` | Two units that work especially well together — complementary roles, covering each other's weaknesses, or enabling combined tactics. |
| `grants_stat_bonus` | `Upgrade → Unit` | An upgrade that permanently improves the stats (damage, armor, speed, shields, range, health, energy) of a specific unit. |
| `garrisons_in` | `Unit → Unit` | One unit can enter, load into, or be sheltered inside another unit or structure (transports, bunkers, etc.). |
| `enables_morph` | `Upgrade or Structure Unit → Unit` | An upgrade or structure that unlocks a unit's ability to transform or change functional form (e.g. BanelingNest enables Zergling → Baneling morph). |

**Key Difference**: Data-derived relations are guaranteed accurate — they come from definitive structured game data. LLM-extracted relations carry an inherent fuzziness from natural language interpretation. They are validated against ontology constraints (subject/object type checks) before entering the final dataset, but their semantic content reflects the tactical understanding expressed in community-authored Liquipedia text.

---

### 3.5 Complete Relation Type Reference (15 types, 3,023 edges)

The table below gives the full specification for every relation type present in `data_base_add_graph.json`. The **Appears In** column tells you which top-level section contains the subject entities; this is also the subject type constraint.

| # | Relation | Count | Appears In | Direction | Source | Description |
| --- | --- | ---: | :---: | --- | :---: | --- |
| 1 | `has_ability` | 1,078 | Unit | **Unit** → Ability | Data-derived (direct) | The unit has this ability listed in its available ability set. This is the most common relation type. Example: `Colossus has_ability STOP_STOP` — "Colossus has access to the ability STOP_STOP." |
| 2 | `action_result` | 326 | Ability | **Ability** → Unit or Upgrade | Data-derived (direct) | Executing this ability produces, transforms into, researches, or otherwise results in the target entity. The exact result type depends on the ability's `target` payload: `Train`/`Build` → produces a Unit; `Research` → researches an Upgrade; `Morph` → transforms into a Unit. Example: `MORPHTOINFESTEDTERRAN_INFESTEDTERRANS action_result InfestorTerran` — "Executing MORPHTOINFESTEDTERRAN_INFESTEDTERRANS results in Unit InfestorTerran." |
| 3 | `soft_counters` | 311 | Unit | **Unit** → Unit or attribute | LLM-extracted (semantic) | The subject pressures or has a tactical advantage against the object through range, mobility, area-of-effect damage, cost efficiency, or general matchup profile — without explicit bonus damage. The object may be a concrete unit or an attribute class (e.g. `Light`, `Armored`). Example: `Colossus soft_counters SiegeTank` — "The Colossus soft counters the Siege Tank by using its range and terrain to kite ground units, despite the Siege Tank's superior single-target damage." |
| 4 | `synergizes_with` | 223 | Unit | **Unit** → Unit | LLM-extracted (semantic) | The subject and object work especially well together. Their abilities, roles, or stat profiles complement each other — e.g. one tanks damage while the other deals it, one provides detection while the other provides firepower, or their spell effects combine effectively. Example: `Colossus synergizes_with Sentry` — "The Colossus synergizes with the Sentry because the Sentry's force fields and guardian shield protect the Colossus while it delivers splash damage." |
| 5 | `grants_stat_bonus` | 207 | Upgrade | **Upgrade** → Unit | LLM-extracted (semantic) | The upgrade permanently improves one or more stats of the target unit: weapon damage, armor, movement speed, shields, attack range, health, or energy. Example: `ChitinousPlating grants_stat_bonus Ultralisk` — "Chitinous Plating grants a stat bonus to the Ultralisk by reducing damage taken." |
| 6 | `hard_counters` | 205 | Unit | **Unit** → Unit or attribute | LLM-extracted (semantic) | The subject has explicit bonus damage or a decisive one-sided counter against the object. This is typically backed by weapon `bonuses[].against` values in the structured data (e.g. +dmg vs Armored, +dmg vs Light). The object may be a concrete unit or an attribute class. Example: `Colossus hard_counters Marine` — "The Colossus hard counters Marines because it deals bonus damage against light units." |
| 7 | `ability_requires_unit` | 126 | Ability | **Ability** → Unit | Data-derived (direct) | The ability cannot be used unless the target unit or structure exists as a prerequisite, production building, tech structure, or attachment. This encodes tech-tree dependencies: a train ability requires the production building; a research ability requires the research structure; an upgrade level requires the higher-tier tech building. Examples: `BARRACKSTRAIN_MARAUDER ability_requires_unit TechLab`, `RESEARCH_ZERGMELEEWEAPONSLEVEL3 ability_requires_unit Hive`. |
| 8 | `morphs_into` | 123 | Unit | **Unit** → Unit | Data-derived (inferred) | The subject can transform, morph, land/lift, burrow/unburrow, or switch mode into the object through a Morph-style ability. Examples: `TechLab morphs_into BarracksTechLab` (add-on attachment), `SiegeTank morphs_into SiegeTankSieged` (mode switch). |
| 9 | `produces` | 108 | Unit | **Unit** → Unit | Data-derived (inferred) | The subject building can directly train, build, or construct the object unit through a Train/Build-style ability. Examples: `CommandCenter produces SCV`, `Barracks produces Marine`, `Gateway produces Zealot`. |
| 10 | `researches` | 105 | Unit | **Unit** → Upgrade | Data-derived (inferred) | The subject building can research the object upgrade through a Research-style ability. Examples: `TechLab researches Stimpack`, `Spire researches ZergFlyerWeaponsLevel1`. |
| 11 | `unlocks_unit_ability` | 61 | Upgrade | **Upgrade** → Unit | Data-derived (inferred) | The upgrade unlocks a new available ability for the target unit. Derived from the `upgrade_name` requirement in the unit's ability entries. Examples: `Stimpack unlocks_unit_ability Marine`, `BlinkTech unlocks_unit_ability Stalker`, `WarpGateResearch unlocks_unit_ability Gateway`. |
| 12 | `ability_requires_upgrade` | 60 | Ability | **Ability** → Upgrade | Data-derived (direct) | The ability cannot be used until the target upgrade has been researched. Example: `SMART ability_requires_upgrade TunnelingClaws` — "Using SMART requires the upgrade TunnelingClaws." |
| 13 | `spawns` | 47 | Unit | **Unit** → Unit | Data-derived (inferred) | The subject can create temporary, summoned, or parasitic units through an ability. These are distinct from production (which creates permanent units from buildings). Examples: `InfestorTerran spawns InfestedTerransEgg`, `BroodLord spawns Broodling`. |
| 14 | `garrisons_in` | 28 | Unit | **Unit** → Unit | LLM-extracted (semantic) | The subject unit can enter, be loaded into, or be sheltered inside the object structure or transport. Examples: `Marine garrisons_in Bunker`, `Colossus garrisons_in WarpPrism`, `SiegeTank garrisons_in Medivac`. |
| 15 | `enables_morph` | 15 | Upgrade | **Upgrade** → Unit | LLM-extracted (semantic) | The upgrade or structure enables the target unit to access a morph or form-change option that was previously unavailable. Example: `Burrow enables_morph InfestorTerran` — "Burrow enables InfestorTerran to access a morph or form-change option." |

---

### 3.6 Ontological Constraints

Every relation in the dataset must satisfy type constraints on both the subject and object. These constraints are enforced during both data-derived inference and LLM-extracted post-processing:

| Relation | Subject Must Be | Object Must Be |
| --- | --- | --- |
| `has_ability` | Unit | Ability |
| `action_result` | Ability | Unit or Upgrade |
| `soft_counters` | Unit | Unit or allowed external attribute |
| `synergizes_with` | Unit | Unit |
| `grants_stat_bonus` | Upgrade | Unit |
| `hard_counters` | Unit | Unit or allowed external attribute |
| `ability_requires_unit` | Ability | Unit |
| `morphs_into` | Unit | Unit |
| `produces` | Unit | Unit |
| `researches` | Unit | Upgrade |
| `unlocks_unit_ability` | Upgrade | Unit |
| `ability_requires_upgrade` | Ability | Upgrade |
| `spawns` | Unit | Unit |
| `garrisons_in` | Unit | Unit |
| `enables_morph` | Upgrade or structure Unit | Unit |

For `hard_counters` and `soft_counters`, the object may be a concrete Unit name or one of nine **external attribute entities** that represent unit class tags rather than specific units:

| Attribute | Definition |
| --- | --- |
| `Armored` | Armor tag carried by most structures, heavy vehicles, capital ships, and massive combat units. |
| `Biological` | Biology tag carried by Terran infantry/SCVs, most Zerg units/structures, and several Protoss infantry/caster units. |
| `Structure` | Structure tag for production, tech, defensive, and static buildings (including lifted/uprooted variants). |
| `Mechanical` | Mechanical tag for Terran vehicles/aircraft/structures, most Protoss units, and select Zerg structures. |
| `Light` | Light armor tag for infantry and small/fragile units — Marines, Zealots, Zerglings, Mutalisks, Phoenixes, Banelings, workers. |
| `Psionic` | Psionic tag for spellcaster/psychic entities — Ghosts, Templar, Sentries, Infestors, Queens, Oracles, Vipers, Mothership. |
| `Massive` | Massive tag for very large units — Colossus, Mothership, Thor, Battlecruiser, Carrier, Ultralisk, Brood Lord, Archon, Tempest. |
| `Heroic` | Heroic tag; in this dataset applicable only to Mothership-like heroic entities. |
| `Summoned` | Summoned tag; in this dataset applicable only to Raven-repair-drone-style summoned entities. |

---

### 3.7 Deprecated Relations

The following legacy relation types were present in earlier versions of the graph but have been removed during normalization. They are **not** present in `data_base_add_graph.json`:

| Deprecated Relation | Reason for Removal |
| --- | --- |
| `requires_tech` | Superseded by `ability_requires_unit` and `ability_requires_upgrade`, which provide more precise directionality. |
| `unlocks_ability` | Superseded by `unlocks_unit_ability`, which explicitly names the target unit. |
| `provides_supply` | Removed as supply information is already captured in the Unit `supply` field. |
| `applies_status` | Listed in the ontology specification but not currently populated in the dataset. Reserved for future extraction of temporary buff/debuff relationships. |

In total, 3,493 legacy relations were processed; after deduplication, type validation, and ontology normalization, 3,023 relations remain in the current graph.

---

## 4. Auxiliary Fields in Detail

### 4.1 `tech_chain`

- **Coverage**: 100% — all 1,011 entities (683 Ability + 204 Unit + 124 Upgrade) have at least one tech chain entry.
- **Format**: Each entry is a string describing the sequence of dependencies needed to unlock or access the entity. The general form is: `TargetName: [prerequisite_1 -> prerequisite_2 -> ... ] -> Target (internal_id)`.
- **Parallel dependencies**: When two or more prerequisites must be satisfied simultaneously, the `+` operator joins them: `Stimpack: [Barracks + TechLab] -> Stimpack`.
- **Multiple alternative paths**: Listed as separate elements in the `tech_chain` array. Each represents a different route to the same target.

### 4.2 `description`

- **Coverage**: Ability 312/683 (46%), Unit 118/204 (58%), Upgrade 87/124 (70%).
- **Unit descriptions**: Mapped from Liquipedia summaries in `DATA_summary_classified/category_1_unit_attributes.json`. Mapped by normalized name (case-insensitive, ignoring spaces, underscores, and punctuation).
- **Ability and Upgrade descriptions**: Mapped from CSV descriptions in `DATA_0/ability_csv_data_action_mapping.json` where `data_ability.name` (for abilities) or `data_upgrade.name` (for upgrades) matches the canonical name.
- **Alias handling**: Some Liquipedia display names differ from internal `data.json` names. Explicit aliases bridge these cases: `Stasis Ward → OracleStasisTrap`, `Templar Archives → TemplarArchive`, `Hellbat → HellionTank`, `Lurker → LurkerMP`, etc.
- Multiple paragraphs per entity are stored as separate list entries.
- Source metadata (URLs, file paths) is intentionally stripped from actual values; the mapping rules are documented here instead.

---

## 5. Coverage Summary

| Section | Count | Has `tech_chain` | Has `description` | Has `relations` |
| --- | ---: | ---: | ---: | ---: |
| Ability | 683 | 683 (100%) | 312 (46%) | 345 (51%) |
| Unit | 204 | 204 (100%) | 118 (58%) | 180 (88%) |
| Upgrade | 124 | 124 (100%) | 87 (70%) | 72 (58%) |
| **Total** | **1,011** | **1,011 (100%)** | **517 (51%)** | **597 (59%)** |

| Relation Type | Count |
| --- | ---: |
| `has_ability` | 1,078 |
| `action_result` | 326 |
| `soft_counters` | 311 |
| `synergizes_with` | 223 |
| `grants_stat_bonus` | 207 |
| `hard_counters` | 205 |
| `ability_requires_unit` | 126 |
| `morphs_into` | 123 |
| `produces` | 108 |
| `researches` | 105 |
| `unlocks_unit_ability` | 61 |
| `ability_requires_upgrade` | 60 |
| `spawns` | 47 |
| `garrisons_in` | 28 |
| `enables_morph` | 15 |
| **Total** | **3,023** |

---

## 6. Build Notes

- The builder script is `DATA_TOOLS/build_data_base.py`.
- Internal numeric IDs from `data.json` are replaced with human-readable names throughout.
- Objects without a matched chain, description, or relation still include the corresponding key with an empty list (`[]`).
- Relation target names are resolved against the same canonical name set as the rest of the database.
- This build (2026-06-14) adds the `attack_type` field to every Unit object (values: `"Both"`, `"Ground"`, `"Air"`, `"None"`), which was not present in earlier versions of the dataset.
