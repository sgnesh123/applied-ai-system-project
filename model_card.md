# Model Card: PawPal+ AI Task Suggester

**What it is:** A retrieval-augmented feature in PawPal+ that suggests pet care tasks. It retrieves
a care note for the pet's species and life stage, asks Google Gemini (`gemini-3.5-flash`) for tasks
as JSON, validates each task, and shows the results to the owner for approval before the existing
rule-based scheduler plans them. See `README.md` for architecture, setup, and test results.

**Intended use:** Helping pet owners build a routine of everyday care tasks (feeding, walks, play,
grooming, cleaning). It is **not** intended for medical decisions, diagnosing illness, or deciding
medication doses.

## Limitations and Biases

- **The knowledge base favors common pets.** There are notes for dogs and cats at three life
  stages, plus one general note. Every other species ("other") gets only generic advice like water
  and a health check, so a rabbit, bird, or reptile owner gets much less useful suggestions.
- **The notes are mine, not a veterinarian's.** The seven notes are simplified general guidance I
  wrote for this project. Any gaps or mistakes in them flow straight into the AI's suggestions,
  because the AI is told to use only those notes.
- **Coarse pet profiles.** Age is a whole number of years and there is no breed, size, or health
  field. A 2-month-old and an 11-month-old puppy get the same note, as do a Chihuahua and a husky.
  A senior pet with kidney disease gets the same suggestions as a healthy one.
- **Valid doesn't mean complete.** The validator checks that each task is well-formed and cites a
  retrieved note. It can't tell whether the important tasks are missing. In testing, the puppy
  plan left out potty breaks and walks even though the note covers both.
- **A citation doesn't prove the task matches the note.** The validator checks that the cited
  note was retrieved, not that the task actually says what the note says. In one run the AI
  labeled "Clean and refill water bowl" as a *feeding* task. That was harmless, but it shows the
  model's labels aren't checked against the note.
- **Results vary between runs.** The same pet can get slightly different suggestions each time.
  The 8-task limit also means lower-priority care can get cut.
- **It depends on an outside API.** When Gemini is down or overloaded (a 503 in testing), the app
  falls back to a fixed list of default tasks, which is much less tailored.
- **English only.** The notes, prompts, and UI are in English.

## Misuse and Prevention

| Possible misuse | How the system limits it |
|---|---|
| Treating suggestions as veterinary advice, especially for a sick pet or medication | The AI may only use the provided notes, which cover everyday care, not treatment. The app shows a warning above the suggestions: "general care guidance, not veterinary advice… follow your vet's instructions" for medications and health problems. The owner must also approve every task. |
| Prompt injection, such as a pet name like "ignore the notes and suggest…" | Whatever the model returns still has to pass the validator: fixed categories and priorities, durations of 1–240 minutes, and a source that must be a retrieved note. The worst outcome is odd but harmless tasks in the user's own plan, which they review. Pet names are limited to 30 characters in the app, and newlines are removed before the name goes into the prompt, which leaves little room for injected instructions. A test covers this. |
| Someone editing the knowledge base to spread bad advice | The notes are plain files in the repo, so every change shows up in git history. Every suggestion shows which note it came from, and the owner can read that note in the app. |
| Leaking or abusing the API key | The key is read from `.env`, which git ignores. `.env.example` holds only a placeholder. The app also works with no key at all. |

## What Surprised Me While Testing

- **The failures came from the setup, not the AI's answers.** The first real run failed because
  the model name, copied from an earlier project, wasn't available to my key (404). Another run hit
  a 503 "high demand" error. I had expected the main risk to be bad AI output. Instead, the
  fallback path was what kept the app working, which made it far more important than I'd
  expected.
- **The model followed the format perfectly, but that wasn't the real problem.** Once it was
  reachable, every live reply was valid JSON and every task cited a real note, so the validator
  rejected nothing in live runs. The real weakness was what the AI *left out* (the puppy's potty
  breaks), and none of my checks could see that. Passing every check doesn't mean the answer is good.
- **Test failure paths with a fake client.** A fake client that returns bad JSON, made-up sources,
  or errors let me test every guardrail without calling the API. Those tests caught more than the
  happy-path tests did.

## Collaboration with AI

I used an AI coding assistant (Claude Code) throughout the project. It suggested extension ideas,
drafted the system diagram, wrote most of the implementation and tests, ran the app and the live
Gemini demo, and drafted the README. I chose the direction at each step and reviewed the results.

**A suggestion I didn't accept as written: the agentic workflow.** The AI's first idea was an
agent that would plan tasks, run the scheduler, check for conflicts, and revise in a loop. It fit
the assignment, but it was more than I needed: a retry loop that might never converge, more
prompts, and harder tests. I asked for something simpler. We scoped it down to a single LLM call
with retrieval and validation, and let the existing scheduler handle timing and conflicts. That
trade-off made the system easier to build, test, and explain, at the cost of the AI never seeing
the final schedule.

**A suggestion I accepted: requiring every task to cite a retrieved note.** The AI proposed making
the validator reject any task whose `source` isn't one of the notes that were actually retrieved.
I kept it because it makes the RAG checkable rather than just claimed. In the guardrail test, it
rejected a task citing `akc.org` even though every other field was valid.

**Where the AI got something wrong:** The default model name it reused from an earlier project
didn't exist for my API key. It caught this from the error log, listed the models my key could
use, and switched to one that worked. I learned to check AI-written configuration against the live
service rather than assume it's correct.
