# TickerPress — digest / alert body template (SCOPE FR-9, FR-10, D14).
#
# This file is the ONLY place digest and alert wording lives. Every section is a
# fixed Markdown fragment with `{placeholder}` fields that carry factual values
# only — title, link, outlet, timestamp, relevance, matched surfaces, copy
# count. There is deliberately no field for commentary, sentiment, price data or
# advice: the finance safeguard is the absence of such a code path, and the
# `footer` section below is what makes the informational-only framing part of
# every rendered body.
#
# Sections are delimited by a line of the form `=== name ===`. Leading and
# trailing blank lines of a section are dropped; everything else is preserved
# byte for byte. Lines before the first delimiter (this comment) are ignored.

=== digest_subject ===
TickerPress digest — {date}

=== digest_heading ===
# TickerPress digest — {date}

=== alert_subject ===
Alert: {ticker} — {title}

=== alert_heading ===
# TickerPress alert — {ticker}

=== company_heading ===
## {ticker} — {name}

=== item ===
- [{title}]({url})
  — {outlet}, {published} UTC, relevance {relevance}, matched: {matched}{extra}

=== extra_one ===
 — +1 other outlet

=== extra_many ===
 — +{count} other outlets

=== footer ===
---
Informational only — links to third-party news coverage. Not investment advice.
