# Chapter Narration Script — Technical Deep Dive

Generate voice-over narration for a video chapter that weaves code quality insights into a product demo. This template combines product walkthrough narration with technical credibility signals drawn from static analysis of the codebase.

## Chapter Details
- **Title:** {{ chapter_title }}
- **Subtitle:** {{ chapter_subtitle }}
- **Pages covered:** {{ chapter_pages }}
- **Narrative arc:** {{ narrative_arc }}
- **Key moments:** {{ key_moments }}

## Tone
{{ tone }}

## Code Quality Context
{{ code_quality_summary }}

- **Primary language:** {{ primary_language }}
- **Codebase size:** {{ loc }} lines of code
- **Maintainability score:** {{ maintainability_score }}/100

### Top Issues
{{ top_issues_summary }}

## Narrative Hooks Available
{{ hooks }}

## Feature Highlights
{{ feature_highlights }}

## Pacing Guidance

This chapter covers approximately {{ estimated_duration_seconds }} seconds of video. At ~150 words/minute, aim for roughly **{{ word_target }} words** of narration.

- Short chapters (under 30s): 1-2 punchy sentences
- Medium chapters (30-60s): 2-4 sentences with natural pacing
- Long chapters (over 60s): 3-5 sentences, allow breathing room between ideas

## Technical + Product Focus

This narration combines product demo with technical credibility. Your narration should:
- **Lead with product value, then reinforce with technical evidence**: "Teams ship faster with this workflow — and the codebase behind it is built for scale"
- **Weave in code quality naturally**: Instead of rattling off numbers, treat metrics as supporting evidence — "Built on a well-maintained codebase..." or "Backed by a thoroughly tested foundation..."
- **Use metrics as credibility signals, not dry statistics**: A maintainability score of 85 becomes "a codebase designed for long-term evolution" — never read raw numbers aloud
- **Connect architecture to user experience**: Good maintainability means fast iteration means better features for users. Draw the line from code to product.
- **Mention tech debt honestly if significant**: "The team has identified areas for improvement and is actively addressing them..." — authenticity builds trust
- **Keep technical references brief**: One sentence per chapter is plenty — the visuals are showing the product, not the code

## Instructions

Write narration text that:
1. Introduces what the viewer is about to see in this chapter
2. Anchors features in business outcomes — don't just name features, explain what they enable
3. Uses specific language over vague praise: "supports 12 languages" beats "powerful translation"
4. Weaves in narrative hooks where they fit organically
5. Matches the specified tone consistently
6. Feels like a product walkthrough by someone who understands both the product and how it's built
7. Avoids repeating the same phrases or metaphors used in other chapters
8. Integrates at most one technical credibility reference per chapter — do not overload with metrics

## Example Narrations

**Professional tone (for a SaaS dashboard with strong code quality):**
"The analytics dashboard surfaces your key metrics in real time — no page reload needed. Under the hood, this is powered by a codebase scoring well above industry benchmarks for maintainability, which means new chart types and data sources ship fast."

**Conversational tone (for an e-commerce site with good test coverage):**
"Here's the product catalog — filter by size, color, and price all at once and watch results update live. The engineering team has invested heavily in test coverage, so features like this ship confidently without breaking existing functionality."

**Technical tone (for a developer tool):**
"The API explorer lets you test endpoints directly in the browser, with live request/response previews. The codebase follows clean architecture patterns that keep complexity low even as the API surface grows."

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
- Read raw metrics aloud: "85 out of 100 maintainability" sounds robotic — paraphrase instead
- Dedicate more than one sentence per chapter to technical aspects — the product is the star

## Transition Guidance
- **First chapter:** Establish context — what is this product and why should viewers trust both the product and the team behind it?
- **Middle chapters:** Can open with brief transitions like "Moving on to..." or "Next up..." but keep them short
- **Last chapter:** Can hint at a conclusion but don't add a formal sign-off (the outro handles that)

Output ONLY the narration text — no JSON, no markdown formatting, just the spoken words.
Do NOT include any preamble such as "Here is the narration:" or "Narration text:" — start directly with the spoken words. The output will be sent directly to a TTS engine.
