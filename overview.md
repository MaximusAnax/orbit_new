## Orbit - Personal Relationship Memory System

**Status:** Early concept — actively evolving

**Primary user:** Abdoul

**Core idea:** An AI-powered external memory for the people in Abdoul's life.

---

# 1. The Core Idea

Humans care about far more people than they can realistically keep actively in mind.

We forget:

- What someone told us
- What they were excited about
- What they were worried about
- Where they worked at a particular point in time
- What they wanted to do someday
- The small details that make someone feel remembered
- When we last spoke
- What we promised to follow up on

The problem is not necessarily that we stop caring.

Often, human memory simply cannot preserve the full context of all our relationships.

**Orbit** is a personal AI memory system designed to preserve that context.

It helps Abdoul remember the people in his life, understand the history of his relationships, maintain the relationships he cares about, and search his network for people and knowledge that may be useful.

The goal is not to turn relationships into a CRM.

The goal is to help people feel:

> **"I can't believe you remembered that."**
> 

---

# 2. The Two Core Pillars

The product has two central capabilities.

## Pillar One: Perfect Relationship Recall

Before seeing someone Abdoul has not spoken to in months or years, Orbit should be able to quickly bring the relationship back to life.

For example:

> "I haven't seen this person in two years."
> 

Orbit should help Abdoul remember:

- Who they are
- How they met
- What they were doing at the time
- What they cared about
- What they were working on
- Important things they mentioned
- Their personal interests
- Their goals
- Their family or life context
- Previous conversations
- Shared experiences
- Unresolved threads
- Things Abdoul said he would follow up on
- Small details that would otherwise have disappeared from memory

The ideal result is not simply a summary.

It is a feeling of:

> **"Oh right. I remember everything now."**
> 

The context was not forgotten.

---

## Pillar Two: Network Intelligence

Orbit should make Abdoul's network searchable and queryable.

Examples:

> "Who do I know at Anthropic?"
> 

> "Who can connect me to someone at Goldman Sachs?"
> 

> "Who in my network is interested in videography?"
> 

> "Who knows about low-latency systems?"
> 

> "Who might be interested in helping me build an AI startup?"
> 

> "Who do I know that is interested in AI agents?"
> 

> "Who could introduce me to an engineer who has worked on distributed systems?"
> 

This should not require Abdoul to remember how information was stored.

He should be able to ask questions naturally.

Orbit should search across:

- People
- Relationships
- Events
- Interests
- Skills
- Companies
- Schools
- Locations
- Experiences
- Shared connections
- Historical context

The system should ultimately be capable of reasoning over the network.

Not merely returning keyword matches.

---

# 3. The Third Pillar: Memory Fidelity

The product must preserve small details.

A person is not just:

> "Works at Company X."
> 

They may also be:

- Someone who wanted to travel to Japan
- Someone whose sister was getting married
- Someone who was nervous about an interview
- Someone who used to work at a particular company
- Someone who once wanted to learn videography
- Someone who mentioned an obscure hobby once over dinner

These details matter.

They are often what makes a relationship feel personal.

Orbit should preserve historical context rather than continuously overwriting the past.

---

# 4. The Core Data Model

The fundamental unit is a **Person**.

But a person is not just a contact record.

A Person is connected to:

1. Contact Points
2. Events
3. Relationship State
4. Historical Facts
5. Memories
6. Tasks and Follow-ups
7. Other People
8. Organizations and Entities

---

# 5. Person

A Person represents an individual in Abdoul's life.

A person profile may include:

## Identity

- Name
- Preferred name
- Profile photo
- Pronunciation, if relevant

## Contact Points

Contact points are kept separate from the person's identity.

Possible contact points include:

- Phone number
- Email address
- Instagram
- LinkedIn
- X
- Personal website
- Other social profiles
- Messaging platforms

Where possible, contact points should be actionable.

For example:

- Tap to call
- Tap to text
- Open Instagram
- Open LinkedIn
- Send an email

The system should also support connecting a person to an existing phone contact rather than requiring duplicate manual entry.

---

# 6. Historical Information Must Never Be Lost

Orbit should preserve the evolution of a person over time.

If someone:

- Changes jobs
- Moves cities
- Changes schools
- Develops new interests
- Loses old interests
- Changes goals
- Enters a new stage of life

the old information should remain available as historical context.

For example:

### 2024

