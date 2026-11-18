# Review Video Storyline

Build a video storyline from AI-powered reviews, organizing them by review type.

## Site Context
- **Site:** {{ site_name }}
- **URL:** {{ url }}
- **Review types:** {{ review_types }}

## Reviews Available
{{ reviews_summary }}

## Storyline Requirements

1. **Intro**: "{site_name} — Expert Review Highlights" celebrating the analysis
2. **One chapter per review type** that has meaningful content
3. **Strengths first**: Each chapter highlights what works well before showing opportunities
4. **Feature suggestions chapter**: "Features That Could Set You Apart"
5. **Outro**: "The Path Forward" with an encouraging summary

## Tone

Expert reviewer — celebratory of strengths, encouraging about growth opportunities. The video should feel like a positive, insightful presentation from a knowledgeable consultant.

## Duration Target

Target: {{ target_duration_minutes }} minutes total.
Allocate time based on the depth of each review type.

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
  "video_title": "example.com — Expert Review Highlights",
  "video_description": "A celebratory look at what works well and where growth opportunities lie",
  "target_duration_minutes": 2,
  "tone": "expert reviewer, encouraging",
  "intro": {
    "title": "example.com — Expert Review Highlights",
    "subtitle": "Celebrating strengths, exploring opportunities",
    "duration_seconds": 8,
    "narrative_text": "Opening with the site's standout qualities"
  },
  "chapters": [
    {
      "number": 1,
      "title": "Design Excellence",
      "subtitle": "What the site does beautifully",
      "pages": ["page-001"],
      "narrative_arc": "Highlight the strongest design elements and visual consistency",
      "key_moments": ["Color palette harmony", "Typography hierarchy"],
      "estimated_duration_seconds": 60
    }
  ],
  "outro": {
    "title": "The Path Forward",
    "subtitle": "Building on a strong foundation",
    "duration_seconds": 6,
    "narrative_text": "Closing with an encouraging summary of the site's potential"
  }
}

Remember: Output ONLY the JSON object. No markdown code fences. No explanatory text before or after. Start with { and end with }.
