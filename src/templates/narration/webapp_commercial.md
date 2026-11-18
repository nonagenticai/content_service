# Chapter Narration Script — Web App Commercial

Generate natural voice-over narration for a video chapter showcasing a web application or SaaS product.

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

- Short chapters (under 30s): 1-2 punchy sentences
- Medium chapters (30-60s): 2-4 sentences with natural pacing
- Long chapters (over 60s): 3-5 sentences, allow breathing room between ideas

## Web App / SaaS Focus

This is a web application or SaaS product demo. Your narration should:
- **Lead with outcomes, not features**: "Teams ship 40% faster" beats "Includes a Kanban board"
- **Frame the UI as a workflow**: Describe what users accomplish, not just what buttons exist
- **Use demo-friendly language**: "Watch how..." / "In just a few clicks..." / "Notice how the dashboard updates..."
- **Emphasize interactivity**: Call out real-time updates, drag-and-drop, live previews, and instant feedback
- **Connect to onboarding**: Frame early chapters as the "getting started" experience a new user would have

## Instructions

Write narration text that:
1. Introduces what the viewer is about to see in this chapter
2. Anchors features in business outcomes — don't just name features, explain what they enable
3. Uses specific language over vague praise: "supports 12 languages" beats "powerful translation"
4. Weaves in narrative hooks where they fit organically
5. Matches the specified tone consistently
6. Feels like a product walkthrough by someone who uses the tool daily
7. Avoids repeating the same phrases or metaphors used in other chapters

## Example Narrations

**Professional tone (for a SaaS dashboard):**
"The analytics dashboard gives you a real-time view of your key metrics. Notice how the charts update instantly as we filter by date range — no page reload needed. The export options down here make it easy to share these insights with your team."

**Conversational tone (for an e-commerce site):**
"Here's where things get interesting — the product catalog. You can filter by size, color, and price all at once, and watch the results update live. Hover over any item to see a quick preview without leaving the page."

**SaaS onboarding tone (for a project management tool):**
"Getting started takes under a minute. The guided setup walks you through creating your first project, inviting team members, and configuring your workflow. By the time you finish, your board is ready with templates tailored to your industry."

## Anti-Patterns — Do NOT:
- Start with "Welcome to Chapter X" or "In this chapter" — jump straight into the content
- Use phrases like "As you can see", "As shown here", "Here you see", "Here, you'll see", or "Here you'll notice" — the viewer is watching, not reading
- Reference "this video" or "this demo" — the narration should feel like a natural walkthrough
- Begin every sentence with the same structure — vary your sentence openings
- Describe UI mechanics ("click the button", "scroll down") — that's what the video shows
- Repeat phrases from previous chapters — each chapter must use fresh language and new angles
- Use empty superlatives: "amazing", "incredible", "powerful", "revolutionary", "game-changing"
- List features without explaining their value — always connect features to outcomes
- Use the word "solution" more than once in the entire narration

## Transition Guidance
- **First chapter:** Establish context — what is this product/site and why does it matter?
- **Middle chapters:** Can open with brief transitions like "Moving on to..." or "Next up..." but keep them short
- **Last chapter:** Can hint at a conclusion but don't add a formal sign-off (the outro handles that)

Output ONLY the narration text — no JSON, no markdown formatting, just the spoken words.
Do NOT include any preamble such as "Here is the narration:" or "Narration text:" — start directly with the spoken words. The output will be sent directly to a TTS engine.
