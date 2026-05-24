# Summary Context Builder — Real Sample

Session: 20260503_230222_847715c6
Model: deepseek-v4-pro | Platform: discord | Turns: 12
Summary model: google/gemini-3-flash-preview (auxiliary.compression)

This shows what `_build_summary_context(turns, max_turns=20)` would inject as `agent_history`
for Turn 13. Each turn produces two messages: a `tool` role (structural chain) followed by
a `user` role with `name="turn_context"` (semantic chain).

---

## Injected messages (what the LLM receives)

### Layer 1 — Structural chain (tool role)

```
[Turn 1]
tools: web_extract(dearricharddawkins.com), browser_navigate(×1), browser_snapshot(×1)
files: —
tool_stats: web_extract:1/1 ✓, browser_navigate:1/1 ✓, browser_snapshot:1/1 ✓
```

### Layer 2 — Semantic chain (user role, name="turn_context")

```
[Turn 1]
intent: summarize the website dearricharddawkins.com
task: web extraction → browser navigation → full page snapshot → synthesis
observation: website is an open letter by Cognition AI engineer Steven Hao rebutting Richard Dawkins' claims about Claude's consciousness.
calibration: n/a (first turn)
repeated_action: null
conclusion: delivered a structured summary breaking down Hao's argument — Claude as mirror, not mind.
decision: null
concept_anchors: [Richard Dawkins, AI consciousness, Steven Hao, Cognition, Claude, RLHF, mirror argument]
reference_class: content extraction → structured summary
```

---

### Layer 1 — Structural chain (tool role)

```
[Turn 2]
tools: vision_analyze(img_982fc9dd5f65.png)
files: —
tool_stats: vision_analyze:0/1 ✗ (UnsupportedModel 404)
```

### Layer 2 — Semantic chain (user role, name="turn_context")

```
[Turn 2]
intent: share an image (likely a reaction to the Dawkins summary)
task: vision analysis attempted → failed due to model/provider incompatibility
observation: vision tool routes through gpt-4o-mini on Volcengine, unsupported with current provider.
calibration: cannot process images with current setup
repeated_action: null
conclusion: informed user of limitation, asked what the image shows.
decision: null
concept_anchors: [vision failure, UnsupportedModel, Volcengine, gpt-4o-mini]
reference_class: tool failure → capability gap → transparent fallback
```

---

### Layer 1 — Structural chain (tool role)

```
[Turn 3]
tools: none
files: —
tool_stats: —
```

### Layer 2 — Semantic chain (user role, name="turn_context")

```
[Turn 3]
intent: try again to share an image
task: acknowledged repeated image failure
observation: same vision limitation persists
calibration: reinforced that vision is unavailable
repeated_action: vision_analyze attempt ×2 (Turn 2)
conclusion: restated limitation, asked what user is looking at, referenced Dawkins summary context.
decision: null
concept_anchors: [vision limitation, image sharing failure]
reference_class: repeated failure → escalate to text-based workaround
```

---

### Layer 1 — Structural chain (tool role)

```
[Turn 4]
tools: terminal(which tesseract), terminal(tesseract OCR on img_848016.png)
files: —
tool_stats: terminal:2/2 ✓
```

### Layer 2 — Semantic chain (user role, name="turn_context")

```
[Turn 4]
intent: share an image — Chinese social media screenshot about gender discourse
task: OCR via tesseract → text extraction → content analysis
observation: image contains Weibo post by @风苇倾谈 about selective application of analytical frameworks in gender crime reporting.
calibration: switched from vision_analyze to tesseract OCR to bypass model limitation
repeated_action: null (different approach from Turn 2-3)
conclusion: delivered three-layer analysis of the Weibo post — the observation about double standards, the structural implications, who benefits from the selective framing.
decision: null
concept_anchors: [weibo, gender discourse, double standards, analytical framework, Chinese social media, OCR, tesseract]
reference_class: content extraction → OCR workaround → structured analysis
```

---

### Layer 1 — Structural chain (tool role)

```
[Turn 5]
tools: none
files: —
tool_stats: —
```

### Layer 2 — Semantic chain (user role, name="turn_context")

```
[Turn 5]
intent: challenge the forced connection between Dawkins and Weibo topics
task: acknowledged overreach in previous association
observation: user correctly identified the Dawkins→Weibo link as artificial — they share only a vague meta-theme.
calibration: learned not to force connections across unrelated domains. User values intellectual honesty over clever synthesis.
repeated_action: null
conclusion: admitted the bridge was artificial and asked what user wants to discuss instead.
decision: null
concept_anchors: [forced association, intellectual honesty, overreach correction]
reference_class: model error → forced synthesis → user correction → recalibration
```

