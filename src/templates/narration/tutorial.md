# Chapter Narration Script — Tutorial Mode

Generate instructional voice-over narration for a tutorial video chapter.

## Chapter Details
- **Title:** {{ chapter_title }}
- **Subtitle:** {{ chapter_subtitle }}
- **Pages covered:** {{ chapter_pages }}
- **Narrative arc:** {{ narrative_arc }}
- **Key moments:** {{ key_moments }}

## Tone
{{ tone }}

## Narrative Hooks Available
{{ hooks }}

## Feature Highlights
{{ feature_highlights }}

## Pacing Guidance

This chapter covers approximately {{ estimated_duration_seconds }} seconds of video. At ~150 words/minute, aim for roughly **{{ word_target }} words** of narration.

- Short chapters (under 30s): 1-2 clear instructions
- Medium chapters (30-60s): 2-4 step-by-step instructions with context
- Long chapters (over 60s): 3-5 instructional sentences with explanations of why each step matters

## Instructions

Write narration text that:
1. Tells the viewer exactly what to do — use second-person ("Click the gear icon to open Settings")
2. Explains the purpose of each action — not just "click here" but why they should
3. Describes what the viewer will see happen after each action
4. Uses specific UI labels: button names, menu items, field labels
5. Matches the specified tone — instructional and supportive
6. Builds confidence — acknowledge when something might be confusing and reassure
7. Avoids repeating the same transition phrases across chapters

## Example Narrations

**Instructional tone (for a SaaS dashboard):**
"To check your analytics, click the Dashboard tab in the left sidebar. You'll see your key metrics front and center — daily active users, conversion rate, and revenue. Use the date picker in the top-right to change the time range. Try selecting 'Last 30 days' to see your monthly trends."

**Step-by-step tone (for an e-commerce site):**
"Finding the right product is easy. Type what you're looking for in the search bar at the top, or use the category menu on the left to browse. Once you find something you like, click on it to see the full details — pricing, availability, and customer reviews are all right there."

**Supportive tone (for a complex tool):**
"Don't worry if this looks like a lot of options — you only need a few to get started. The most important setting is your notification preferences, right here under the bell icon. Toggle on the alerts you want, and you can always come back to fine-tune these later."

## Anti-Patterns — Do NOT:
- Start with "Welcome to Chapter X" or "In this chapter" — jump straight into the instruction
- Use phrases like "As you can see", "As shown here", "Here you see" — describe the action, not the visual
- Reference "this tutorial" or "this video" — the narration should feel like a personal walkthrough
- Begin every sentence with the same structure — vary between "Click...", "Next...", "You'll notice..."
- Use vague directions ("go to settings") without specifying where to find the control
- Repeat phrases from previous chapters — each chapter must use fresh language
- Use empty superlatives: "amazing", "incredible", "powerful", "revolutionary", "game-changing"
- Skip the "why" — always explain what a feature does, not just where it is

## Transition Guidance
- **First chapter:** Orient the user — "Let's start by getting familiar with the layout"
- **Middle chapters:** Can open with task-oriented transitions like "Now let's..." or "Next, we'll learn how to..."
- **Last chapter:** Can hint at independence — "Now that you know the basics..." but don't add a formal sign-off (the outro handles that)

Output ONLY the narration text — no JSON, no markdown formatting, just the spoken words.
Do NOT include any preamble such as "Here is the narration:" or "Narration text:" — start directly with the spoken words. The output will be sent directly to a TTS engine.
