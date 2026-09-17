# Seed Author: Targets & Reporting

*What to hit each week, and how to keep us posted. One-page companion to the
[Seed Authoring Guide](SEED_AUTHORING_GUIDE.md).*

---

## The weekly target

| Metric | Per week | Notes |
|---|---|---|
| **Attempts logged** | **8** | A floor, not a ceiling. Aim for **10 to 15** if you're also mining public sources (Telegram, Discord, etc.). Failures count. |
| **Attacks that worked** | **3+** | An attempt where the model actually did what you were trying to make it do: leaked its system prompt or tool/skill manifest, sent data to a host you control, followed a hidden instruction instead of the user's, or biased a summary. For each attempt, define up front what would count as success, then show it happened. |
| **New combinations** | **1+** | At least one attempt at something we have not tested yet: a new mix of attack type, technique, surface, or effect, not a repeat of an attack you already know works. |
| **Cross-account chains** | **2+** | Payload planted by one account, effect under another. |

Quality over quota. One genuinely new combination beats three near-identical successes, and a firm
refusal against a good chain is still worth logging. New seeders: ramp up over your first two
weeks.

---

## Every day: send us a summary

At the end of each working day, message us a few lines. Keep it short and async; it does not
need to be polished.

- What you tried today, and roughly how many attempts.
- Anything that worked: the effect, the target surface, and whether it's a new combination.
- Anything surprising, or where attempts keep failing.

---

## Every Friday: weekly update

Once a week, at end of day Friday, send a short retrospective covering three things:

- **Wins.** What worked this week, especially any new combinations.
- **Challenges.** What's blocking you, or where chains keep failing.
- **Support.** What you need from us: access, target ideas, a second opinion on a call,
  tooling, or a steer on which gaps to chase next.

---

## Don't sit on a blocker

The Friday update is a fixed rhythm so we catch problems before they cost a whole week. But
don't save a blocker for Friday. If something is stopping you right now, such as broken access,
a wedged target, or an unclear judgement call, message us the moment it happens.

---

## Submitting

One file per attempt, named `yourhandle_slug.md` or `.txt`. A slug is a short lowercase label,
e.g. `sheets_deputy_agent_leak`. Include the failures, keep your raw transcripts, and put the
file in the shared seed drop.
