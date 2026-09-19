# Getting Started

## First run

```bash
blackvoice
```

A tray icon appears — a white plate with a black waveform. Black Voice is now
listening for its wake word.

If you have no system tray (some GNOME setups need the AppIndicator extension),
the popup overlay still works and you can trigger it from the terminal.

### Terminal only

```bash
blackvoice run --no-ui
```

No tray, no overlay, everything printed to the terminal. This is what to use
over SSH, on a server, or when you are debugging.

### Without speaking at all

```bash
blackvoice text                       # interactive prompt
blackvoice text "open firefox"        # one command and exit
```

Text mode runs the same router and the same skills as the voice path — only the
microphone is skipped. It is the fastest way to check whether a phrase is
understood before you try saying it.

## The three ways to get its attention

**Say the wake word.** “Black”, then your command. You can pause between them, or
run them together: *“Black, open firefox”*.

**Click the tray icon.** It starts listening immediately, no wake word needed.

**Type it.** Tray menu → *Type a command…*, or `blackvoice text`.

There is no global hotkey built in, because every desktop environment grabs keys
differently and a half-working one is worse than none. Bind
`~/.local/bin/blackvoice text` in your desktop's keyboard settings if you want
one.

## What the states mean

The tray icon and the overlay both show what Black Voice is doing:

| State | Meaning |
|---|---|
| `ready` | Idle, waiting for the wake word |
| `listening…` | Capturing your command — the waveform moves with your voice |
| `thinking…` | Running a skill |
| `speaking` | Talking back |
| `asleep` | Ignoring the wake word until you ask it to wake up |

Say **“go to sleep”** to stop it listening; click the tray icon to wake it
again.

## Your first commands

Start with things that cannot go wrong:

```
black, what time is it
black, battery
black, system info
black, disk space
```

Then something that does work:

```
black, open firefox
black, volume 40
black, screenshot
```

See **[Voice Commands](Voice-Commands)** for the full list, or just say:

```
black, help
```

## When it asks you a question

Anything that could change your system asks first:

```
  you    black, shut down
  black  Should I really shut down? Say yes to confirm.
  you    yes
```

“Yes” and “ok” both confirm. “No” and “stop” both cancel. A confirmation
expires after 30 seconds, and saying anything else cancels it — so an ignored
prompt never fires later by accident.

Read **[Security Model](Security-Model)** to see exactly which commands ask,
which run straight away, and which are refused outright.

## Asking questions

Anything the command rules do not recognise becomes a question for the AI
backend:

```
  you    black, why is the sky blue
  black  Sunlight scatters off air molecules, and blue scatters most.
```

This needs a backend configured. The default is [Ollama](https://ollama.com),
which runs locally:

```bash
ollama pull llama3.2
```

Claude and OpenAI are also supported. See
[Configuration → AI backend](Configuration#ai--the-question-answering-backend). Set `ai.provider` to
`"none"` to switch question answering off entirely — unrecognised commands then
simply say so.

## Checking your setup

```bash
blackvoice doctor      # what is installed, what is missing
blackvoice devices     # list microphones
blackvoice say "test"  # check speech output
```

If the wake word never fires, `doctor` will tell you whether the models are
missing. If it hears you but does the wrong thing, use `blackvoice text` to see
how the phrase is being routed.

## Next

→ **[Voice Commands](Voice-Commands)** — everything it understands
→ **[Configuration](Configuration)** — make it yours
