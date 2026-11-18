CRITICAL: Your ENTIRE response must be a single valid JSON object. No markdown, no explanations, no text before or after the JSON. Do not wrap the JSON in ```json``` code fences. Start with { and end with }.
{% if mode == "tutorial" %}

# Video Storyline Development — Tutorial Mode

Create a tutorial video storyline that teaches users how to navigate and use this website.

## Narrative Philosophy

This video will be watched by new users learning to use this product. Every chapter must teach a specific skill or workflow.

- **Chapter 1 should orient the user** — show where things are, how the interface is organized, and establish the mental model. Start with the homepage or dashboard and give viewers a guided tour of the layout.
- **Middle chapters should teach workflows** — each one walks through a complete user task from start to finish. Show the exact steps: where to click, what to type, what to expect. Build from simple tasks to more advanced ones.
- **Final chapter should cover advanced tips or settings** — show power-user features, customization options, or less obvious capabilities that improve productivity.

The narrative arc is: **Discover → Learn → Practice → Master**

## Tone Guidance

- Instructional and supportive. Think knowledgeable colleague, not corporate narrator.
- Action-oriented. "Click the Settings icon in the top-right corner" beats "Settings can be accessed."
- Patient, not rushing. Give users time to absorb each step before moving on.
{% elif mode == "company_commercial" %}

# Company Showcase Storyline

Create a brand-narrative video storyline for this company website.

## Narrative Philosophy

This video tells the story of a company — who they are, what they do, and why they're worth trusting. The audience is potential partners, clients, or investors learning about the organization for the first time.

- **Chapter 1 should establish identity** — who is this company? What do they stand for? The homepage and about page set the tone. Frame the company's mission and the people behind it.
- **Middle chapters should demonstrate expertise** — services, portfolio, capabilities. Show what the company delivers through their work, not through product demos. Focus on depth and credibility.
- **Final chapter should build trust** — testimonials, case studies, certifications, contact. Give the viewer reasons to believe and a clear path to engage.

The narrative arc is: **Identity → Expertise → Proof → Connection**

## Tone Guidance

- Authoritative yet approachable. Think company documentary, not sales pitch.
- Let the company's work speak. "Delivered 14 enterprise deployments across APAC" beats "We're experts."
- Warm but professional. This is a handshake, not a hard sell.
{% else %}

# Video Storyline Development

Create a compelling demo video storyline for this website.

## Narrative Philosophy

This video will be shown to busy decision-makers. Every chapter must earn its screen time.

- **Chapter 1 should establish the problem or context** — why does this product/service exist? What pain point does it solve? Don't just "show the homepage" — frame what the viewer is about to discover.
- **Middle chapters should build the case** — each one proves a specific capability. Show don't tell. Anchor features in business outcomes: time saved, risk reduced, growth enabled.
- **Final chapter should close with proof or next steps** — testimonials, case studies, pricing, or a clear call-to-action.

The narrative arc is: **Context → Capability → Proof → Action**
{% if mode == "webapp_commercial" %}

Note: If the site is primarily a corporate/brand website with limited interactivity, consider using `company_commercial` mode instead.
{% endif %}

## Tone Guidance

- Professional, not salesy. Think trusted advisor, not infomercial.
- Specific, not vague. "Reduces reporting time from 8 hours to 30 minutes" beats "Powerful analytics."
- Confident, not breathless. No "amazing", "incredible", "revolutionary."
{% endif %}

## Guidelines
{% if mode == "tutorial" %}
- Target approximately {{ target_duration_minutes }} minutes total duration
- Start by showing the entry point (homepage or login landing page)
- Organize chapters by user task or workflow (not by business value)
- Each chapter should complete one meaningful workflow end-to-end
- End with a summary of what was covered and where to find help
- Use a learning progression: orient → basic tasks → advanced features → wrap up
{% elif mode == "company_commercial" %}
- Target approximately {{ target_duration_minutes }} minutes total duration
- Start with the company's identity (homepage, about page)
- Prioritize pages that tell the company story: About, Team, Services, Portfolio, Testimonials, Case Studies, Contact
- Deprioritize interactive product pages, login forms, search interfaces, and app dashboards
- End with trust signals and a way to connect
- Use a contemplative, narrative flow: who → what → proof → connect
{% else %}
- Target approximately {{ target_duration_minutes }} minutes total duration
- Start with a strong first impression (homepage)
- Group related pages into logical chapters (2-5 chapters)
- Each chapter should tell a mini-story with a clear theme
- End with a call-to-action (visit the site, sign up, etc.)
- Use a natural narrative flow: introduce → explore → deep dive → conclude
{% endif %}