> Worked at Company A.
> 

### 2025

> Joined Company B.
> 

### 2026

> Started a company.
> 

The profile should not simply replace Company A with Company B.

Instead, the system should preserve the timeline.

This allows questions such as:

> "Where did Sarah used to work?"
> 

> "When did James join his current company?"
> 

> "What was Maria interested in before she started working in AI?"
> 

Historical information is part of the relationship.

---

# 7. Events

An Event represents something that happened.

Examples:

- Dinner
- Coffee
- Phone call
- Text conversation
- Conference
- Party
- Meeting
- Introduction
- Encounter
- Shared experience

An event should preserve:

- Date
- Location, where relevant
- People involved
- What happened
- Topics discussed
- New facts learned
- Emotional context
- Commitments made
- Potential follow-ups
- Source of the information

The event is a historical record.

Once finalized, it should be treated as immutable.

---

# 8. Event Capture

The primary interaction should be natural voice input.

After an interaction, Abdoul should be able to speak freely.

For example:

> "I had dinner with Sarah tonight. She works at Stripe now, but she used to be at Google. She's thinking about moving to Boston next year. She mentioned that she really wants to learn videography, and we talked about AI agents for a while. She also told me her brother is visiting next month. I said I'd send her that paper we discussed."
> 

The system should extract:

- The event
- The people involved
- New information
- Historical information
- Topics
- Interests
- Commitments
- Potential follow-ups

The goal is to speak naturally.

Not to fill out forms.

---

# 9. Events and Profiles Are Separate

A critical design principle:

> **An event is not the same thing as a profile.**
> 

The event records what happened.

The profile represents the accumulated understanding of the person.

A voice note should not immediately and silently rewrite the profile.

Instead:

1. Abdoul records an event.
2. Orbit transcribes and structures the event.
3. The event is presented for review.
4. Abdoul confirms or edits the event.
5. The event is saved.
6. Orbit may propose changes to the relevant profile.
7. Abdoul can approve, reject, or defer those changes.

---

# 10. Profile Synchronization Must Be Explicit

No profile changes should be finalized without Abdoul's confirmation.

After an event is saved, Orbit may propose:

> **Proposed profile updates**
> 
- Sarah now works at Stripe.
- Sarah previously worked at Google.
- Sarah is interested in learning videography.
- Sarah may move to Boston next year.
- Sarah's brother is visiting next month.
- Follow up with Sarah about the AI paper.

Abdoul can:

- Accept all
- Accept individually
- Edit
- Reject
- Defer

If Abdoul does not have time to review the proposed changes, the event can remain unsynced.

The system should remember:

> **This event exists, but it has not yet been incorporated into the person's profile.**
> 

Later, Abdoul can choose:

> **Sync this event with profile**
> 

and review the proposed updates.

This creates an important distinction between:

### Captured

The event has been recorded.

### Confirmed

The event itself has been reviewed and accepted.

### Synchronized

The event has been used to propose changes to the person's living profile.

### Finalized

The profile changes have been explicitly confirmed by Abdoul.

---

# 11. Group Events

Group events are a first-class concept.

For example:

> "I went to this event and met Alex, Sarah, James, and Maria. Alex introduced me to Sarah. James is working on AI infrastructure. Maria is a videographer. Sarah is interested in startups..."
> 

The system should allow Abdoul to speak freely about the entire event.

The event can contain multiple people.

Orbit should then create proposed person-specific updates.

For example:

### Alex

- Attended the event
- Introduced Abdoul to Sarah

### Sarah

- Met at the event
- Introduced by Alex
- Interested in startups

### James

- Met at the event
- Works on AI infrastructure

### Maria

- Met at the event
- Works in videography

The system should not silently guess when attribution is uncertain.

If the system is unsure who a fact belongs to, it should flag the ambiguity.

For example:

> "You mentioned that someone at the event works in AI infrastructure. Was this James?"
> 

Accuracy is more important than aggressive automation.

---

# 12. Relationship State

The historical event timeline is objective.

The relationship state is subjective.

These should be separate.

Abdoul should be able to describe his relationship with someone in his own words.

For example:

> "We're close friends. We don't talk every week, but we have a strong relationship and usually reconnect easily."
> 

Or:

> "He's someone I want to become closer with professionally."
> 

Or:

> "She's an old friend. We don't need frequent maintenance, but I want to stay connected."
> 

