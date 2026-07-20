"""Persona data for the build-week demo vault (Maya Chen, indie game developer).

Pure data, no imports beyond the standard library, deliberately separate from
scripts/build_demo_vault.py so the ~130 memory records can be reviewed on
their own (public-safe, zero real-person data -- see WORKLOG.md 2026-07-20
"Build-week demo vault").

Each record: (tag, date, kind, channel, bullet[, valid_from, valid_to]).
tag: stable id used to wire supersede chains and topic membership.
date: "YYYY-MM-DD", the story's world time (also becomes ts).
kind: "fact" | "decision" | "idea".
channel: memsub.provenance channel ("user_stated" default; one "imported").
bullet: the fact, rewritten into warm prose by qwen3:8b at build time --
    every proper noun/number here is preserved verbatim by the generation
    prompt, so entity extraction and the engineered showcase structures
    stay reliable regardless of the LLM's exact phrasing.
valid_from/valid_to: "YYYY-MM-DD" bi-temporal window, only set for the
    freelance-rate story (memsub/temporal.py's fields, set directly since
    the exact validity dates are already known -- see WORKLOG.md
    assumption 4).
"""

# fmt: off
RECORDS = [
    # -- engine choice: THE RIPPLE SHOWCASE ------------------------------- #
    ("engine.pricing_research1", "2026-01-08", "fact", "user_stated",
     "Maya compared Godot 4 and Unity licensing costs for a solo indie "
     "developer before starting her game Lighthouse Keeper; Godot's "
     "zero-royalty open-source model stood out immediately."),
    ("engine.pricing_research2", "2026-01-10", "fact", "user_stated",
     "Maya read through Unity's per-seat subscription tiers and runtime "
     "fee terms and found them confusing for a one-person studio that was "
     "still pre-revenue."),
    # kind="milestone", a value no other record in this dataset uses:
    # memsub.consolidate's contradiction phase only ever pairs same-kind
    # episodes, so a unique kind makes this episode mathematically
    # unpairable there, guaranteeing it survives (still active) into the
    # fusion phase later in the same consolidate() call. kind="fact" was
    # tried first and still collided (paired with an unrelated same-kind
    # "fact" episode and got auto-superseded with reason="contradiction"
    # before fusion ran, stealing the engineered engine-switch reversal) --
    # see WORKLOG.md 2026-07-20 "Build-week demo vault" iteration notes.
    # The deliberate engine-switch supersede is still applied explicitly
    # afterward, unaffected by kind.
    ("engine.decision.godot", "2026-01-14", "milestone", "user_stated",
     "Maya decided to build Lighthouse Keeper in Godot 4, drawn by the "
     "free license, GDScript's Python-like syntax, and the lightweight "
     "editor."),
    ("engine.tutorial1", "2026-01-18", "fact", "user_stated",
     "Maya finished the official Godot 'Your First 2D Game' tutorial and "
     "started sketching Lighthouse Keeper's movement controller in "
     "GDScript."),
    ("engine.tutorial2", "2026-01-25", "fact", "user_stated",
     "Maya followed a GDScript state-machine tutorial to structure the "
     "lighthouse keeper character's walk, climb, and interact states."),
    ("engine.tutorial3", "2026-02-02", "fact", "user_stated",
     "Maya hit a wall with Godot's TileMap layering for the lighthouse "
     "interior and spent a weekend in the Godot Discord getting help."),
    ("engine.hiring_interview", "2026-02-10", "fact", "user_stated",
     "Maya interviewed Priya Anand, a freelance Godot contractor, about "
     "taking on the save-system and settings-menu work for Lighthouse "
     "Keeper."),
    ("engine.hiring_decision", "2026-02-14", "decision", "user_stated",
     "Maya hired Priya Anand for ten hours a week of Godot contract work "
     "on Lighthouse Keeper's save system."),
    ("engine.progress1", "2026-02-20", "fact", "user_stated",
     "Priya Anand shipped the first version of Lighthouse Keeper's save "
     "system using Godot's built-in resource format."),
    ("engine.progress2", "2026-03-05", "fact", "user_stated",
     "Maya and Priya Anand paired for two hours to debug a save-"
     "corruption bug that only showed up after the third in-game day."),
    ("engine.godot_version", "2026-03-12", "fact", "user_stated",
     "Godot 4.3 shipped and Maya upgraded the Lighthouse Keeper project, "
     "fixing two small GDScript deprecation warnings."),
    ("engine.forum_thread", "2026-04-01", "fact", "user_stated",
     "Before deciding anything, Maya posted in an indie dev forum asking "
     "Godot and Unity users about console-porting problems, and most "
     "replies favored Unity for a first console release."),
    ("engine.unity_pricing_change", "2026-04-02", "fact", "user_stated",
     "Unity announced a new Indie Growth tier that waives the per-install "
     "runtime fee entirely for studios under two hundred thousand dollars "
     "in annual revenue."),
    ("engine.unity_eval1", "2026-04-05", "fact", "user_stated",
     "Maya re-evaluated Unity under the new Indie Growth tier and was "
     "impressed by how much larger the Unity Asset Store's dialogue-"
     "system plugin selection was."),
    ("engine.unity_eval2", "2026-04-09", "fact", "user_stated",
     "Maya worried that the console-porting story for her current engine "
     "was still thin for a solo developer without a publisher, while "
     "Unity had mature console export templates."),
    ("engine.decision.unity", "2026-04-14", "decision", "user_stated",
     "Maya decided to switch Lighthouse Keeper from Godot to Unity after "
     "Unity's Indie Growth pricing change removed the runtime fee and its "
     "asset store filled the dialogue-tooling gap."),
    ("engine.priya_reassign", "2026-04-16", "fact", "user_stated",
     "Maya and Priya Anand agreed to wind down the save-system contract, "
     "and Priya recommended a Unity-focused contractor instead."),
    ("engine.unity_migration1", "2026-04-22", "fact", "user_stated",
     "Maya spent a week porting Lighthouse Keeper's core movement and "
     "interaction scripts from GDScript to C sharp in Unity."),
    ("engine.unity_migration2", "2026-05-01", "fact", "user_stated",
     "Maya rebuilt the save system in Unity using JSON serialization, "
     "referencing Priya Anand's original design for the data schema."),
    ("engine.unity_progress", "2026-05-20", "fact", "user_stated",
     "Lighthouse Keeper's Unity build hit a stable vertical slice: three "
     "rooms, the save system, and the opening cutscene."),
    ("engine.retro", "2026-06-10", "idea",  "user_stated",
     "Maya wrote a devlog reflecting that starting in Godot was not "
     "wasted time -- the GDScript prototype clarified Lighthouse Keeper's "
     "core interaction loop before the Unity rewrite."),
    ("engine.cost_tracking", "2026-06-25", "fact", "user_stated",
     "Maya tallied total engine-related costs for Lighthouse Keeper and "
     "found the free early months meant the Unity switch still landed "
     "well under her original budget."),

    # -- freelance clients + THE TEMPORAL STORY (rate changes twice) ------ #
    ("freelance.fintrack_intro", "2026-01-06", "fact", "user_stated",
     "Maya took on a freelance motion-graphics contract with Fintrack, a "
     "fintech startup, to animate their onboarding screens."),
    ("freelance.fintrack_contact", "2026-01-06", "fact", "user_stated",
     "Daniel Ochoa, Fintrack's product lead, became Maya's main point of "
     "contact for the onboarding animation work."),
    ("freelance.rate.v1", "2026-01-06", "fact", "user_stated",
     "Maya set her freelance rate for Fintrack work at sixty-five dollars "
     "an hour -- the amount she charges clients for a full hour of work.",
     "2026-01-06", "2026-03-01"),
    ("freelance.fintrack_delivery1", "2026-01-20", "fact", "user_stated",
     "Maya delivered the first batch of onboarding animations to "
     "Fintrack, and Daniel Ochoa asked for a second round covering the "
     "settings flow."),
    ("freelance.bramble_intro", "2026-02-01", "fact", "user_stated",
     "Maya started an illustration and web contract with Bramble and Co, "
     "a small indie book publisher."),
    ("freelance.bramble_contact", "2026-02-01", "fact", "user_stated",
     "Wren Callahan, an editor at Bramble and Co, briefed Maya on cover-"
     "style illustrations for their spring catalog."),
    ("freelance.invoice_habit", "2026-02-25", "fact", "user_stated",
     "Maya switched to sending itemized invoices with per-project time "
     "logs after Fintrack's finance team asked for clearer breakdowns."),
    ("freelance.bramble_delivery1", "2026-02-18", "fact", "user_stated",
     "Maya finished three cover illustrations for Bramble and Co's spring "
     "catalog, and Wren Callahan asked her to redesign the site's book-"
     "listing pages."),
    ("freelance.rate.v2", "2026-03-01", "fact", "user_stated",
     "Maya raised the hourly rate she charges freelance clients to eighty-"
     "five dollars, starting with new Fintrack and Bramble and Co "
     "contracts.",
     "2026-03-01", "2026-05-01"),
    ("freelance.taxes", "2026-03-20", "fact", "user_stated",
     "Maya set aside thirty percent of freelance income into a separate "
     "savings account for quarterly taxes."),
    ("freelance.fintrack_delivery2", "2026-03-15", "fact", "user_stated",
     "Maya wrapped the Fintrack settings-flow animations at the new rate "
     "without any pushback from Daniel Ochoa."),
    ("freelance.bramble_referral", "2026-04-10", "fact", "user_stated",
     "Wren Callahan referred Maya to two other small publishers looking "
     "for cover illustration work."),
    ("freelance.bramble_web_delivery", "2026-04-25", "fact", "user_stated",
     "Maya shipped the redesigned book-listing pages for Bramble and Co, "
     "and Wren Callahan said site traffic from the new pages looked "
     "promising."),
    ("freelance.rate.v3", "2026-05-01", "fact", "user_stated",
     "Maya raised the rate she charges freelance clients again, to a "
     "hundred dollars an hour, after Wren Callahan's referrals filled her "
     "calendar through the summer.",
     "2026-05-01", None),
    ("freelance.new_client1", "2026-05-08", "fact", "user_stated",
     "Maya started a short illustration contract with one of Wren "
     "Callahan's referrals, a poetry press called Marrow Editions."),
    ("freelance.balance_note", "2026-05-15", "idea", "user_stated",
     "Maya started blocking freelance work to three afternoons a week so "
     "Lighthouse Keeper's Unity migration would not stall."),
    ("freelance.marrow_delivery", "2026-06-01", "fact", "user_stated",
     "Maya delivered the first round of cover illustrations for Marrow "
     "Editions, the poetry press Wren Callahan referred her to."),
    ("freelance.fintrack_wrap", "2026-06-05", "fact", "user_stated",
     "Maya's Fintrack contract with Daniel Ochoa wrapped up after a final "
     "round of animations for their mobile app launch."),
    ("freelance.fintrack_wrap_note", "2026-06-08", "fact", "user_stated",
     "Daniel Ochoa asked Maya to stay on Fintrack's referral list for "
     "future contract work even after the main engagement wrapped."),
    ("freelance.contract_template", "2026-01-25", "idea", "user_stated",
     "Maya wrote a reusable freelance contract template after the "
     "Fintrack negotiation took longer than expected."),
    ("freelance.calendar_review", "2026-06-20", "idea", "user_stated",
     "Maya plans to block one full freelance-free week each quarter once "
     "Lighthouse Keeper nears launch, to protect crunch time."),

    # -- game design decisions (Lighthouse Keeper) ------------------------ #
    ("design.premise", "2026-01-05", "idea", "user_stated",
     "Maya's core idea for Lighthouse Keeper is a solitary lighthouse "
     "keeper solving the mystery of a ship that vanished decades ago, "
     "told through found letters and logbook entries."),
    ("design.core_loop", "2026-01-12", "decision", "user_stated",
     "Maya settled on a core loop of exploring the lighthouse and "
     "coastline by day and tending the light and reading recovered pages "
     "by night."),
    ("design.puzzle_style", "2026-01-22", "decision", "user_stated",
     "Maya decided Lighthouse Keeper's puzzles would be environmental and "
     "narrative, avoiding inventory-combination puzzles she found "
     "frustrating in other games."),
    ("design.difficulty", "2026-02-05", "decision", "user_stated",
     "Maya decided against a fail state entirely -- Lighthouse Keeper's "
     "puzzles can always be solved with patience, since the game is about "
     "atmosphere, not challenge."),
    ("design.journal_idea", "2026-02-12", "idea", "user_stated",
     "Maya's idea: a logbook mechanic where the player physically writes "
     "short notes that persist and get referenced by other characters "
     "later in the story."),
    ("design.map_scope", "2026-02-22", "fact", "user_stated",
     "Maya scoped Lighthouse Keeper's map to five explorable areas: the "
     "lighthouse tower, the keeper's cottage, the tide pools, the wrecked "
     "pier, and the cliffside cave."),
    ("design.npc_letters", "2026-03-02", "fact", "user_stated",
     "Maya wrote the first draft of the letters found in the wrecked "
     "pier area, establishing the vanished ship's captain as a central "
     "mystery figure."),
    ("design.playtest1", "2026-03-18", "fact", "user_stated",
     "Maya ran a playtest with two friends and found the tide-pools "
     "puzzle was too obscure without a hint the game never gave."),
    ("design.hint_system", "2026-03-22", "decision", "user_stated",
     "Maya added an optional hint system triggered by re-reading the "
     "logbook, so players who want hints can ask for them without a "
     "difficulty toggle."),
    ("design.act_structure", "2026-04-01", "decision", "user_stated",
     "Maya restructured Lighthouse Keeper into three acts tied to the "
     "tide, so certain areas are only reachable at low tide, pacing the "
     "mystery's reveals."),
    ("design.color_tide_link", "2026-04-08", "fact", "user_stated",
     "Maya linked the tide-based act structure directly to Sofia Reyes's "
     "color script so lighting and pacing reinforce each other."),
    ("design.ending_idea", "2026-04-18", "idea", "user_stated",
     "Maya's idea: an ending where the player chooses whether to keep "
     "the vanished captain's story secret or send it to his descendants, "
     "changing the final logbook entry."),
    ("design.letters_rewrite", "2026-05-05", "fact", "user_stated",
     "Maya rewrote the captain's letters after the second playtest "
     "showed players missed a key clue about the ship's fate."),
    ("design.playtest2", "2026-05-10", "fact", "user_stated",
     "A second playtest after the Unity migration confirmed the tide-"
     "based act structure read clearly to new players."),
    ("design.sound_design", "2026-05-18", "fact", "user_stated",
     "Maya brought on an audio contractor for ambient lighthouse and "
     "coastline sound design to strengthen the atmosphere the puzzles "
     "depend on."),
    ("design.accessibility", "2026-05-30", "decision", "user_stated",
     "Maya added a text-size option and full subtitle support after a "
     "playtester with low vision struggled to read the logbook font."),
    ("design.length_target", "2026-05-25", "fact", "user_stated",
     "Maya settled on a target playtime of three to four hours for "
     "Lighthouse Keeper, matching other narrative games she admires."),
    ("design.title_screen", "2026-06-01", "fact", "user_stated",
     "Maya finalized Lighthouse Keeper's title screen, using a slow pan "
     "across the lighthouse at dusk instead of a static logo."),
    ("design.demo_scope", "2026-06-15", "decision", "user_stated",
     "Maya decided the public demo would cover only the first act, "
     "ending right before the tide-pools reveal, to avoid spoiling the "
     "mystery."),

    # -- art style (pixel -> painterly contradiction/supersede pivot) ----- #
    ("art.style_pixel", "2026-01-16", "decision", "user_stated",
     "Maya initially decided on a pixel-art style for Lighthouse Keeper "
     "to keep the scope manageable for a solo artist."),
    ("art.pixel_test", "2026-01-28", "fact", "user_stated",
     "Maya mocked up the lighthouse tower interior in sixteen-by-sixteen "
     "pixel art and felt it undersold the moody coastal atmosphere she "
     "wanted."),
    ("art.painterly_test", "2026-02-08", "fact", "user_stated",
     "Maya experimented with painterly gouache-style backgrounds for the "
     "same lighthouse interior scene and felt it captured the mood far "
     "better than pixel art."),
    ("art.style_painterly", "2026-02-15", "decision", "user_stated",
     "Maya decided to switch Lighthouse Keeper's background art to a "
     "painterly gouache style, keeping simple flat-shaded character "
     "sprites for readability."),
    ("art.color_theory", "2026-02-25", "fact", "user_stated",
     "Maya read a chapter on gouache color theory that shaped the style-"
     "guide discussion she later had with Sofia Reyes."),
    ("art.illustrator_search", "2026-02-20", "fact", "user_stated",
     "Maya started looking for a background illustrator who could match "
     "the painterly gouache style for the game's five areas."),
    ("art.hire_sofia", "2026-03-01", "decision", "user_stated",
     "Maya hired Sofia Reyes, a freelance illustrator, to paint "
     "Lighthouse Keeper's background art in the gouache style."),
    ("art.color_script", "2026-03-10", "fact", "user_stated",
     "Sofia Reyes delivered a color script mapping each area's palette "
     "to the story's tide-based act structure."),
    ("art.first_bg", "2026-03-25", "fact", "user_stated",
     "Sofia Reyes finished the first background painting, the lighthouse "
     "tower interior, and Maya integrated it into the game build."),
    ("art.character_sprites", "2026-04-05", "fact", "user_stated",
     "Maya finalized the keeper character's flat-shaded sprite sheet, "
     "keeping the character simple against Sofia Reyes's detailed "
     "backgrounds."),
    ("art.palette_lock", "2026-04-15", "fact", "user_stated",
     "Maya and Sofia Reyes locked the final gouache palette so no more "
     "area backgrounds would drift in tone after the engine migration."),
    ("art.bg_migration", "2026-04-28", "fact", "user_stated",
     "Maya re-imported Sofia Reyes's background paintings into the new "
     "Unity project during the engine migration without any quality "
     "loss."),
    ("art.ui_style", "2026-05-08", "fact", "user_stated",
     "Maya designed Lighthouse Keeper's UI icons to match Sofia Reyes's "
     "painterly style instead of a flat modern icon set."),
    ("art.lighting_pass", "2026-05-12", "fact", "user_stated",
     "Maya added a dynamic lighting pass in Unity so the lighthouse beam "
     "sweeps across Sofia Reyes's painted backgrounds at night."),
    ("art.remaining_bgs", "2026-05-28", "fact", "user_stated",
     "Sofia Reyes delivered the tide-pools and cliffside-cave "
     "backgrounds, leaving only the wrecked pier scene outstanding."),
    ("art.style_guide", "2026-06-08", "idea", "user_stated",
     "Maya and Sofia Reyes wrote a short style guide documenting the "
     "gouache palette rules so future contract artists can match the "
     "look."),
    ("art.final_bg", "2026-06-20", "fact", "user_stated",
     "Sofia Reyes finished the wrecked pier background, completing art "
     "for all five of Lighthouse Keeper's areas."),

    # -- health / habits (sleep-time contradiction/supersede pivot) ------- #
    ("health.sleep_6am", "2026-01-08", "decision", "user_stated",
     "Maya committed to a six a.m. wake time to get focused Lighthouse "
     "Keeper work done before freelance calls started."),
    ("health.standing_desk", "2026-01-10", "fact", "user_stated",
     "Maya switched to a standing desk after long development sessions "
     "left her lower back sore."),
    ("health.running", "2026-01-14", "fact", "user_stated",
     "Maya started running three times a week along the coastline, "
     "partly for research on Lighthouse Keeper's setting."),
    ("health.wrist_pain", "2026-01-30", "fact", "user_stated",
     "Maya started noticing wrist pain from long typing sessions and "
     "looked into ergonomic keyboards."),
    ("health.caffeine", "2026-02-10", "fact", "user_stated",
     "Maya cut caffeine after two p.m. after noticing it was wrecking "
     "her wake-up schedule."),
    ("health.ergo_keyboard", "2026-02-05", "fact", "user_stated",
     "Maya bought a split ergonomic keyboard and the wrist pain eased "
     "within two weeks."),
    ("health.sleep_struggle", "2026-02-20", "fact", "user_stated",
     "Maya found the six a.m. wake time was not sticking, especially "
     "after late-night debugging sessions."),
    ("health.sleep_8am", "2026-03-01", "decision", "user_stated",
     "Maya settled on an eight a.m. wake time instead, which fit her "
     "natural rhythm and late-night debugging sessions better."),
    ("health.screen_breaks", "2026-03-05", "fact", "user_stated",
     "Maya started a twenty-minute walk break every two hours after a "
     "full day of engine-migration work left her eyes strained."),
    ("health.running_streak", "2026-03-15", "fact", "user_stated",
     "Maya kept up the running habit for two months and noticed the "
     "coastline runs were feeding directly into her descriptions of the "
     "tide-pools area."),
    ("health.meal_prep", "2026-04-01", "fact", "user_stated",
     "Maya began batch-cooking on Sundays to avoid skipping lunch during "
     "crunch weeks on the Unity migration."),
    ("health.posture_reminder", "2026-04-20", "fact", "user_stated",
     "Maya set an hourly posture reminder after a long crunch week "
     "during the Unity migration."),
    ("health.wrist_recheck", "2026-05-10", "fact", "user_stated",
     "Maya's wrist pain stayed manageable through the Unity migration "
     "thanks to the ergonomic keyboard and regular screen breaks."),
    ("health.social_time", "2026-05-18", "fact", "user_stated",
     "Maya started scheduling one no-work evening a week with friends "
     "after realizing freelance work and Lighthouse Keeper had eaten "
     "every evening in March."),
    ("health.running_race", "2026-06-01", "fact", "user_stated",
     "Maya ran a local ten-kilometer coastal race, her longest run since "
     "starting Lighthouse Keeper."),

    # -- reading list / research ------------------------------------------ #
    ("reading.art_of_game_design", "2026-01-11", "fact", "user_stated",
     "Maya started reading The Art of Game Design by Jesse Schell for "
     "help with Lighthouse Keeper's puzzle design."),
    ("reading.godot_docs", "2026-01-20", "fact", "user_stated",
     "Maya bookmarked the Godot documentation's TileMap and Resource "
     "sections as ongoing references during development."),
    ("reading.lens_100", "2026-01-24", "idea", "user_stated",
     "Maya's idea: apply Jesse Schell's 'lens of the problem' to the "
     "tide-pools puzzle once playtesting reveals whether it works."),
    ("reading.braid_postmortem", "2026-02-03", "fact", "user_stated",
     "Maya read a postmortem on Braid's puzzle design and took notes on "
     "how its mechanics were introduced without any tutorial text."),
    ("reading.narrative_newsletter", "2026-02-15", "fact", "user_stated",
     "Maya subscribed to a narrative-design newsletter that regularly "
     "covers environmental storytelling techniques."),
    ("reading.gone_home_analysis", "2026-02-27", "fact", "user_stated",
     "Maya read an analysis of Gone Home's use of found documents, "
     "directly relevant to Lighthouse Keeper's letter-based mystery."),
    ("reading.what_remains", "2026-03-08", "fact", "user_stated",
     "Maya played through What Remains of Edith Finch as research for "
     "pacing a slow, letter-driven mystery."),
    ("reading.idea_journal_mechanic", "2026-03-14", "idea", "user_stated",
     "Maya's idea: borrow Edith Finch's trick of changing controls per "
     "memory sequence for a few key logbook flashback scenes in "
     "Lighthouse Keeper."),
    ("reading.unity_docs", "2026-04-06", "fact", "user_stated",
     "Maya started working through Unity's official C sharp scripting "
     "documentation ahead of the engine migration."),
    ("reading.firewatch_analysis", "2026-04-14", "fact", "user_stated",
     "Maya read an analysis of Firewatch's isolated-setting pacing, "
     "directly relevant to the lighthouse's solitary tone."),
    ("reading.sound_design_book", "2026-05-02", "fact", "user_stated",
     "Maya read a chapter on ambient sound design in games, informing "
     "the brief she gave her audio contractor."),
    ("reading.marketing_book", "2026-05-20", "fact", "user_stated",
     "Maya started reading a book on marketing for solo indie "
     "developers, focused on wishlist growth before launch."),
    ("reading.solo_dev_podcast", "2026-06-12", "fact", "user_stated",
     "Maya started listening to a solo-indie-dev podcast episode about "
     "pricing a first commercial release."),
    ("reading.idea_museum_ending", "2026-06-05", "idea", "user_stated",
     "Maya's idea: a post-credits scene showing the recovered letters "
     "displayed in a small coastal museum, tying the ending choice to a "
     "visible consequence."),

    # -- community / marketing --------------------------------------------- #
    ("marketing.devlog1", "2026-02-01", "fact", "user_stated",
     "Maya published the first Lighthouse Keeper devlog post, "
     "introducing the premise and the early prototype."),
    ("marketing.steam_page", "2026-03-01", "fact", "user_stated",
     "Maya set up Lighthouse Keeper's Steam page with early screenshots "
     "from the prototype build."),
    ("marketing.wishlist_100", "2026-03-20", "fact", "user_stated",
     "Lighthouse Keeper's Steam wishlist crossed one hundred adds after "
     "the devlog post was shared on a narrative-games forum."),
    ("marketing.discord", "2026-04-25", "fact", "user_stated",
     "Maya opened a small Discord server for Lighthouse Keeper and "
     "answered questions directly from the first wishlisters."),
    ("marketing.devlog2", "2026-04-20", "fact", "user_stated",
     "Maya published a devlog explaining the switch from Godot to "
     "Unity, which drew more comments than any previous post."),
    ("marketing.screenshot_update", "2026-05-15", "fact", "user_stated",
     "Maya updated Lighthouse Keeper's Steam page screenshots to show "
     "Sofia Reyes's finished painterly backgrounds."),
    ("marketing.press_kit", "2026-05-25", "fact", "user_stated",
     "Maya assembled a press kit with Sofia Reyes's background art and a "
     "short trailer cut from the Unity vertical slice."),
    ("marketing.wishlist_500", "2026-06-01", "fact", "user_stated",
     "Lighthouse Keeper's Steam wishlist crossed five hundred adds after "
     "the new screenshots went up."),
    ("marketing.trailer_feedback", "2026-06-05", "fact", "user_stated",
     "Maya shared the Unity vertical-slice trailer with a small feedback "
     "Discord and revised the pacing of the opening shot."),
    ("marketing.festival_app", "2026-06-10", "fact", "user_stated",
     "Maya applied to a small narrative-games showcase festival for a "
     "demo slot in the fall."),
    ("marketing.demo_plan", "2026-06-18", "decision", "user_stated",
     "Maya decided to launch the public demo alongside a wishlist-"
     "focused devlog post covering only the first act."),
    ("marketing.newsletter_signup", "2026-06-22", "fact", "user_stated",
     "Maya added an email newsletter signup to the Steam page ahead of "
     "the demo launch."),

    # -- quarantine exhibit (imported channel, tampered post-write) ------- #
    ("quarantine.bookmark", "2026-04-12", "fact", "imported",
     "Maya bookmarked a Kickstarter campaign page for a competing "
     "lighthouse-themed adventure game to keep an eye on the market."),
]
# fmt: on

