---
name: news-research-report
description: Research recent public news with dated multilingual searches, evidence gates, contradiction checks, and sourced reports. Use for current news, country or company monitoring, trend analysis, issue discovery, Korea-related international news, and requests for evidence-backed Markdown or Word reports.
---

# News Research Report

Use `news_search` for current news discovery. Treat its results as metadata and snippets, not verified article text.

## Workflow

1. State the as-of date and requested date range before searching.
2. For an international report, prefer one `news_research_bundle` call with distinct English, Vietnamese, and Korean queries. It deterministically performs the three dated searches. Use separate `news_search` calls only for follow-up gaps.
3. Keep an evidence ledger containing the exact `evidence_id`, title, publisher, published time, exact URL, query language, and verification level returned by the tool. Prefer rows with `query_match_score >= 2`.
4. Use `web_fetch` only on an exact candidate URL returned by the bundle/search. Never guess a direct article URL from a title or publisher. Mark each item as `full-text`, `snippet-only`, or `unavailable`.
5. Deduplicate syndicated headlines and count independent publishers, not result rows.
6. Before writing, call `news_evidence_validate` with a JSON array of the selected `evidence_id` and exact `url`. If validation is not `valid`, remove or correct every rejected row and validate again. Never invent, shorten, decode, reconstruct, or otherwise rewrite a source URL.
7. Separate confirmed facts, cross-source interpretations, contradictions, risks, opportunities, and unknowns.
8. Write the source-backed Markdown before creating another output format.

When a Word report is requested, call `word_report_create` after the Markdown is validated and saved. If it reports `missing_dependency`, install only the exact returned package with `python_package_install`, then retry `word_report_create`. Do not generate an ad-hoc conversion script with `write` or `exec`.

## Evidence Gates

- With zero dated results, produce only a search-failure report. Do not invent events, organizations, dates, numbers, or URLs.
- With fewer than three independent publishers or fewer than two full-text articles, label the report `limited evidence` and avoid broad trend claims.
- A normal analytical report requires at least three independent publishers, two full-text articles, and a source URL for every material claim.
- Search snippets may establish what a source claims, but not that the claim is true.
- When evidence is limited, write "source X reports Y" and do not conclude that a relationship, market, or trend is broadly expanding, strengthening, declining, or established.
- Preserve the source's actors and scope. A domestic agency agreement that mentions a target country is not a bilateral agreement with that country.

## Completion Checks

- Compare the search count and source count in the report with actual tool evidence.
- Include only rows accepted by `news_evidence_validate`; copy fields from `validated_results` without alteration.
- Do not call a date future or past without comparing it to the runtime as-of date.
- Do not say a Word, PDF, chart, or other artifact exists until `ls` or an equivalent read-only check confirms it.
- If the requested output cannot be created or visually verified, deliver the Markdown and state the exact missing capability.
- Confirm that Word output reports at least one real table when the Markdown contains an evidence table.
- Never expose `<reasoning>` blocks or end with a plan for work that was not executed.

## Report Structure

Use: scope and as-of date, executive summary, evidence ledger, findings by theme, problems and risks, opportunities, contradictions and unknowns, limitations, and sources.
