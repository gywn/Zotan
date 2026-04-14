---
name: web-research
description: Use this skill when researching topics that require gathering information from multiple web sources. Applies to web searches, information gathering, fact-finding, and analysis tasks that need progressive accumulation of findings.
---

# Web Research and Information Analysis

A systematic process for finding, filtering, summarizing, and analyzing web information using progressive accumulation.

## Why Use This Skill

AI agents have limited context windows. When researching topics that require gathering information from many sources, simply storing all visited content would exceed these limits. Use **progressive accumulation** - maintaining a separate progress document that condenses key findings into summaries, allowing the agent to build knowledge incrementally while staying within context window constraints.

## Core Principles

1. **Filter**: Only record URLs and their summaries when they are relevant to the research goal.
2. **Summarize**: Never save entire web content. Always extract and condense key information.
3. **Track Progress**: Use progressive accumulation - update the progress document **immediately after deciding a URL is relevant and should be added**, not after a batch of URLs. Context windows are limited - HTML fetching can cause context float/loss, so save your progress as soon as you decide to include an entry.

## AI Agent Decision Authority

The AI Agent has full autonomy to:
- **Decide when to stop**: Stop when no new relevant URLs are emerging, or sufficient coverage is achieved.
- **Prioritize URLs**: Choose which links to explore first based on relevance, credibility, and diversity of perspectives.
- **Assess source credibility**: Evaluate source reputation, publication date, and cross-reference with other sources.
- **Explore internal links**: When fetching a webpage, also examine and consider links within that page. They have the same credibility and relevance as search results.
- **Build knowledge connections**: Identify and document relationships between related entries.

## Progress Document Structure

Create and maintain a progress document (e.g., `research_progress.md`) with this structure:

```markdown
## Research Goal
[Clear statement of what we're analyzing - update as understanding evolves]

## Current Understanding
[The AI's evolving understanding of the problem - update as information accumulates]

## Progress (Progressive Accumulation)

https://example.com/page1

{Summary of the key information from this page}

[Connections/Notes: related to other entries on topic X]

---

https://example.com/page2

{Summary of the key information from this page}

[Connections/Notes: contrasts with entry above on point Y]

---

...
```

## Connection Building

Each entry may include connections to other relevant entries:

```
https://en.wikipedia.org/wiki/Artificial_intelligence
AI is the capability of computational systems to perform tasks typically associated with human intelligence, including learning, reasoning, and problem-solving.
Connections: connects to IBM article below on AI applications; expands on basic definition from Google Cloud

https://www.ibm.com/think/topics/artificial-intelligence
IBM describes AI as technology enabling computers to simulate human learning, comprehension, decision-making, and creativity.
Notes: relates to Wikipedia entry above; provides more detail on practical applications
```

## Research Workflow

1. **Define Research Goal** - Write at top of progress document
2. **Search** - Use search tool to find relevant pages
3. **Filter** - Only keep URLs matching the goal
4. **Fetch One URL** - Use HTML fetching tool on a single selected URL
5. **Summarize** - Extract key points, never copy entire content
6. **Assess Relevance** - Decide if this URL adds value to the research
7. **Connect** - Note relationships to other entries
8. **Update Progress** - If relevant, write the summary to the progress document.
9. **Update Understanding** - Periodically revise the current understanding section as knowledge grows significantly
10. **Repeat** - Continue with next URL (steps 4-8)
