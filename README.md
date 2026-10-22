# MonLua 5.4+ Obfuscator Bot

A Discord bot that obfuscates Lua and Luau scripts with the MonLua VM engine.

## What it does

- `bot.py`: Discord bot with `/obfuscate` and `/protectinfo` (private responses)
- `engine.py`: the obfuscation engine plus a SHA-256 manifest for every build
- `test_engine.py`: regression tests
- `render.yaml`: Render Background Worker configuration (the live deployment runs on OrbitHost)

The engine compiles the source to a custom VM bytecode with shuffled opcodes and
a self-contained VM launcher (mode `vm`). Scripts the VM compiler cannot parse
fall back to a text-level pass with encoded strings and numbers, randomized
identifiers, and junk-code insertion (mode `source`). Every reply carries a
manifest with input and output SHA-256 hashes and the engine mode used.

Metadata-only logs: source code and tokens are never logged. Temporary compiler
input files are cleaned up with `TemporaryDirectory`.

## Honest limitations

Obfuscation is protection, not encryption. The output runs the same logic as the
input, and dedicated tooling may still be able to reverse it. A SHA-256 hash
detects changes; it hides nothing. Sensitive logic and credentials should stay
on a trusted server.

## Local setup

Python 3.10+.

```bash
python -m pip install -r requirements.txt
python -m unittest test_engine
```

Set `DISCORD_TOKEN` in the environment and run:

```bash
python bot.py
```

Invite the bot with the `bot` and `applications.commands` scopes. Message
Content intent is not needed.

## Deployment

The live bot runs on OrbitHost from this repository: every deploy clones the
repo, rebuilds the virtual environment when `requirements.txt` changed, and
starts `bot.py`. `DISCORD_TOKEN` is stored as a project secret. Do not commit
tokens to source control.

Optionally set `LUAU_COMPILER` to a `luau-compile` executable path to validate
the original source before obfuscation. Without it the manifest says validation
was not performed.

## Environment variables

- `DISCORD_TOKEN` (required)
- `MAX_UPLOAD_BYTES` (default 524288, 512 KB)
- `BUILD_TIMEOUT_SECONDS` (default 300)
- `LOG_LEVEL` (default INFO)
- `LUAU_COMPILER` (optional, Luau syntax validation of the source)
- `FIVEM_VALIDATOR` (optional, CfxLua validator; the engine obfuscates the
  same way for both targets)

## Commands

- `/obfuscate file:<attachment>`: obfuscates a `.lua`, `.luau` or `.txt` file
  and replies privately with the output, a manifest, and a build report
- `/protectinfo`: shows implemented features and limitations
