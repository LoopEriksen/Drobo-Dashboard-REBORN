# Documents

The project's documents, grouped by who they are for (each folder's own
README describes that folder). If you read only one, read
[`../ROADMAP.md`](../ROADMAP.md) (the plan) or
[`capabilities.md`](capabilities.md) (what works today).

## For you

| Document | What it is |
|---|---|
| [`../ROADMAP.md`](../ROADMAP.md) | The plan of record: the six phases, dated decisions, what is done and what is blocked |
| [`capabilities.md`](capabilities.md) | What the software does and how far each part has got, checked against the code |
| [`development-history.md`](development-history.md) | How it was built, in order, including the wrong turns |
| [`../CHANGELOG.md`](../CHANGELOG.md) | What changed between releases |
| [`../CREDITS.md`](../CREDITS.md) | Other people's work this project relies on, and why it was trusted |
| [`accessing-your-files.md`](accessing-your-files.md) | Why Windows 11 refuses the Drobo's shared folders, and the fix. The Windows app opens this page from its Tools tab, so never move or rename it |
| [`hardware-session.md`](hardware-session.md) | The maintainer's own checklist for what only a real Drobo can settle, step by step: create and delete a throwaway share from the app, record the original Dashboard turning its Blink Lights on and off, showing its event log and creating and deleting a throwaway user, read one permission code, and blink the lights from the app at the 900 Dashboard sends. What to send back, and what never to touch |
| [`running-the-agent.md`](running-the-agent.md) | Keeping the agent running in the background, even with nobody logged in, and reaching it from a phone. Optional: starting the app from a double-click launcher or the installed shortcut starts the agent too, for as long as its window stays open. Parts of it were written before the app and installer existed |
| [`../CLAUDE.md`](../CLAUDE.md) | Working notes for Claude sessions; not needed day to day |

## For testers

The README and the testers' guide point to these by path, so do not rename
or move them.

| Document | What it is |
|---|---|
| [`testers-tasks.md`](testers-tasks.md) | The jobs that need hardware this project does not have, with read-only steps |
| [`capabilities.md`](capabilities.md) | What works today |
| [`development-history.md`](development-history.md) | How it was built |
| [`../CHANGELOG.md`](../CHANGELOG.md) | What changed between releases |
| [`../windows/installer/TESTING.md`](../windows/installer/TESTING.md) | Installing, first run, reporting problems and removing the program |

## Protocol research

For developers and Claude sessions.

| Document | What it is |
|---|---|
| [`protocol-map.md`](protocol-map.md) | The protocol reference. Read this first. Code comments quote its section titles, so do not retitle them |
| [`command-surface.md`](command-surface.md) | Lab notebook of the command sweeps, in dated sections: what the Drobo answered, and what it did not |
| [`crypto-handshake.md`](crypto-handshake.md) | Research on a cryptographic exchange the Drobo performs. Its first conclusion was withdrawn (see its opening paragraph); what the exchange is for is still an open question |
| [`patents.md`](patents.md) | Protocol facts taken from Drobo's patents |
| [`original-dashboard-installer-notes.md`](original-dashboard-installer-notes.md) | Notes on Drobo's own Dashboard 3.5.0 installer. This project's installer is in [`../windows/installer/`](../windows/installer/) |

## Folders

| Folder | What it holds |
|---|---|
| [`archive/`](archive/README.md) | Finished or superseded documents, kept for the record. Nothing there describes the software as it is today |
| [`captures/`](captures/README.md) | Packet captures, kept only on your PC; git ignores them. Read its README before handling one: captures can contain the Drobo password |

## Not in git

The original blueprint was a Word document and is not tracked: git ignores
Word, Excel and PDF files because they carry their author's name.
[`archive/BLUEPRINT.md`](archive/BLUEPRINT.md) replaces it.
