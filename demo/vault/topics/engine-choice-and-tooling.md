---
type: "topic"
namespace: "topics"
slug: "engine-choice-and-tooling"
member_count: 25
member_hash: "9f061c526a1ccb87"
created: "2026-07-20T18:23:44"
updated: "2026-07-20T18:23:44"
---

<!-- memsub:distill:begin -->

# Maya's Engine Switch for Lighthouse Keeper

Maya initially developed Lighthouse Keeper in Godot 4 due to its open-source model and GDScript syntax, but later switched to Unity after Unity's Indie Growth tier offered better tools and cost efficiency for her project.

## Key facts

- Maya compared Godot 4 and Unity licensing costs for a solo indie developer before starting her game Lighthouse Keeper, and Godot's zero-royalty open-source model stood out immediately. [[maya-compared-godot-4-and-unity-licensing-costs-fo--b56d1170]]
- Maya read through Unity's per-seat subscription tiers and runtime fee terms, finding them confusing for a one-person studio that was still pre-revenue. [[maya-read-through-unity-s-per-seat-subscription-ti--4d9fb986]]
- Maya decided to build Lighthouse Keeper in Godot 4, drawn by the free license, GDScript's Python-like syntax, and the lightweight editor. [[maya-decided-to-build-lighthouse-keeper-in-godot-4--c86a4f4f]]
- Maya finished the official Godot 'Your First 2D Game' tutorial and began sketching Lighthouse Keeper's movement controller in GDScript. [[maya-finished-the-official-godot-your-first-2d-gam--30f56afa]]
- Maya followed a GDScript state-machine tutorial to structure the lighthouse keeper character's walk, climb, and interact states. [[maya-followed-a-gdscript-state-machine-tutorial-to--dbda1bdb]]
- Maya hit a wall with Godot's TileMap layering for the lighthouse interior and spent a weekend in the Godot Discord getting help. [[maya-hit-a-wall-with-godot-s-tilemap-layering-for--3dac6b4d]]
- Maya interviewed Priya Anand, a freelance Godot contractor, about taking on the save-system and settings-menu work for Lighthouse Keeper. [[maya-interviewed-priya-anand-a-freelance-godot-con--8be9e037]]
- Maya hired Priya Anand for ten hours a week of Godot contract work to help with the Lighthouse Keeper's save system. [[maya-hired-priya-anand-for-ten-hours-a-week-of-god--4b518c1c]]
- Priya Anand shipped the first version of Lighthouse Keeper's save system using Godot's built-in resource format. [[priya-anand-shipped-the-first-version-of-lighthous--7bac2c6b]]
- Maya and Priya Anand spent two hours debugging a save-corruption bug that only appeared after the third in-game day. [[maya-and-priya-anand-spent-two-hours-debugging-a-s--908c2521]]
- When Godot 4.3 was released, Maya upgraded the Lighthouse Keeper project and fixed two small GDScript deprecation warnings. [[when-godot-4-3-was-released-maya-upgraded-the-ligh--a812566e]]
- Maya posted in an indie dev forum asking Godot and Unity users about console-porting problems, and most replies favored Unity for a first console release. [[maya-posted-in-an-indie-dev-forum-asking-godot-and--27d135c8]]
- Maya Chen followed the Unity announcement closely, noting the new Indie Growth tier that waives the per-install runtime fee entirely for studios under two hundred thousand dollars in annual revenue. [[maya-chen-followed-the-unity-announcement-closely--2737f9e4]]
- Maya re-evaluated Unity under the new Indie Growth tier and was impressed by how much larger the Unity Asset Store's dialogue-system plugin selection had become. [[maya-re-evaluated-unity-under-the-new-indie-growth--67c547d3]]
- Maya worried that the console-porting story for her current engine was still thin for a solo developer without a publisher, while Unity had mature console export templates. [[maya-worried-that-the-console-porting-story-for-he--8c35e601]]
- Maya decided to switch Lighthouse Keeper from Godot to Unity after Unity's Indie Growth pricing change removed the runtime fee, and its asset store filled the dialogue-tooling gap. [[maya-decided-to-switch-lighthouse-keeper-from-godo--8049802d]]
- Maya and Priya Anand agreed to wind down the save-system contract, and Priya recommended a Unity-focused contractor instead. [[maya-and-priya-anand-agreed-to-wind-down-the-save--16fc424e]]
- Maya spent a week porting Lighthouse Keeper's core movement and interaction scripts from GDScript to C# in Unity. [[maya-spent-a-week-porting-lighthouse-keeper-s-core--3888bb65]]
- Maya rebuilt the save system in Unity using JSON serialization, following Priya Anand's original design for the data schema. [[maya-rebuilt-the-save-system-in-unity-using-json-s--16b2b155]]
- Maya Chen completed a stable vertical slice for the Lighthouse Keeper's Unity build, including three rooms, the save system, and the opening cutscene. [[maya-chen-completed-a-stable-vertical-slice-for-th--7aaee855]]
- Maya wrote a devlog reflecting that starting in Godot wasn't wasted time, as the GDScript prototype helped clarify Lighthouse Keeper's core interaction loop before moving to Unity. [[maya-wrote-a-devlog-reflecting-that-starting-in-go--94ac5f64]]
- Maya used GDScript in Godot to prototype Lighthouse Keeper, which helped clarify the game's core interaction loop before porting the core movement and interaction scripts to C# in Unity. [[maya-used-gdscript-in-godot-to-prototype-lighthous--fusion:7]]
- Maya started developing Lighthouse Keeper in Godot 4 due to its zero-royalty open-source model, GDScript's Python-like syntax, and lightweight editor, but later switched to Unity after Unity's Indie Growth pricing change and asset store provided better tools for the project. [[maya-started-developing-lighthouse-keeper-in-godot--fusion:4]]
- Maya tallied the total engine-related costs for Lighthouse Keeper and discovered that the free early months meant the Unity switch still landed well under her original budget. [[maya-tallied-the-total-engine-related-costs-for-li--ed87fcca]]
- Maya developed Lighthouse Keeper, a narrative puzzle game set in a solitary lighthouse keeper's journey to solve the mystery of a vanished ship, using environmental and narrative puzzles, and transitioning from Godot to Unity while maintaining a playtime of three to four hours, with background art in a painterly gouache style and a title screen featuring a slow pan across the lighthouse at dusk. [[maya-developed-lighthouse-keeper-a-narrative-puzzl--fusion:9]]

## Open questions

- How did Maya's experience with Godot influence her decision-making process when switching to Unity?

<!-- memsub:distill:end -->
