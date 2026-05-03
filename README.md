# livechat-mcp

A Model Context Protocol (MCP) server that lets you have a continuous voice
conversation with your AI coding assistant. You speak, your speech is
transcribed locally with Whisper, and each utterance is delivered to the
assistant as if you'd typed it. No tab switching, no copy/paste, no batch
recording.

Works with any MCP host. First-class support for:

- **Claude Code**
- **Codex CLI**
- **Gemini CLI**

## Requirements

- macOS (tested) — Linux likely works, Windows untested.
- Python 3.10+
- An MCP host installed (Claude Code, Codex, Gemini, etc.)
- A working microphone
- ~500 MB disk for Whisper model cache + dependencies
- [`uv`](https://docs.astral.sh/uv/) for project management (recommended)

## Setup (macOS)

### 1. Install portaudio

`sounddevice` needs portaudio.

```bash
brew install portaudio
```

### 2. Install `uv` if you don't have it

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 3. Clone and install dependencies

```bash
cd livechat-mcp
uv sync
```

This will create `.venv/` and install `mcp`, `faster-whisper`, `sounddevice`,
`silero-vad`, `torch`, etc.

### 4. Run the setup wizard

The fastest way to install for one or more hosts is the included wizard:

```bash
install -m 0755 bin/livechat-skill ~/.local/bin/livechat-skill
livechat-skill setup
```

The wizard will:

1. Ask which assistants to install for (Claude Code / Codex / Gemini, any combination).
2. Copy the `/livechat` and `/endlivechat` slash commands to each host's
   commands directory (in the right format — Markdown for Claude Code & Codex,
   TOML for Gemini).
3. Register the MCP server in each host's config file.
4. Walk you through the tunable env vars (silence threshold, Whisper model,
   etc.) — press Enter to keep defaults.

Make sure `~/.local/bin` is on your `PATH` (it already is if you used the
official `uv` installer).

If you'd rather wire things up by hand, the manual steps for each host are
below.

### 5. Grant microphone permission

The first time the server tries to capture audio, macOS will prompt your
**terminal app** (Terminal, iTerm, Ghostty, Warp, etc.) for mic access. If
you miss the prompt, enable it manually:

> System Settings → Privacy & Security → Microphone → enable for your terminal

If you skip this, audio capture silently returns silence and nothing will
ever transcribe.

### 6. Pre-download the Whisper model (optional)

The first run downloads `base.en` (~150 MB). You can pre-warm it:

```bash
uv run python -c "from faster_whisper import WhisperModel; WhisperModel('base.en', device='cpu', compute_type='int8')"
```

## Manual install (skip if you used `livechat-skill setup`)

### Claude Code

Copy the slash commands:

```bash
mkdir -p ~/.claude/commands
cp commands/livechat.md ~/.claude/commands/
cp commands/endlivechat.md ~/.claude/commands/
```

Register the MCP server:

```bash
claude mcp add livechat -- uv --directory "$(pwd)" run livechat-mcp
```

Or edit `~/.claude.json` directly:

```json
{
  "mcpServers": {
    "livechat": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/livechat-mcp", "run", "livechat-mcp"]
    }
  }
}
```

### Codex CLI

Copy the slash commands:

```bash
mkdir -p ~/.codex/prompts
cp commands/livechat.md ~/.codex/prompts/
cp commands/endlivechat.md ~/.codex/prompts/
```

Register the MCP server in `~/.codex/config.toml`:

```toml
[mcp_servers.livechat]
command = "uv"
args = ["--directory", "/absolute/path/to/livechat-mcp", "run", "livechat-mcp"]
```

### Gemini CLI

Gemini uses TOML for custom commands. The wizard generates these for you;
to do it by hand, see `commands/gemini/livechat.toml.template` (created by
running `livechat-skill setup` once).

Register the MCP server in `~/.gemini/settings.json`:

```json
{
  "mcpServers": {
    "livechat": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/livechat-mcp", "run", "livechat-mcp"]
    }
  }
}
```

## Usage

Open your assistant's CLI in any terminal:

```bash
claude    # or: codex    or: gemini
```

Then in the assistant prompt:

```
/livechat
```

The assistant will call `get_voice_input` and start listening. **Speak
normally.** When you pause for ~1.5 seconds, your utterance is finalized,
transcribed, and sent as a prompt. The assistant responds, then immediately
listens for the next utterance.

While the assistant is generating a response, the mic is still hot — anything
you say during that time queues up and is delivered all at once on the next
`get_voice_input` call.

### Ending a session

Three ways:

1. **`/endlivechat`** — cleanest, runs from the assistant prompt. (You'll
   need to interrupt the current turn first if it's mid-response.)
2. **Wake phrase** — say `terminate voice session now`. The transcription
   triggers shutdown. The phrase is intentionally awkward to avoid collisions
   with real review content. Configurable via `LIVECHAT_END_PHRASE`.
3. **Ctrl+C** — kills the MCP server. The assistant will see a tool error on
   the next call and stop the loop.

## Configuration

All tunables live in `livechat_mcp/config.py` and can be overridden via env vars:

| Var                          | Default                          | Notes                                                       |
| ---------------------------- | -------------------------------- | ----------------------------------------------------------- |
| `LIVECHAT_WHISPER_MODEL`     | `base.en`                        | `tiny.en`, `base.en`, `small.en`, `medium.en`               |
| `LIVECHAT_WHISPER_DEVICE`    | `auto`                           | `cpu`, `cuda`, `auto`                                       |
| `LIVECHAT_WHISPER_COMPUTE`   | `int8`                           | `int8` (CPU), `float16` (GPU)                               |
| `LIVECHAT_SILENCE_SEC`       | `1.5`                            | Silence after speech to end an utterance                    |
| `LIVECHAT_VAD_THRESHOLD`     | `0.5`                            | Silero VAD speech probability threshold                     |
| `LIVECHAT_MIN_UTTERANCE_SEC` | `0.4`                            | Minimum utterance length (filters coughs)                   |
| `LIVECHAT_MAX_UTTERANCE_SEC` | `120`                            | Force-cut runaway utterances                                |
| `LIVECHAT_LONG_POLL_SEC`     | `300`                            | How long `get_voice_input` blocks before `__NO_INPUT__`     |
| `LIVECHAT_END_PHRASE`        | `terminate voice session now`    | Spoken phrase to end the session                            |
| `LIVECHAT_DEBUG`             | unset                            | Set to `1` for VAD/segmentation debug logs to stderr        |

The easy way to set these is `livechat-skill set KEY VALUE` — it edits the
`env` block in every host config it finds (Claude / Codex / Gemini).

```bash
livechat-skill show           # print current env block(s)
livechat-skill set LIVECHAT_SILENCE_SEC 1.5
livechat-skill unset LIVECHAT_DEBUG
```

Restart your assistant CLI after any change — MCP env vars are read by the
server at startup.

To do it manually, edit the `env` field of the livechat MCP entry in each
host's config. Example for Claude Code:

```json
{
  "mcpServers": {
    "livechat": {
      "command": "uv",
      "args": ["--directory", "/abs/path", "run", "livechat-mcp"],
      "env": {
        "LIVECHAT_WHISPER_MODEL": "small.en",
        "LIVECHAT_DEBUG": "1"
      }
    }
  }
}
```

## Troubleshooting

**Nothing happens when I speak.**
Check (in order): mic permission for your terminal app, mic input level
(System Settings → Sound), set `LIVECHAT_DEBUG=1` and watch stderr for VAD
events, lower `LIVECHAT_VAD_THRESHOLD` to `0.3`.

**Transcriptions are inaccurate.**
Upgrade model: `LIVECHAT_WHISPER_MODEL=small.en` or `medium.en`. `medium.en`
is noticeably slower on CPU (still real-time-ish) but much better for
technical vocabulary.

**Utterance ends too quickly / too slowly.**
Tune `LIVECHAT_SILENCE_SEC` (or run `livechat-skill set LIVECHAT_SILENCE_SEC 1.5`).
1.0–4.5 is the useful range — lower feels snappier but risks cutting
mid-thought pauses.

**`uv` not found.**
Either install uv (recommended) or change the MCP config `command` to a
direct invocation of `python -m livechat_mcp.server` from inside an activated
venv.

**The server starts but the assistant never calls the tool.**
Make sure `/livechat` was invoked. Without the slash command, the assistant
has no instruction to enter the loop.

**Server logs go into the assistant's UI as garbage / break the protocol.**
This shouldn't happen — all server logging goes to stderr. If you see it,
file a bug. Make sure you have not added any `print(...)` statements without
`file=sys.stderr`.

**`portaudio` errors on startup.**
Install it: `brew install portaudio`. If it's installed and still failing, try
`brew reinstall portaudio` and reinstall sounddevice: `uv sync --reinstall`.

## How it works (short version)

```
[mic] → [Silero VAD] → [Whisper] → [queue] ← [get_voice_input tool] ← [Assistant]
   ↑________background thread, always running________↑
```

The audio pipeline is decoupled from the MCP tool, so the mic is *always* hot
while the server is up. Utterances spoken while the assistant is generating
a response are queued and delivered on the next tool call.

## License

MIT.
