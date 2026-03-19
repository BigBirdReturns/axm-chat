# axm-chat

Turn Claude, ChatGPT, and Gemini conversation exports into cryptographically signed, queryable knowledge shards. Every claim traces to a byte range in the source. Verification runs offline, no network required.

**[→ axm-chat docs](https://bigbirdreturns.github.io/axm-chat/)**

---

## What it does

You export your conversations. axm-chat compiles them into shards — sealed archives where every factual claim is bound to the exact bytes it came from. You query in plain English. The answer shows you the evidence and lets you verify it cryptographically without leaving the shell.

The model only appears at distillation time. Query is deterministic SQL. Nothing leaves your machine.

---

## Run it

```bash
git clone https://github.com/BigBirdReturns/axm-genesis
git clone https://github.com/BigBirdReturns/axm-core
git clone https://github.com/BigBirdReturns/axm-chat
cd axm-chat
bash start.sh
```

`start.sh` installs the three packages in order, seeds the gold shard into `~/.axm/shards/`, and starts the server on `:8410`.

Then open `ui/axm-shell.html` in your browser.

**Requires:** Python 3.10+  
**Optional:** [Ollama](https://ollama.ai) for distillation (`ollama pull mistral`)

---

## Try it immediately

The gold shard (`fm21-11-hemorrhage-v1`) is seeded on first run — a field manual on hemorrhage control compiled from FM 21-11. No import needed. Open the shell and type:

```
what treats severe bleeding
what is a tourniquet used for
when should you not use a tourniquet
what are the signs of shock
```

Every result shows the source evidence and a verify button. Click it. The shell reads the bytes from the shard and confirms they match.

---

## Import your own conversations

Export from Claude: Settings → Data export → conversations.json  
Export from ChatGPT: Settings → Data export → conversations.json

```
> import /path/to/conversations.json
```

Drop the file onto the shell window to import without typing a path.

After import, query immediately. Distillation (episodic index + decision extraction) is a separate optional step that requires Ollama:

```
> distill <shard_name>
```

---

## Shell commands

```
query <question>       natural language — anything else routes here too
import <path>          ingest a conversation export
distill <shard>        run episodic distillation (requires ollama + mistral)
verify <shard>         cryptographic verification — Merkle + signature
shards                 list mounted shards
help                   command reference
clear                  clear the chronicle
```

---

## Architecture

```
axm-genesis   cryptographic kernel — Merkle, signing, verification
axm-core      hub — Forge (compile), Spectra (query), Clarion (transport)
axm-chat      spoke — conversation import, distillation, Flask server
axm-shell     browser UI — talks to the server, renders the chronicle
```

Spokes depend on the hub. The hub depends on the kernel. The kernel is frozen.

**[→ Genesis](https://bigbirdreturns.github.io/axm-genesis/)** · **[→ Core](https://bigbirdreturns.github.io/axm-core/)** · **[→ Show](https://bigbirdreturns.github.io/axm-show/)** · **[→ Embodied](https://bigbirdreturns.github.io/axm-embodied/)**

---

## License

Apache-2.0