# -- deliberate supersede chains: (loser_tag, winner_tag, reason) --------- #
SUPERSEDE_CHAINS = [
    ("engine.decision.godot", "engine.decision.unity", "engine_switch"),
    ("freelance.rate.v1", "freelance.rate.v2", "rate_change"),
    ("freelance.rate.v2", "freelance.rate.v3", "rate_change"),
    ("art.style_pixel", "art.style_painterly", "art_style_pivot"),
    ("health.sleep_6am", "health.sleep_8am", "habit_pivot"),
]

# -- topic notes: slug -> (title_seed, [member tags]) --------------------- #
TOPICS = {
    "engine-choice-and-tooling": [
        "engine.pricing_research1", "engine.pricing_research2",
        "engine.decision.godot", "engine.tutorial1", "engine.tutorial2",
        "engine.tutorial3", "engine.hiring_interview", "engine.hiring_decision",
        "engine.progress1", "engine.progress2", "engine.godot_version",
        "engine.forum_thread", "engine.unity_pricing_change",
        "engine.unity_eval1", "engine.unity_eval2", "engine.decision.unity",
        "engine.priya_reassign", "engine.unity_migration1",
        "engine.unity_migration2", "engine.unity_progress", "engine.retro",
        "engine.cost_tracking",
    ],
    "freelance-clients-and-rates": [
        "freelance.fintrack_intro", "freelance.fintrack_contact",
        "freelance.rate.v1", "freelance.fintrack_delivery1",
        "freelance.bramble_intro", "freelance.bramble_contact",
        "freelance.invoice_habit", "freelance.bramble_delivery1",
        "freelance.rate.v2", "freelance.taxes", "freelance.fintrack_delivery2",
        "freelance.bramble_referral", "freelance.bramble_web_delivery",
        "freelance.rate.v3", "freelance.new_client1", "freelance.balance_note",
        "freelance.marrow_delivery", "freelance.fintrack_wrap",
        "freelance.fintrack_wrap_note", "freelance.contract_template",
        "freelance.calendar_review",
    ],
    "game-design-lighthouse-keeper": [
        "design.premise", "design.core_loop", "design.puzzle_style",
        "design.difficulty", "design.journal_idea", "design.map_scope",
        "design.npc_letters", "design.playtest1", "design.hint_system",
        "design.act_structure", "design.color_tide_link", "design.ending_idea",
        "design.letters_rewrite", "design.playtest2", "design.sound_design",
        "design.accessibility", "design.length_target", "design.title_screen",
        "design.demo_scope",
    ],
    "art-style-and-illustration": [
        "art.style_pixel", "art.pixel_test", "art.painterly_test",
        "art.style_painterly", "art.color_theory", "art.illustrator_search",
        "art.hire_sofia", "art.color_script", "art.first_bg",
        "art.character_sprites", "art.palette_lock", "art.bg_migration",
        "art.ui_style", "art.lighting_pass", "art.remaining_bgs",
        "art.style_guide", "art.final_bg",
    ],
    "health-and-habits": [
        "health.sleep_6am", "health.standing_desk", "health.running",
        "health.wrist_pain", "health.caffeine", "health.ergo_keyboard",
        "health.sleep_struggle", "health.sleep_8am", "health.screen_breaks",
        "health.running_streak", "health.meal_prep", "health.posture_reminder",
        "health.wrist_recheck", "health.social_time", "health.running_race",
    ],
    "reading-and-research": [
        "reading.art_of_game_design", "reading.godot_docs",
        "reading.lens_100", "reading.braid_postmortem",
        "reading.narrative_newsletter", "reading.gone_home_analysis",
        "reading.what_remains", "reading.idea_journal_mechanic",
        "reading.unity_docs", "reading.firewatch_analysis",
        "reading.sound_design_book", "reading.marketing_book",
        "reading.solo_dev_podcast", "reading.idea_museum_ending",
    ],
    "community-and-marketing": [
        "marketing.devlog1", "marketing.steam_page", "marketing.wishlist_100",
        "marketing.discord", "marketing.devlog2", "marketing.screenshot_update",
        "marketing.press_kit", "marketing.wishlist_500",
        "marketing.trailer_feedback", "marketing.festival_app",
        "marketing.demo_plan", "marketing.newsletter_signup",
    ],
    # The second citation for the ripple showcase: a cross-cutting "key
    # decisions" retrospective note that legitimately includes the engine
    # decision alongside every other pivotal call Maya made -- this is what
    # gives ripple(engine.decision.godot) a second topic-tier edge (see
    # WORKLOG.md assumption 3: auto-clustering can never do this, since one
    # episode can only land in one disjoint cluster).
    "key-project-decisions": [
        "engine.decision.godot", "engine.decision.unity",
        "engine.hiring_decision", "art.style_pixel", "art.style_painterly",
        "health.sleep_6am", "health.sleep_8am", "freelance.rate.v2",
        "freelance.rate.v3", "design.hint_system", "design.demo_scope",
        "marketing.demo_plan",
    ],
}

# The three planned demo questions (memsub.sdk.MemoryClient.search sanity
# check) and the ripple showcase memory tag.
DEMO_QUESTIONS = [
    "what engine is Maya using and why",
    "what does Maya charge",
    "what has Maya decided about the art style",
]
RIPPLE_TAG = "engine.decision.godot"