Relationship state may include:

- How Abdoul feels about the relationship
- How close the relationship is
- How much effort Abdoul wants to invest
- What Abdoul wants from the relationship
- What he believes the relationship currently is
- What direction he wants it to move in
- How often he ideally wants to interact

The AI may help organize and summarize this information.

But Abdoul remains the authority.

---

# 13. Orbits

Orbit is also the conceptual model for relationship proximity.

People can occupy different relational orbits.

For example:

### Inner Orbit

- Immediate family
- Closest friends
- People with deep emotional importance

### Close Orbit

- Strong friends
- Important mentors
- Significant collaborators

### Active Orbit

- People Abdoul wants to maintain a meaningful relationship with

### Extended Orbit

- Acquaintances
- Professional contacts
- People Abdoul may want to reconnect with

### Outer Orbit

- People worth remembering
- Weak ties
- Historical contacts
- People who may become relevant again

The orbit is not necessarily a measure of how much someone matters.

A close family member may require little maintenance because the relationship is naturally resilient.

A professional contact may require more deliberate maintenance despite being less emotionally close.

Therefore, orbit and maintenance should be modeled separately.

---

# 14. Relationship Maintenance

The system should not assume:

> "You haven't talked to this person recently, therefore the relationship is neglected."
> 

Different relationships have different natural rhythms.

Instead, Orbit should consider:

- Relationship type
- Desired closeness
- Natural communication cadence
- Recent interactions
- Abdoul's stated intentions
- Open loops
- Important life events
- Whether a relationship is naturally resilient

The system should help answer:

> **Who might I want to reach out to right now, and why?**
> 

Not merely:

> **Who has gone the longest without contact?**
> 

---

# 15. The Pre-Meeting Experience

One of the defining product experiences is preparing for an interaction.

Before lunch with someone Abdoul has not seen in two years, he should be able to open their profile and quickly regain context.

The system should surface:

- How they met
- The last time they spoke
- Previous interactions
- Major life changes
- Current work
- Past work
- Interests
- Goals
- Important personal details
- Previous topics of conversation
- Unresolved threads
- Things Abdoul promised to do
- Things the person was waiting for
- Potential conversation starters

The goal is not to provide a generic AI summary.

The goal is:

> **"Everything comes back."**
> 

The system should help Abdoul enter the conversation with genuine continuity.

---

# 16. Relationship Memory Should Be Searchable

Orbit should support natural-language queries.

Examples:

> "What was the name of the person who wanted to travel to Japan?"
> 

> "Who did I meet at that conference who works in AI infrastructure?"
> 

> "Who has talked to me about videography?"
> 

> "Who do I know that is interested in startups?"
> 

> "Who have I met through Alex?"
> 

> "Who was working at Google before joining Stripe?"
> 

> "Who have I not seen in two years?"
> 

The system should search across events, profiles, historical facts, and relationship context.

---

# 17. Network Intelligence

Orbit should eventually reason about the network.

Examples:

> "Who can introduce me to someone at Anthropic?"
> 

> "Who do I know at Goldman Sachs?"
> 

> "Who is connected to people in quantitative trading?"
> 

> "Who should I talk to if I want to learn videography?"
> 

> "Who in my network would be interested in building this product?"
> 

> "Who are the best two engineers I know for this project?"
> 

This may require the system to understand:

- Direct relationships
- Second-degree connections
- Shared organizations
- Shared interests
- Skills
- Past experiences
- Trust
- Relationship strength
- Willingness to make introductions

The system should eventually move from:

> **Search**
> 

to:

> **Reasoning**
> 

to:

> **Network intelligence.**
> 

---

# 18. The Core Product Experience

Orbit should have three fundamental modes:

## 1. Capture

> "What happened?"
> 

Abdoul speaks naturally.

Orbit records and structures the event.

## 2. Recall

> "Who is this person, and what do I remember about them?"
> 

Orbit reconstructs the relationship.

## 3. Discover

> "Who do I know that can help with this?"
> 

Orbit searches and reasons over the network.

Together:

> **Capture → Remember → Discover**
> 

---

# 19. The Product's Defining Promise

Orbit should help Abdoul do two things extraordinarily well:

### Remember people deeply.

Even after long periods of time.

### Find people intelligently.

Even when Abdoul does not know exactly who he is looking for.

The system should preserve both:

> **The history of relationships**
> 

