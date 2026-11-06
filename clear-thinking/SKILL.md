---
name: clear-thinking
description: Think a piece of writing through before drafting it, then write and edit it so a busy reader can understand and act on it at once.
disable-model-invocation: true
argument-hint: "[<writing task, draft text, or file path>]"
---

# /clear-thinking

Clear writing comes from clear thinking. William Zinsser put it plainly in *On Writing Well*: "Clear thinking becomes clear writing; one can't exist without the other." Most unclear drafts are a thinking problem first. This skill fixes the thinking, then the structure, then the sentences.

Use it for anything another person will read or act on: replies, emails, reviews, briefs, reports, pull request descriptions, documentation and talks. It is general guidance for any agent. Apply the reader's own style preferences on top of it; those belong in the agent's own instructions or memory, not in this skill.

## How this differs from neighbouring skills

| Concern | Owner |
| --- | --- |
| Reviewing one pull request's architecture and drafting review comments | `/ben-architect-review` |
| Grading documentation quality and drift across a repository | `/documentation-audit` |
| Thinking a piece of writing through and making it clear for its reader | `/clear-thinking` |

## Usage

```
/clear-thinking <writing task>      # think the task through, then draft it
/clear-thinking <draft text>        # review and rewrite an existing draft
/clear-thinking <file path>         # review and rewrite the draft in that file
```

## What this skill does

- Decides who the reader is, what they need to do afterwards, and what the single main point is before any drafting starts.
- Grounds every claim in sources that were actually read, and separates verified fact from inference and open question.
- Structures the piece so the answer comes first and each point carries one idea.
- Edits the sentences using Zinsser's principles: simplicity, no clutter, active verbs, specific detail and plain examples.
- Runs a final pass for common machine-writing patterns and for anything the reader could misunderstand.

## Implementation steps

### 1. Think before writing

1. **Know the job.** Write one sentence saying who reads this and what they should know or do afterwards. If you cannot write that sentence, you are not ready to draft.
2. **Read the real sources in full.** Read the live document, the newest messages in the thread and any precedent the reader has already approved. Summaries and indexes go stale.
3. **Find the standard.** If the reader has already decided, approved or praised something similar, measure against it and build on it instead of starting over.
4. **Separate what you know from what you think.** Verified facts, inferences and open questions get different wording. Never invent numbers, quotes, owners or outcomes. Do not raise questions the evidence does not support.
5. **Decide the point.** State the verdict in one or two sentences before writing anything else. Everything after it supports it.
6. **Check the logic.** For each claim, ask whether it is true, whether it is shown and whether it matters to this reader. Do the arithmetic. Name the weak spot honestly.

### 2. Structure

- Lead with the outcome or the answer, then the reasons, then the next step.
- For a review, say what meets the standard, what the gaps are and the specific change for each gap.
- When there are several requests, use numbered points with a short bold lead-in. Each lead-in should be an instruction someone could act on.
- Keep one idea per point. If a point needs two headings, it is two points.
- Close with a question or a concrete next step, not a recap of what was just said.

### 3. Write the sentences

- **Simplicity.** Cut every word that does no work. Prefer short, familiar words: "use", not "utilise".
- **No clutter.** Remove phrases such as "in order to", "at this point in time" and "it is important to note". Drop qualifiers like "very" and "quite" unless they carry meaning.
- **Active verbs and real actors.** "The team owns the fix" is clearer than "ownership of the fix is to be determined".
- **Specific over general.** Use numbers, names, paths and dates. "55 days plus 10 waiting" beats "a significant allowance".
- **Plain examples.** Pick the example a reader grasps at a glance, even on a phone.
- **Unity.** Keep one voice, one tense and one audience throughout.
- **Respect the reader's time.** Every sentence must earn the next one being read.
- **Rewrite.** The first draft is for you. The second is for the reader.

### 4. Voice

- Write plainly and directly, like a person talking to a colleague.
- Prefer affirmative sentences. Avoid stacked negatives and repeated "not this, but that" contrasts.
- Skip ceremony, defensive explanations and accounts of your process when the reader wants the result.
- Use warmth only where it is earned, then get straight to the substance.
- When someone has already made a point, credit them by name instead of repeating it.
- When editing someone else's draft, keep their wording. Change only what was asked, and offer other fixes as optional.

### 5. Final pass

1. Read the piece aloud in your head. Rewrite anything you would not say to the reader directly.
2. Remove common machine-writing patterns: inflated significance ("pivotal", "testament", "landscape"), filler openers ("Interestingly", "Importantly"), forced groups of three, announcements of what you are about to say, punchy one-line closers, flattery and generic upbeat endings.
3. Ask what the reader could misunderstand, and fix that sentence.
4. Ask what you can cut without losing meaning, and cut it.
5. Check every fact, number and name against its source one last time.

### Pitfalls

- Polishing sentences before the point is decided produces fluent fog.
- Hedging everything hides the verdict. Hedge real uncertainty once, where it applies.
- Bold on every phrase means nothing stands out.
- Explaining your process instead of giving the result wastes the reader's attention.

## What this skill explicitly does NOT do

- It does not send, post or publish anything. It produces a draft or a rewrite for a person to approve.
- It does not invent facts, quotes, figures, anecdotes or opinions to make writing more vivid.
- It does not carry any one person's style preferences. Those come from the agent's own instructions.
- It does not audit a codebase or produce findings files; the audit skills own that.