## Chapter Sizing
{% if mode == "company_commercial" %}
- Each chapter should contain **1-3 pages**. Prefer grouping 2-3 related pages over creating many single-page chapters. A 5-page site should have 2-3 chapters, not 5.
- Estimate **40-70 seconds per page** (slower than product demos — let the viewer absorb the brand). A 2-page chapter = roughly 90-150 seconds.
- Keep the total chapter count between 2 and 4. Fewer, more meaningful chapters let the company story breathe.
{% else %}
- Each chapter should contain **1-3 pages**. Prefer grouping 2-3 related pages over creating many single-page chapters. A 5-page site should have 2-3 chapters, not 5.
- Estimate **30-60 seconds per page** plus 5-10 seconds for transitions between pages within a chapter. A 2-page chapter = roughly 70-130 seconds.
- Keep the total chapter count between 2 and 5. Fewer, richer chapters are better than many thin ones.
{% endif %}

## SPA Handling

If multiple pages share the same base URL (single-page application sections, often marked with `[SPA]` in the pages list), group them into one chapter. They represent different views within the same application section and should be presented as a cohesive flow.

## Storyline Structure
{% if mode == "tutorial" %}
1. **Intro** (6-8 seconds): Title card with product name and "Getting Started Guide" or similar
2. **Chapters** (2-5): Each chapter teaches a workflow with 1-3 pages
3. **Outro** (5-6 seconds): Closing title card summarizing what was covered
{% elif mode == "company_commercial" %}
1. **Intro** (6-8 seconds): Title card with company name and positioning
2. **Chapters** (2-4): Each chapter covers a theme with 1-3 pages
3. **Outro** (5-6 seconds): Closing title card with CTA (visit, contact, partner)
{% else %}
1. **Intro** (6-8 seconds): Title card with website name and tagline
2. **Chapters** (2-5): Each chapter covers a theme with 1-3 pages
3. **Outro** (5-6 seconds): Closing title card with CTA
{% endif %}

## Output Format

