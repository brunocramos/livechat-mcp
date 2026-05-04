---
name: livechat
description: Start or manage a continuous live voice session using the livechat MCP server. Use this when the user asks to use livechat, start voice mode, start a voice review, talk instead of type, listen on the microphone, or end a livechat voice session.
metadata:
  short-description: Start live voice mode
---

# Livechat

Use the `livechat` MCP tools to run a continuous voice session.

## Start

1. Before calling any MCP tool, print exactly:

   `Listening — go ahead.`

2. Call `reset_voice_session` **once**, right after the announcement and before
   the first `get_voice_input`. The MCP server is long-lived; this clears any
   stale shutdown flag from a prior `/endlivechat` in the same process so the
   new session starts cleanly. On a fresh server it's a safe no-op. Do NOT
   call `reset_voice_session` again during the loop.
3. Call `get_voice_input`.
4. For real speech, echo the transcription before acting:

   `> "<exact text returned by get_voice_input>"`

5. Treat the transcription as the user's next request. Act on it normally.
6. After completing each request, immediately call `get_voice_input` again to continue the loop.

## Tool Results

- `__NO_INPUT__`: do not echo it. Call `get_voice_input` again.
- `__END_SESSION__`: do not echo it. Stop the loop and summarize the session briefly.
- `__ALREADY_RUNNING__:<pid>`: do not echo it. Tell the user another voice session is active and ask whether to take over from this window. If they agree, call `take_over_voice_session`; on `OK`, print `Listening — go ahead.` again and resume with `get_voice_input`.

## End

When the user asks to end livechat or stop voice mode, call `end_voice_session`. After calling this tool, **do not call `get_voice_input` again**. Instead, stop the loop and summarize what was accomplished.

Keep responses concise during voice mode. The user is actively speaking and expects the loop to continue until the session ends.
