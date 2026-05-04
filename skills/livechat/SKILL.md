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

2. Call `get_voice_input`.
3. For real speech, echo the transcription before acting:

   `> "<exact text returned by get_voice_input>"`

4. Treat the transcription as the user's next request. Act on it normally.
5. After completing each request, immediately call `get_voice_input` again.

## Tool Results

- `__NO_INPUT__`: do not echo it. Call `get_voice_input` again.
- `__END_SESSION__`: do not echo it. Stop the loop and summarize the session briefly.
- `__ALREADY_RUNNING__:<pid>`: do not echo it. Tell the user another voice session is active and ask whether to take over from this window. If they agree, call `take_over_voice_session`; on `OK`, print `Listening — go ahead.` again and resume with `get_voice_input`.

## End

When the user asks to end livechat or stop voice mode, call `end_voice_session`, then summarize what was accomplished.

Keep responses concise during voice mode. The user is actively speaking and expects the loop to continue until the session ends.