Your response must be ONLY a raw JSON object (no markdown fences, no commentary). It must match this schema:
{% if mode == "tutorial" %}
{
  "video_title": "Site Name — Getting Started Guide",
  "video_description": "Learn how to use...",
  "target_duration_minutes": {{ target_duration_minutes }},
  "tone": "instructional, supportive",
  "intro": {
    "title": "Getting Started with Site Name",
    "subtitle": "A step-by-step walkthrough",
    "duration_seconds": 8,
    "narrative_text": "Opening with..."
  },
{% elif mode == "company_commercial" %}
{
  "video_title": "Company Name — Positioning Statement",
  "video_description": "A showcase of...",
  "target_duration_minutes": {{ target_duration_minutes }},
  "tone": "authoritative, approachable",
  "intro": {
    "title": "Company Name",
    "subtitle": "Positioning or tagline",
    "duration_seconds": 8,
    "narrative_text": "Opening with..."
  },
{% else %}
{
  "video_title": "Site Name — Tagline",
  "video_description": "A walkthrough of...",
  "target_duration_minutes": {{ target_duration_minutes }},
  "tone": "professional, friendly",
  "intro": {
    "title": "Welcome to Site Name",
    "subtitle": "Your solution for...",
    "duration_seconds": 8,
    "narrative_text": "Opening with..."
  },
{% endif %}
  "chapters": [
    {
      "number": 1,
      "title": "Chapter Title",
      "subtitle": "Brief description",
      "pages": ["page-001", "page-003"],
{% if mode == "tutorial" %}
      "narrative_arc": "What the user learns in this chapter",
      "key_moments": ["'Create New' button in the top toolbar", "Project settings form with name and description fields"],
{% else %}
      "narrative_arc": "What happens in this chapter",
      "key_moments": ["'Start Free Trial' hero CTA button", "Feature comparison table with three tiers"],
{% endif %}
      "estimated_duration_seconds": 90
    }
  ],
  "outro": {
{% if mode == "tutorial" %}
    "title": "You're All Set",
    "subtitle": "Start exploring on your own",
{% elif mode == "company_commercial" %}
    "title": "Let's Connect",
    "subtitle": "Visit company.com",
{% else %}
    "title": "Get Started Today",
    "subtitle": "Visit example.com",
{% endif %}
    "duration_seconds": 6,
    "narrative_text": "Closing with..."
  }
}

Note: `pages` is an array of page IDs like `"page-001"`, `"page-003"`. A chapter with multiple pages means the video will show those pages in sequence within that chapter.

**key_moments must reference SPECIFIC page elements.** Downstream systems use key_moments literally as `Highlight '{moment}'` actions in the shot list. Vague moments produce unusable shots.

Good key_moments (specific, actionable):
- `"'Start Free Trial' hero button"` — references a real button
- `"Analytics Dashboard with real-time metrics"` — references a named feature
- `"'Trusted by 500+ companies' social proof banner"` — references actual page text
- `"Three-tier pricing table: Starter, Pro, Enterprise"` — references concrete content

Bad key_moments (vague, generic):
- `"Key feature"` — which feature?
- `"Important section"` — which section?
- `"Main content area"` — too generic to act on
- `"Product overview"` — doesn't identify what to highlight
{% if mode == "tutorial" %}

## Tutorial Arc Examples

**Example 1 — B2B SaaS product (3 chapters):**
- Chapter 1 "Finding Your Way Around" (pages: homepage + dashboard) — Orient the user: show the main navigation, sidebar, and key sections. Point out the most important buttons and menus.
- Chapter 2 "Your First Task" (pages: create/edit page + list view) — Walk through creating or completing a core workflow step by step. Show what each field does and what happens after submitting.
- Chapter 3 "Customizing Your Experience" (pages: settings + profile) — Show how to configure preferences, manage account settings, and find help resources.

**Example 2 — E-commerce site (2 chapters):**
- Chapter 1 "Browsing & Finding Products" (pages: homepage + catalog + product detail) — Teach users how to search, filter, and find products. Show how to read product details and compare options.
- Chapter 2 "Making a Purchase" (pages: cart + checkout + FAQ) — Walk through adding items to cart, completing checkout, and finding answers to common questions.

**Example 3 — Documentation / knowledge base (3 chapters):**
- Chapter 1 "Navigation & Search" (pages: homepage + search) — Show how to find content using navigation, search, and category browsing.
- Chapter 2 "Reading & Using Content" (pages: article + documentation) — Demonstrate how to use code examples, follow tutorials, and navigate between related topics.
- Chapter 3 "Getting Help" (pages: FAQ + contact) — Show how to find answers, contact support, and use community resources.

**Example 4 — Single-page SaaS app (2 chapters, same page):**
- Chapter 1 "Getting Oriented" (pages: page-001) — narrative_arc: "Orient the user with the dashboard layout and navigation sidebar." key_moments: ["'My Projects' sidebar navigation", "'Create New' button in top toolbar"]. Focus on the UI chrome: menus, toolbar, sidebar.
- Chapter 2 "Completing a Workflow" (pages: page-001) — narrative_arc: "Walk through creating a project using the inline editor." key_moments: ["Project settings form fields", "'Save & Publish' confirmation dialog"]. Focus on the interactive workflow within the same page.
{% elif mode == "company_commercial" %}

## Narrative Arc Examples

**Example 1 — Professional services firm (3 chapters):**
- Chapter 1 "Who They Are" (pages: homepage + about) — Establish the company's identity, mission, and the team behind it. Let the hero messaging and about page set the tone.
- Chapter 2 "What They Deliver" (pages: services + portfolio) — Walk through service offerings and showcase past work. Emphasize concrete outcomes and industry expertise.
- Chapter 3 "Why Trust Them" (pages: testimonials + contact) — Close with client voices, certifications, and a clear invitation to connect.

**Example 2 — Corporate website (2 chapters):**
- Chapter 1 "The Company" (pages: homepage + about + team) — Present the full company story in one arc: mission, history, and people.
- Chapter 2 "Impact & Connect" (pages: case studies + contact) — Demonstrate results and invite the next conversation.

**Example 3 — Creative agency (3 chapters):**
- Chapter 1 "Meet the Studio" (pages: homepage + about) — Introduce the agency's creative philosophy and founding story.
- Chapter 2 "The Work" (pages: portfolio + case studies) — Let the portfolio do the talking. Focus on 2-3 standout projects with measurable impact.
- Chapter 3 "Start a Project" (pages: services + contact) — Explain the engagement model and make it easy to reach out.

**Example 4 — Single-page company site (3 chapters, same page):**
- Chapter 1 "Who They Are" (pages: page-001) — narrative_arc: "Establish identity through the hero section and company mission statement." key_moments: ["'Building the future of sustainable energy' hero headline", "'Founded in 2018' origin story section"]. Focus on the top-of-page brand messaging.
- Chapter 2 "What They Do" (pages: page-001) — narrative_arc: "Showcase service offerings from the mid-page services grid." key_moments: ["'Solar Installation' service card", "'Grid Storage Solutions' service card", "'24/7 Monitoring' capability badge"]. Focus on the services and capabilities sections.
- Chapter 3 "Why Trust Them" (pages: page-001) — narrative_arc: "Close with social proof and contact details from the page footer area." key_moments: ["'98% client satisfaction' statistics banner", "'Get a Free Quote' CTA button"]. Focus on testimonials, stats, and the contact/CTA section.
{% elif mode == "webapp_commercial" %}

## Narrative Arc Examples

**Example 1 — B2B SaaS product (3 chapters):**
- Chapter 1 "The Challenge" (pages: homepage) — Frame the business problem this product solves. Show the hero messaging and value propositions that set context for what follows.
- Chapter 2 "The Solution" (pages: product + features) — Demonstrate core capabilities through the product itself. Focus on 2-3 differentiators, not every feature.
- Chapter 3 "Results & Getting Started" (pages: case studies/pricing/signup) — Provide proof that it works and make the next step obvious.

**Example 2 — E-commerce site (2 chapters):**
- Chapter 1 "Browse & Discover" (pages: homepage + catalog + product detail) — Show the shopping experience from landing to finding a product.
- Chapter 2 "Purchase & Support" (pages: cart + FAQ) — Complete the journey with checkout flow and customer support options.

**Example 3 — SaaS dashboard (3 chapters):**
- Chapter 1 "First Impressions" (pages: homepage + signup) — Show the landing page messaging and how easy it is to get started.
- Chapter 2 "Inside the Platform" (pages: dashboard + key feature pages) — Walk through the core product experience, demonstrating interactive features and real-time capabilities.
- Chapter 3 "Why Teams Choose It" (pages: pricing + testimonials) — Prove ROI with social proof and make the buying decision easy.

**Example 4 — Single-page landing site (3 chapters, same page):**
- Chapter 1 "The Problem" (pages: page-001) — narrative_arc: "Frame the pain point using the hero section and problem statement." key_moments: ["'Stop wasting hours on manual reporting' hero headline", "'3 out of 4 teams struggle with...' statistics callout"]. Focus on the hero and problem-framing sections at the top of the page.
- Chapter 2 "The Platform" (pages: page-001) — narrative_arc: "Demonstrate capabilities through the features grid and product screenshots." key_moments: ["'Automated Pipelines' feature card with diagram", "'Real-time Collaboration' split-screen demo", "'One-click Integrations' logo grid showing Slack, Jira, GitHub"]. Focus on the mid-page features and product showcase sections.
- Chapter 3 "Get Started" (pages: page-001) — narrative_arc: "Close with social proof and conversion path." key_moments: ["'Trusted by teams at Stripe, Notion, and Linear' logo bar", "'Start free — no credit card required' CTA button"]. Focus on the testimonials, pricing, and signup sections near the bottom.
{% else %}

## Narrative Arc Examples

**Example 1 — B2B SaaS product (3 chapters):**
- Chapter 1 "The Challenge" (pages: homepage) — Frame the business problem this product solves. Show the hero messaging and value propositions that set context for what follows.
- Chapter 2 "The Solution" (pages: product + features) — Demonstrate core capabilities through the product itself. Focus on 2-3 differentiators, not every feature.
- Chapter 3 "Results & Getting Started" (pages: case studies/pricing/signup) — Provide proof that it works and make the next step obvious.

**Example 2 — E-commerce site (2 chapters):**
- Chapter 1 "Browse & Discover" (pages: homepage + catalog + product detail) — Show the shopping experience from landing to finding a product.
- Chapter 2 "Purchase & Support" (pages: cart + FAQ) — Complete the journey with checkout flow and customer support options.

**Example 3 — Professional services / consultancy (3 chapters):**
- Chapter 1 "Who They Are" (pages: homepage + about) — Establish credibility: what the company does, who they serve, and their core methodology.
- Chapter 2 "What They Offer" (pages: services + solutions) — Walk through service offerings with emphasis on concrete outcomes and differentiators.
- Chapter 3 "Why Choose Them" (pages: case studies + contact) — Show proof of results and make it easy to take the next step.

**Example 4 — Single-page product site (3 chapters, same page):**
- Chapter 1 "The Challenge" (pages: page-001) — narrative_arc: "Frame the problem using the hero messaging and value propositions at the top of the page." key_moments: ["'Cut your deployment time by 90%' hero headline", "'Why teams switch' comparison table"]. Focus on the hero section and problem framing.
- Chapter 2 "The Solution" (pages: page-001) — narrative_arc: "Walk through the product capabilities shown in the features and how-it-works sections." key_moments: ["'Visual Pipeline Builder' feature with screenshot", "'Three steps to deploy' workflow diagram", "'Enterprise-grade Security' compliance badges"]. Focus on the mid-page features and product demo sections.
- Chapter 3 "Results & Next Steps" (pages: page-001) — narrative_arc: "Close with customer evidence and a clear path to get started." key_moments: ["'Acme reduced downtime by 74%' case study highlight", "'Free for up to 5 users' pricing callout", "'Book a Demo' CTA button"]. Focus on testimonials, pricing, and the signup/contact section.
{% endif %}

## Important Rules

The `pages` array in each chapter MUST contain `page_id` values (e.g., `"page-001"`, `"page-002"`) copied exactly from the "Pages Available" section below. Do NOT use page titles or URLs — use only the `page_id` identifiers listed for each page. Do NOT invent or guess page IDs.

Every page from the "Pages Available" section should appear in at least one chapter. When multiple pages exist, avoid duplicating the same page across chapters unless necessary. When only one page exists, all chapters will reference that page but must focus on different sections of it.

You MUST include at least 1 chapter in the "chapters" array. A video cannot be produced with 0 chapters. If only one page is available, create a single chapter containing that page.

Your response MUST be a valid JSON object matching the schema above. Do NOT return an empty "chapters" array. Do NOT omit the "chapters" key.

If the site has only one page (or very few pages), you MUST still create distinct chapters. Each chapter should focus on a DIFFERENT section of that page. Use the content_sections and headings from the page analysis to identify distinct areas (hero, features, pricing, testimonials, how-it-works, CTA, etc.). Assign different sections as the chapter's focus via narrative_arc and key_moments — even though all chapters reference the same page_id.

**Single-page chapter differentiation is critical.** When multiple chapters share the same page_id, the ONLY way downstream systems distinguish them is through `key_moments`. Each chapter's key_moments MUST reference elements from different page sections:
- Chapter 1 key_moments should reference hero/value-prop elements (e.g., the headline text, tagline, hero image description)
- Chapter 2 key_moments should reference features/capabilities (e.g., specific feature names, product screenshots, workflow diagrams)
- Chapter 3 key_moments should reference proof/CTA elements (e.g., testimonial quotes, pricing tier names, signup button text, partner logos)

If two chapters have overlapping or vague key_moments (like "Key feature" and "Important section"), the downstream shotlist builder cannot generate distinct actions and the video will have redundant scenes. Be specific: use actual heading text, section names, button labels, and feature names from the page analysis.

## Required JSON Schema

Your response MUST be a single JSON object matching this exact schema:

{
{% if mode == "tutorial" %}
  "video_title": "string — video title with product name",
{% elif mode == "company_commercial" %}
  "video_title": "string — video title with company name",
{% else %}
  "video_title": "string — video title with site name",
{% endif %}
  "video_description": "string — one-sentence video description",
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
{% if mode == "tutorial" %}
      "narrative_arc": "string — what the user learns in this chapter",
{% else %}
      "narrative_arc": "string — what happens in this chapter",
{% endif %}
      "key_moments": ["string — specific element names from the page, e.g. heading text, feature names, button labels"],
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
{% if mode == "tutorial" %}

EXAMPLE (minimal valid response):

{
  "video_title": "Acme Corp — Getting Started Guide",
  "video_description": "Learn how to navigate and use the Acme Corp platform",
  "target_duration_minutes": 2,
  "tone": "instructional, supportive",
  "intro": {
    "title": "Getting Started with Acme Corp",
    "subtitle": "A step-by-step walkthrough",
    "duration_seconds": 8,
    "narrative_text": "Opening with an overview of the Acme interface"
  },
  "chapters": [
    {
      "number": 1,
      "title": "Finding Your Way Around",
      "subtitle": "Interface orientation",
      "pages": ["page-001"],
      "narrative_arc": "Orient the user with the main navigation and layout",
      "key_moments": ["Top navigation bar with Projects, Settings, and Help links", "Dashboard overview showing activity feed and quick-action cards"],
      "estimated_duration_seconds": 60
    },
    {
      "number": 2,
      "title": "Your First Task",
      "subtitle": "Creating your first project",
      "pages": ["page-002", "page-003"],
      "narrative_arc": "Walk through completing a core workflow step by step",
      "key_moments": ["'New Project' form with name, description, and team fields", "'Create Project' button and success confirmation toast"],
      "estimated_duration_seconds": 90
    }
  ],
  "outro": {
    "title": "You're All Set",
    "subtitle": "Start exploring on your own",
    "duration_seconds": 6,
    "narrative_text": "Closing with a summary of what was covered"
  }
}
{% elif mode == "company_commercial" %}

EXAMPLE (minimal valid response):

{
  "video_title": "Meridian Partners — Strategic Consulting for Growth",
  "video_description": "A showcase of Meridian Partners' expertise, team, and client impact",
  "target_duration_minutes": 2,
  "tone": "authoritative, approachable",
  "intro": {
    "title": "Meridian Partners",
    "subtitle": "Strategic consulting for the digital age",
    "duration_seconds": 8,
    "narrative_text": "Opening with Meridian's brand identity and mission"
  },
  "chapters": [
    {
      "number": 1,
      "title": "Who We Are",
      "subtitle": "Mission, team, and values",
      "pages": ["page-001", "page-002"],
      "narrative_arc": "Establish Meridian's identity through their homepage and about page",
      "key_moments": ["'Transforming businesses through strategic insight' mission statement", "Leadership team grid with CEO and founding partners"],
      "estimated_duration_seconds": 90
    },
    {
      "number": 2,
      "title": "Our Impact",
      "subtitle": "Client results and next steps",
      "pages": ["page-003", "page-004"],
      "narrative_arc": "Show proven results and invite the viewer to connect",
      "key_moments": ["'Meridian helped us scale 3x in 12 months' client testimonial", "'Schedule a Consultation' contact form and office locations"],
      "estimated_duration_seconds": 80
    }
  ],
  "outro": {
    "title": "Let's Connect",
    "subtitle": "Visit meridian.com",
    "duration_seconds": 6,
    "narrative_text": "Closing with an invitation to start a conversation"
  }
}
{% else %}

EXAMPLE (minimal valid response):

{
  "video_title": "Acme Corp — Your Business Solution",
  "video_description": "A walkthrough of Acme Corp's platform and services",
  "target_duration_minutes": 2,
  "tone": "professional, friendly",
  "intro": {
    "title": "Welcome to Acme Corp",
    "subtitle": "Your solution for business growth",
    "duration_seconds": 8,
    "narrative_text": "Opening with a strong first impression of Acme Corp"
  },
  "chapters": [
    {
      "number": 1,
      "title": "The Challenge",
      "subtitle": "Why businesses need Acme",
      "pages": ["page-001"],
      "narrative_arc": "Frame the business problem Acme solves",
      "key_moments": ["'Grow faster with smarter automation' hero headline", "Three value proposition cards: Save Time, Reduce Risk, Scale Easily"],
      "estimated_duration_seconds": 60
    },
    {
      "number": 2,
      "title": "The Solution",
      "subtitle": "How Acme delivers results",
      "pages": ["page-002", "page-003"],
      "narrative_arc": "Demonstrate core capabilities through the product",
      "key_moments": ["'Smart Workflows' drag-and-drop builder interface", "'Connect Your Tools' integration grid showing 50+ apps"],
      "estimated_duration_seconds": 90
    }
  ],
  "outro": {
    "title": "Get Started Today",
    "subtitle": "Visit acme.com",
    "duration_seconds": 6,
    "narrative_text": "Closing with a clear call to action"
  }
}
{% endif %}

Remember: Output ONLY the JSON object. No markdown code fences. No explanatory text before or after. Start with { and end with }.
