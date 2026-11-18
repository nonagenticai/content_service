# Critique Video Storyline

Build a video storyline from critique findings, organizing them into chapters by category.

## Site Context
- **Site:** {{ site_name }}
- **URL:** {{ url }}
- **Categories scanned:** {{ categories }}
- **Total findings:** {{ total_findings }}

## Category Scores
{{ scores }}

## Storyline Requirements

1. **Intro**: "{site_name} — Growth Opportunities Report" with an encouraging subtitle
2. **One chapter per category** with findings, ordered by score (lowest = biggest opportunity first)
3. **Positive chapter titles**: "Accessibility Opportunities", "SEO Growth Areas", etc.
4. **Each chapter's narrative_arc** summarizes the top recommendations
5. **Outro**: "Your Roadmap to Excellence" with an actionable call to action

## Tone

Constructive consultant — frame everything as an opportunity to excel. Every finding should feel like expert advice from a supportive partner, not a list of problems.

## Duration Target

Target: {{ target_duration_minutes }} minutes total.
Allocate time proportionally to the number of findings per category.

## Output Format

CRITICAL: Your ENTIRE response must be a single valid JSON object. No markdown, no explanations, no text before or after the JSON. Do not wrap the JSON in ```json``` code fences. Start with { and end with }.

Your response MUST match this exact schema:

{
  "video_title": "string — video title",
  "video_description": "string — one-sentence description",
  "target_duration_minutes": number,
  "tone": "string — tone description",
  "intro": {
    "title": "string — intro card title",
    "subtitle": "string — intro card subtitle",
    "duration_seconds": number,
    "narrative_text": "string — what the intro conveys"
  },
  "chapters": [
    {
      "number": number,
      "title": "string — chapter title",
      "subtitle": "string — brief description",
      "pages": ["string — page IDs like page-001"],
      "narrative_arc": "string — what happens in this chapter",
      "key_moments": ["string", "string"],
      "estimated_duration_seconds": number
    }
  ],
  "outro": {
    "title": "string — closing card title",
    "subtitle": "string — closing card subtitle",
    "duration_seconds": number,
    "narrative_text": "string — what the outro conveys"
  }
}

EXAMPLE (minimal valid response):

{
  "video_title": "example.com — Growth Opportunities Report",
  "video_description": "A constructive review of growth areas across SEO, accessibility, performance, and security",
  "target_duration_minutes": 2,
  "tone": "constructive consultant",
  "intro": {
    "title": "example.com — Growth Opportunities",
    "subtitle": "Expert recommendations for your next steps",
    "duration_seconds": 8,
    "narrative_text": "Opening with the site's overall health and areas of opportunity"
  },
  "chapters": [
    {
      "number": 1,
      "title": "Accessibility Opportunities",
      "subtitle": "Making the site work for everyone",
      "pages": ["page-001"],
      "narrative_arc": "Walk through the top accessibility recommendations and their impact",
      "key_moments": ["ARIA landmarks", "Form label associations"],
      "estimated_duration_seconds": 60
    }
  ],
  "outro": {
    "title": "Your Roadmap to Excellence",
    "subtitle": "Start with the highest-impact changes",
    "duration_seconds": 6,
    "narrative_text": "Closing with an actionable summary of next steps"
  }
}

Remember: Output ONLY the JSON object. No markdown code fences. No explanatory text before or after. Start with { and end with }.