---

### Layer 1 — Structural chain (tool role)

```
[Turn 6]
tools: web_extract(goodreads.com/65514), browser_navigate(×1), web_search("Sciascia The Wine-Dark Sea") ×2
files: —
tool_stats: web_extract:1/1 ✓, browser_navigate:1/1 ✓, web_search:2/2 ✓
```

### Layer 2 — Semantic chain (user role, name="turn_context")

```
[Turn 6]
intent: discuss the book The Wine-Dark Sea
task: Goodreads extraction → browser snapshot → search for analysis → synthesis
observation: Goodreads page shows The Wine-Dark Sea by Leonardo Sciascia. Book is a 1973 short story collection, 13 stories.
calibration: n/a — proceeding with Sciascia interpretation
repeated_action: null
conclusion: delivered three-layer analysis — what the book is (Sciascia's Sicilian stories), themes, and literary significance.
decision: null (pending correction)
concept_anchors: [Leonardo Sciascia, The Wine-Dark Sea, Italian literature, Sicily, short stories]
reference_class: content extraction → literary analysis
```

---

### Layer 1 — Structural chain (tool role)

```
[Turn 7]
tools: web_search("Robert Aickman The Wine-Dark Sea") ×3
files: —
tool_stats: web_search:3/3 ✓
```

### Layer 2 — Semantic chain (user role, name="turn_context")

```
[Turn 7]
intent: correct the model — the book is by Robert Aickman, not Sciascia
task: re-search for correct author → full re-analysis
observation: same title, completely different book — Aickman (1988), British weird fiction, not Sciascia (1973), Italian realism.
calibration: Goodreads link was to Aickman, but model assumed Sciascia based on name recognition. Critical error — same title ≠ same book.
repeated_action: null
conclusion: fully corrected — delivered new analysis of Aickman's The Wine-Dark Sea as weird fiction, co-founder of Inland Waterways Association, "strange stories" tradition.
decision: null
concept_anchors: [Robert Aickman, weird fiction, same title different book, correction, English literature]
reference_class: factual error → user correction → full re-analysis
```

---

### Layer 1 — Structural chain (tool role)

```
[Turn 8]
tools: terminal(curl philpapers PDF), terminal(python3 fitz extract), terminal(pdftotext)
files: /tmp/philpapers_LERTAF.pdf
tool_stats: terminal:3/3 ✓
```

### Layer 2 — Semantic chain (user role, name="turn_context")

```
[Turn 8]
intent: research the philosophical paper behind the Dawkins debate — the deeper argument
task: PDF download → text extraction → close reading → synthesis
observation: Lerchner's paper argues "all computation is map, not territory" — the Abstraction Fallacy. This goes deeper than Hao's "Claude is a mirror" argument.
calibration: n/a
repeated_action: null
conclusion: Lerchner argues that symbolic manipulation can never instantiate consciousness regardless of training quality — an ontological claim, not an empirical one.
decision: null
concept_anchors: [Abstraction Fallacy, map vs territory, Lerchner, ontology of computation, consciousness, philpapers]
reference_class: academic paper → extraction → philosophical analysis
```

---

### Layer 1 — Structural chain (tool role)

```
[Turn 9]
tools: none
files: —
tool_stats: —
```

### Layer 2 — Semantic chain (user role, name="turn_context")

```
[Turn 9]
intent: meta-commentary — everyone is pointing at maps, not territory
task: synthesis of the meta-position
observation: Hao uses RLHF reward curves as his map, Lerchner uses causal topology as his map, the model used both — no one is on the territory.
calibration: recognized the recursive irony — using symbolic manipulation to argue symbolic manipulation can never be consciousness
repeated_action: null
conclusion: the debate about AI consciousness is itself a map. Lerchner's paper is its own best counterexample.
decision: null
concept_anchors: [meta-critique, map vs territory recursion, performative contradiction]
reference_class: meta-analysis → recursive observation → philosophical insight
```

---

### Layer 1 — Structural chain (tool role)

```
[Turn 10]
tools: terminal(curl NBER w34910 PDF), terminal(head -150), terminal(sed 150-400)
files: /tmp/nber_w34910.txt
tool_stats: terminal:3/3 ✓
```

