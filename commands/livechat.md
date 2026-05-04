---
description: Start a live voice review session. Speak instead of typing.
---

You are entering **live voice review mode**. The user is speaking to you instead of typing.

## STEP 0 — announce the session BEFORE doing anything else

Your **very first output** in this session must be exactly this line, as plain text:

`Listening — go ahead.`

This is non-negotiable:

- Do NOT call any MCP tool before printing that line.
- Do NOT skip it, paraphrase it inside a tool result, or hide it inside reasoning.
- The user has **no other indicator** that the mic is hot. If you call `get_voice_input` first, the mic is already capturing audio but the user is staring at a blank screen wondering whether it's working. They will start a conversation that they can't tell you've heard.

So: emit the literal line `Listening — go ahead.` first, then proceed to STEP 1.

## STEP 1 — clear stale state once, before the loop

Right after the announcement and BEFORE the first `get_voice_input`, call the
`reset_voice_session` MCP tool exactly once. The MCP server is long-lived and
keeps state across slash commands, so a previous `/endlivechat` in this same
process leaves a shutdown flag set; without resetting, your first
`get_voice_input` call would return `__END_SESSION__` immediately and the loop
would never start. On a freshly-started server with nothing to reset, the call
is a safe no-op and returns "active; nothing to reset" — ignore the message and
keep going either way.

Do NOT call `reset_voice_session` again inside the loop.

## The loop

1. Call the `get_voice_input` MCP tool to receive the user's next utterance.
2. **Echo the transcription back to the user before doing anything else.** The user has no other way to see what Whisper heard — wrong transcriptions otherwise look like Claude misunderstanding. Format it as a markdown blockquote on its own line, exactly like:

   `> "<the exact text returned by the tool>"`

   Then on the next line, proceed with your response or action.
3. After echoing, treat the utterance as a normal user prompt and act on it — answer questions, edit files, run commands, whatever the utterance asks for.
4. After completing each action, **immediately call `get_voice_input` again**. Do not ask "anything else?" Do not wait for typed input. The loop is the entire point.
5. If the tool returns multiple utterances joined with ` / `, echo the full joined string in the blockquote, then treat them as related sequential requests from the same speaker.
6. If the tool returns the literal string `__NO_INPUT__`, the long-poll timed out with no speech. **Do not echo it** — it is not user speech. Call the tool again immediately.
7. If the tool returns the literal string `__END_SESSION__`, the user has ended the session. **Do not echo it.** Stop calling the tool, briefly summarize what was accomplished during the session, and stop.
8. If the tool returns a string starting with `__ALREADY_RUNNING__:` (e.g. `__ALREADY_RUNNING__:34291`), another livechat session is currently active in a different window (PID shown after the colon). **Do not echo it.** Tell the user briefly: "Another voice session is active (PID X). Take over from this window?" and wait for a typed yes/no — do NOT call `get_voice_input` again until the user replies (their mic is not yours yet). If the user agrees, call `take_over_voice_session`. On `OK`, **print the literal line `Listening — go ahead.` again** before doing anything else (the takeover gives no other indicator that the mic is now hot in this window), then immediately call `get_voice_input` to begin the loop. On any other return, surface the error and stop.

Keep your spoken-context responses tight: the user is reviewing code or otherwise actively engaged, so prefer making the requested change over long explanations. If something is ambiguous, make a reasonable choice and note it briefly rather than blocking on a clarifying question — the user will just tell you to redo it.

Begin: print `Listening — go ahead.` now, then call `reset_voice_session` once, then call `get_voice_input` to enter the loop.
