# Part Narration Script

Generate voice-over narration for a single video segment. The narration must describe exactly what the viewer is watching in this segment.

## Part Details
- **Title:** {{ part_title }}
- **Actions shown:** {{ part_actions }}
- **Pages visible:** {{ part_pages }}

## Chapter Context
- **Chapter:** {{ chapter_title }}
- **Narrative arc:** {{ narrative_arc }}

## Style
- **Tone:** {{ tone }}
- **Mode:** {{ mode }}

## Pacing

This segment lasts approximately {{ estimated_duration_seconds }} seconds. At ~150 words/minute, aim for roughly **{{ word_target }} words**.

The narration should fill the segment naturally — neither rushing nor leaving long silences.

## Position in Chapter
{{ position_in_chapter }}

## Previous Narration
{{ previous_narration }}

## Instructions

Write narration that:
1. Describes what the viewer is seeing and doing in THIS specific segment
2. Matches the visual content — if the segment shows a pricing page, talk about pricing
3. Flows naturally from the previous segment's narration (if any)
4. Uses the appropriate transition for this segment's position in the chapter
5. Stays within the word target — the audio will be synced to this specific video segment

## Position-Specific Guidance
- **first**: Briefly set context for what this chapter covers before describing the visuals
- **middle**: Continue the flow — reference what was just shown and bridge to current content
- **last**: Wrap up the chapter's theme while describing the final visuals
- **only**: Combine context-setting with visual description in a self-contained narration

## Anti-Patterns — Do NOT:
- Describe actions the viewer hasn't seen yet in this segment
- Reference the previous segment by name ("as we just saw...", "as we saw earlier") — let the flow be implicit by picking up naturally from where the last segment ended without calling attention to the transition
- Use generic filler that could apply to any page
- Start with "In this section" or "Now we see"

Output ONLY the narration text — no JSON, no markdown formatting, just the spoken words.
Do NOT include any preamble such as "Here is the narration:" — start directly with the spoken words.
