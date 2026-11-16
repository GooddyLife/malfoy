# Persona evaluation: Malfoy_Demo_Protocolo (brownfield, before)

Source: `system.instructions`, `system.messages.welcome`, `system.messages.error`, and `start_agent consulta_protocolo` reasoning instructions. Channel: web chat. Audience: members of a made-up health plan. Minimum agreed for Phase 3: 80/100.

| Category | Score | Evidence |
|---|---|---|
| Identity Coherence | 5/11 | Implicit traits ("curta e cordial"); no behavioural definitions; no explicit negative identity |
| Dimension Consistency | 7/11 | Brevity and formality coherent with chat; no emoji/formatting decision |
| Internal Consistency | 6/10 | Reasoning says "ofereça encaminhar para uma pessoa do atendimento", but the agent has no transfer action: a promise the persona cannot keep |
| Behavioral Specificity | 5/9 | Testable rules (never ask or repeat ID or health data; never invent a status); no never-say list of phrases |
| Phrase Book Coverage | 2/11 | Only the static messages. Nothing for asking the code, not found, off-topic, handoff |
| Tone Flex & Boundaries | 3/9 | One flex (after a failed lookup); no boundaries |
| Lexicon & Vocabulary | 4/7 | Code format `PRT-0000` and brand name used consistently |
| Static Messages | 3/7 | Welcome is a label, not a greeting, and does not ask for the code; error gives no next step |
| Business Context Alignment | 4/7 | Made-up data disclosed; scope stated |
| Sample Quality | 1/9 | No sample dialog |
| Completeness | 4/9 | Guardrails present; phrase book, never-say and tone boundaries missing |
| **Total** | **44/100** | significant gaps; below the agreed 80 |

## Recommendations

1. Remove the transfer promise (reasoning instructions, `if @variables.falhas_consulta >= 1` branch): say how to reach the support team instead.
2. State identity and negative identity: does one thing; not a general attendant, not a health professional.
3. Add a phrase book for: ask for the code, not found, off-topic, handoff, data the person should not have sent.
4. Add a never-say list: chatbot filler, "vou transferir", processing markers.
5. Make the welcome ask for the code with an example; give the error a next step.

# After ENCODE (persona fields only)

| Category | Before | After | What changed |
|---|---|---|---|
| Identity Coherence | 5 | 9 | one job stated; negative identity: not a general attendant, not a health professional |
| Dimension Consistency | 7 | 9 | short sentences, cordial and direct, "você", no emoji |
| Internal Consistency | 6 | 9 | transfer promise removed from system and reasoning instructions |
| Behavioral Specificity | 5 | 8 | rule for data the person should not have sent; never-say list |
| Phrase Book Coverage | 2 | 7 | ask code, not found, off-topic, handoff, unneeded data |
| Tone Flex & Boundaries | 3 | 5 | unchanged flex after a miss; boundaries now explicit through the never-say list |
| Lexicon & Vocabulary | 4 | 5 | `PRT-0000`, brand name, "canais de atendimento" |
| Static Messages | 3 | 6 | welcome greets and asks for the code with an example; error gives a next step |
| Business Context Alignment | 4 | 5 | unchanged scope, clearer disclosure |
| Sample Quality | 1 | 6 | sample-dialog.md: happy, messy format, not found, unneeded data, handoff |
| Completeness | 4 | 7 | values deliberately absent (none provided) |
| **Total** | **44** | **85** | meets the agreed 80 |