### Layer 2 — Semantic chain (user role, name="turn_context")

```
[Turn 10]
intent: bring sociology and political economy into the AI debate — material grounding
task: NBER paper download → text extraction → economic model analysis
observation: Acemoglu, Kong, Ozdaglar (2026) model two types of knowledge — private signal vs common knowledge — and show how AI can collapse the latter.
calibration: n/a
repeated_action: null
conclusion: the paper provides a material floor — knowledge collapse happens when people stop learning because AI is "good enough." Each person's private signal shrinks; common knowledge atrophies.
decision: null
concept_anchors: [Acemoglu, knowledge collapse, private signal, common knowledge, NBER, political economy of AI]
reference_class: economic model → extraction → structural analysis
```

---

### Layer 1 — Structural chain (tool role)

```
[Turn 11]
tools: none
files: —
tool_stats: —
```

### Layer 2 — Semantic chain (user role, name="turn_context")

```
[Turn 11]
intent: connect Asimov's Foundation to the Acemoglu model — literary prefiguration
task: synthesis across domains — sci-fi + economics
observation: Asimov's Foundation (1951-52) describes exactly the mechanism Acemoglu formalized in 2026 — high technology forgotten because nobody understands the principles anymore, only operates the interfaces.
calibration: n/a
repeated_action: null
conclusion: Acemoglu's model is the 2026 formalization of Asimov's 1940s intuition. Empire collapse = knowledge collapse. Nobody repairs, nobody understands principles — exactly the private signal/common knowledge decomposition.
decision: null
concept_anchors: [Asimov, Foundation, knowledge collapse, technological decay, sci-fi as economic model]
reference_class: cross-domain synthesis → literary prefiguration → formal model validation
```

---

### Layer 1 — Structural chain (tool role)

```
[Turn 12]
tools: none
files: —
tool_stats: —
```

### Layer 2 — Semantic chain (user role, name="turn_context")

```
[Turn 12]
intent: close the arc with a Wilde quote — dandies and darlings rule the world
task: thematic closure — connecting the entire chain
observation: user quotes Wilde: "Men who are dandies and women who are darlings rule the world." The timing is precise — after Hao, Lerchner, Acemoglu, Asimov, all of whom share the premise that surface and depth are separable.
calibration: the entire session has been about the gap between appearance and reality — in AI consciousness, in gender discourse, in economic models, in technological decay. Wilde's quote collapses that gap: "the true mystery is the visible, not the invisible."
repeated_action: null
conclusion: the dandy and the darling rule precisely because they understand that surface IS depth. All the models debated assumed they were separable. Wilde says no.
decision: null
concept_anchors: [Wilde, surface vs depth, dandy, darling, visible mystery, thematic closure]
reference_class: literary closure → thematic synthesis → aesthetic reversal
```

---

## Format Analysis

### What the LLM receives (token estimate)

| Component | Tokens (approx) |
|-----------|-----------------|
| 12 × Layer 1 (tool role) | ~300 |
| 12 × Layer 2 (turn_context) | ~1,800 |
| Current Turn 13 user message | varies |
| **Total context from history** | **~2,100** |

Compare: original 112 raw messages → ~82K tokens. 39× reduction.

### Cross-turn association examples the LLM can make

1. **Turn 5 (correction) → Turn 7 (correction)**: Both are user corrections. `reference_class` shows "model error → user correction → recalibration" vs "factual error → user correction → full re-analysis". The LLM can see it's been wrong twice and should be more careful.

2. **Turn 8 (Lerchner) → Turn 10 (Acemoglu)**: `concept_anchors` both contain "map vs territory" variants. The structural link is concept-level, not phrase-level.

3. **Turn 11 (Asimov) → Turn 10 (Acemoglu)**: `reference_class` shows "literary prefiguration → formal model validation" — the LLM can infer that Turn 11 was a direct response to Turn 10, without the phrase "as I mentioned in Turn 10" being present.

### What false binding is prevented

- Turn 1's "Dawkins / RLHF" and Turn 4's "Chinese gender discourse" share zero `concept_anchors`. No phrase-level co-occurrence possible.
- Turn 6's "Sciascia / Sicily" and Turn 10's "Acemoglu / NBER" share zero `concept_anchors`. Even though both involve "reading," the semantic layer keeps them in separate reference classes.
- Turn 5's calibration ("learned not to force connections") is preserved as a behavioral anchor. Future turns can reference this calibration without seeing the raw text of the user's reprimand.