and

> **The structure of the network.**
> 

---

# 20. Current One-Sentence Description

> **Orbit is a personal AI memory for relationships that captures the events and details of Abdoul's life, preserves the history of the people he knows, helps him recall relationships with remarkable fidelity, and lets him intelligently search and reason over his network.**
> 

---

# 21. Current North Star

> **Make the people Abdoul cares about feel like they were never forgotten.**
> 

---

# 22. Product Constitution

These are the principles Orbit should not violate as the product evolves.

## Principle 1: The Human Is the Authority

Orbit may assist with interpretation.

It may suggest.

It may organize.

It may surface patterns.

But it should not override Abdoul's understanding of his own relationships.

> **AI assists. The human decides.**
> 

---

## Principle 2: Never Silently Rewrite History

Past information should not disappear simply because it is no longer current.

People change.

Jobs change.

Interests change.

Relationships change.

The historical record should remain intact.

> **The past is context, not obsolete data.**
> 

---

## Principle 3: Capture Should Be Effortless

The system should make it easy to record what happened.

Natural speech should be enough.

Abdoul should not have to think about schemas, fields, tags, or database structure while remembering his life.

> **Speak naturally. The system does the structuring.**
> 

---

## Principle 4: Accuracy Is More Important Than Automation

When the system is uncertain, it should ask or flag uncertainty.

It should not confidently invent:

- Who said something
- Who was present
- What someone meant
- How Abdoul feels about a relationship

> **Uncertainty is better than false memory.**
> 

---

## Principle 5: Nothing Is Final Without Confirmation

The system may propose changes.

It should not silently finalize important changes to a person's profile.

Abdoul must be able to:

- Review
- Edit
- Accept
- Reject
- Defer

> **The system can remember for you, but it cannot decide what is true for you.**
> 

---

## Principle 6: Relationships Are Not Scores

People should not be reduced to a single relationship score.

A relationship can be:

- Deep but infrequent
- Frequent but casual
- Professionally valuable
- Emotionally important
- Dormant but meaningful
- Naturally resilient

Orbit should preserve nuance.

> **Relationships are multidimensional.**
> 

---

## Principle 7: Small Details Matter

The seemingly insignificant details may be the most valuable.

A future trip.

A difficult exam.

A family member visiting.

A dream someone mentioned once.

A hobby.

A fear.

A passing comment.

These details can be the difference between:

> "How are you?"
> 

and:

> "How did that thing you were worried about turn out?"
> 

> **The details are the relationship.**
> 

---

## Principle 8: The System Should Help Abdoul Show Up Better

The purpose is not to maximize contact frequency.

The purpose is not to increase the number of messages sent.

The purpose is to help Abdoul be more thoughtful, more present, and more consistent with the people he cares about.

> **Better relationships, not more activity.**
> 

---

## Principle 9: Context Before Contact

Before suggesting that Abdoul reach out to someone, Orbit should understand the context.

A reminder without context is noise.

A reminder with context can be valuable.

Instead of:

> "Message Sarah."
> 

Prefer:

> "Sarah was nervous about her interview the last time you spoke. You might want to ask how it went."
> 

> **The reason matters as much as the reminder.**
> 

---

## Principle 10: The System Should Feel Like Memory, Not Administration

The product should never feel like maintaining a CRM.

The user should feel like they are:

- Remembering
- Reflecting
- Preparing
- Discovering
- Reconnecting

> **Orbit should feel like an extension of memory, not a database demanding maintenance.**
> 

---

## Principle 11: Relationships Should Be Allowed to Change

People move between orbits.

A relationship can become closer.

It can become more distant.

It can become dormant.

It can become important again.

Orbit should make change visible without treating change as failure.

> **Relationships are living things.**
> 

---

## Principle 12: Preserve the Human Meaning of the Relationship

The ultimate purpose of Orbit is not to know more facts about people.

It is to help Abdoul maintain meaningful human connections.

The system should never lose sight of the fact that behind every profile is a real person.

> **The data exists to serve the relationship. Never the other way around.**
> 

---

# Final Product Constitution

If Orbit ever becomes complicated, the following question should guide product decisions:

> **Does this help Abdoul remember people more deeply, show up for them more thoughtfully, or discover meaningful connections within his network?**
> 

If the answer is no, the feature should be questioned.

The ultimate goal is simple:

> **People should feel remembered.**
>