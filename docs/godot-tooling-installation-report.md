# Godot Tooling Installation And Verification Report

Date: 2026-05-20  
Workspace: `/mnt/d/users/johni/documents/building3d`

## Summary

The Godot tooling stack is installed locally and mostly works. I did not need sudo.

Install locations:

- Godot binary: `~/.local/opt/godot/4.6.2-stable`
- User command shims: `~/.local/bin`
- Main vendored tools: `tools/vendor/godot-tooling/`
- Codex skills: `~/.codex/skills`
- Raw verification logs: `tools/vendor/godot-tooling/logs/`

Main result:

- Fully working: Godot CLI/headless, Godot AI MCP, GoPeak, Coding-Solo MCP, alexmeckes MCP, Better Godot MCP, MhrnMhrn MCP, Erodenn runtime MCP, npm `godot-mcp`, GUT, GdUnit4, GDSnap, gdtoolkit, GDQuest formatter CLI, VS Code Godot extension, godot-ci Docker image, PlayGodot Python package/tests, haxqer Godot skill tests.
- Partially working: `godot-mcp-cli`, ee0pdt Godot-MCP, lifeiscontent Godot MCP, GDQuest formatter addon, Godot MCP Pro, GDAI plugin repo, Godot-Claude-Skills example tests.
- Not installable from available sources: `niejiaqiang/godot-mcp-codex`.

Do not enable every MCP in Codex at the same time. Several expose overlapping tool names and several use the same executable name, especially `godot-mcp`. Use explicit absolute paths in MCP config.

## Environment

Verified tools:

| Tool | Result |
|---|---|
| Godot | `4.6.2.stable.official.71f334935` |
| Node | `v24.11.1` |
| npm | `11.6.2` |
| Python | `3.12.3` |
| uv | available |
| Cargo | `1.95.0` |
| Docker | `29.0.1` |
| VS Code CLI | available |

## MCP Server Verification

I tested real MCP servers by connecting over stdio with the MCP SDK and calling `tools/list`. That is stronger than only checking `--help`.

| Tool | Install State | MCP Probe | Notes |
|---|---:|---:|---|
| Godot AI / `hi-godot/godot-ai` | OK | OK, 39 tools | `godot-ai` starts, exposes scene/node/editor/session tools, opens WebSocket on `127.0.0.1:9500`. |
| GoPeak repo / `HaD0Yun/Gopeak-godot-mcp` | OK | OK, 33 tools | Built from repo and starts a bridge on `127.0.0.1:6505`. |
| GoPeak npm package | OK | OK, 33 tools | `gopeak --help` works and MCP probe works. |
| Coding-Solo `godot-mcp` | OK | OK, 14 tools | Basic editor/run/project/scene tools work. |
| alexmeckes `godot-mcp` | OK | OK, 99 tools | Warned that Building3D root is not a Godot project, but server and tool list work. |
| Better Godot MCP | OK | OK, 17 composite tools | Detects Godot 4.6.2 at `~/.local/bin/godot`. |
| Erodenn `godot-mcp-runtime` | OK | OK, 36 tools | Runtime/project manipulation tools list correctly. |
| npm `godot-mcp` package | OK | OK, 14 tools | Separate local npm install works. |
| MhrnMhrn `godot-mcp` | OK | OK, 29 tools | Installed with `uv tool`; exposes project/resource/scene/script tools. |
| ee0pdt Godot-MCP | Partial | Timeout | Server starts but immediately tries `ws://localhost:9080`; it needs its Godot-side plugin/server running. |
| `godot-mcp-cli` server | Partial | Timeout | CLI usage works, but direct stdio SDK probe did not return `tools/list` within timeout. Treat as a CLI wrapper first, MCP server second. |
| lifeiscontent Godot MCP | Partial | Not stdio-probed | Godot plugin loads headlessly. Repo is mainly a Godot plugin/SSE-style integration, not a simple stdio CLI. |
| GDAI MCP plugin repo | Partial | Not available from repo | Public repo contains a sample Godot project, not the actual distributable plugin/server. Official plugin is gated through the GDAI site. |
| Godot MCP Pro | Partial | Plugin only | Public asset-library zip downloaded and plugin loads. It is proprietary and still needs project/client configuration. |
| `niejiaqiang/godot-mcp-codex` | Failed | Not installable | GitHub repo is README-only; npm/PyPI checks found no package; LobeHub CLI requires marketplace credentials for more metadata. |

Raw MCP result file:

```text
tools/vendor/godot-tooling/logs/mcp_probe_results.json
```

Important MCP command collision:

```text
godot-mcp
```

is used by multiple installs. Use absolute paths, for example:

```text
/mnt/d/users/johni/documents/building3d/tools/vendor/godot-tooling/mcp/better-godot-mcp/bin/cli.mjs
/mnt/d/users/johni/documents/building3d/tools/vendor/godot-tooling/mcp/coding-solo-godot-mcp/build/index.js
/home/johni/.local/bin/godot-ai
```

## Godot CLI And Built-In Tooling

| Capability | Result | Evidence |
|---|---:|---|
| Godot command line | OK | `godot --headless --version` prints `4.6.2.stable.official.71f334935`. |
| Headless script execution | OK | A `SceneTree` probe printed `SCENE_TREE_SCRIPT_OK`. |
| CLI flags | OK | `--headless`, `--script`, `--check-only`, and `--import` are present in `godot --help`. |
| LSP port | OK | Headless editor opened `127.0.0.1:6005`. |
| DAP port | OK | Headless editor opened `127.0.0.1:6006`. |
| EditorScript via CLI | Expected limitation | `--script` requires `SceneTree` or `MainLoop`, so a direct `EditorScript` CLI probe fails. EditorScripts are still a Godot editor feature, but not runnable through this simple CLI path. |

## Godot Addons And Plugins

I copied each addon/plugin into a temporary Godot project and launched Godot headlessly in editor mode with the plugin enabled.

| Addon / Plugin | Result | Notes |
|---|---:|---|
| Godot AI plugin | OK | Loads in a temporary Godot 4.6 project. |
| Godot MCP Pro plugin | OK | Loads, starts, and stops cleanly. Proprietary package. |
| lifeiscontent Godot MCP plugin | OK | Loads and deactivates cleanly. |
| GUT | OK with warning | Loads. Godot reports an ObjectDB leak warning on exit, but return code is 0. |
| GdUnit4 | OK | Loads cleanly. |
| GDSnap | OK | Loads cleanly. |
| GDQuest GDScript formatter addon | Partial | Loads, but reports missing formatter executable path in editor settings. The CLI exists at `~/.local/bin/gdscript-formatter`; set that path in the addon settings when using it in a real project. |
| GDAI sample project | OK | The public repo's sample Godot project opens headlessly, but this is not the full plugin package. |

Raw addon result file:

```text
tools/vendor/godot-tooling/logs/addon_probe_results.json
```

## GDScript Tooling

| Tool | Result | Evidence |
|---|---:|---|
| gdtoolkit | OK | `gdlint 4.5.0`, `gdformat 4.5.0`, `gdparse`, and `gdradon` work on a sample `.gd` file. |
| GDQuest formatter CLI | OK | `gdscript-formatter 0.20.0`; check/stdout formatting works. |
| GDQuest formatter addon | Partial | Plugin loads, but needs the executable path configured in Godot editor settings. |
| VS Code Godot plugin | OK | `geequlim.godot-tools@2.6.1` installed. |

## Test Frameworks

| Framework | Result | Evidence |
|---|---:|---|
| GUT | OK | Addon loads in Godot headless editor mode. |
| GdUnit4 | OK | Addon loads in Godot headless editor mode. |
| GDSnap | OK | Addon loads in Godot headless editor mode. |
| PlayGodot Python package | OK | After adding missing `numpy`, upstream Python tests pass: `229 passed`. |
| Godot-Claude-Skills PlayGodot example tests | Blocked as designed | Tests require `GODOT_PATH` pointing to the custom automation Godot fork. Without that, pytest exits with the repo's own setup error. |

PlayGodot details:

- Package import works.
- The repository test suite initially failed 11 screenshot-similarity tests because `numpy` was missing.
- After installing `numpy` into `tools/vendor/godot-tooling/cli/playgodot-venv`, the suite passed.

## CI And Export Tooling

| Tool | Result | Notes |
|---|---:|---|
| `godot-ci` repo | OK | Cloned under `tools/vendor/godot-tooling/cli/godot-ci`. |
| `barichello/godot-ci:latest` Docker image | OK | Docker image pulled and `godot --version` runs. The container prints a `libfontconfig.so.1` warning but still reports Godot 4.6.2 and exits successfully. |

## Skills And Agent Helper Repos

| Skill / Repo | Result | Notes |
|---|---:|---|
| Randroids-Dojo Godot skill | OK | Installed at `~/.codex/skills/godot-randroids/SKILL.md`. |
| haxqer Godot skill | OK | Installed at `~/.codex/skills/godot-haxqer/SKILL.md`; helper tests pass: `14 passed`. |
| Randroids Godot-Claude-Skills | OK / partial tests | Installed at `~/.codex/skills/godot-claude-skills/SKILL.md`; example PlayGodot tests require custom Godot automation fork. |
| CODEXVault_GODOT | Cloned, safe validation OK | Shell syntax checks pass for `.codex/setup.sh` and `.codex/fix_indent.sh`. I did not run `setup.sh` because it performs apt/system installs and can overwrite `/usr/local/bin/godot`. |

## Known Caveats

1. Do not add all MCPs to Codex at once. They overlap heavily and will make tool selection noisy.
2. Use one primary Godot MCP for real work. Best candidates from the tests are:
   - Godot AI: strongest editor/plugin integration.
   - GoPeak: strong MCP list-tools result and live bridge.
   - Better Godot MCP: compact, predictable tool surface.
   - Coding-Solo / npm `godot-mcp`: simple baseline.
3. `ee0pdt Godot-MCP` needs a live Godot WebSocket server at port `9080`.
4. `godot-mcp-cli` is usable as a CLI, but did not behave like a direct stdio MCP server in the probe.
5. `GDAI MCP Plugin Godot` cannot be fully installed from the public GitHub repo alone.
6. `godot-mcp-codex` is not currently installable from the public repo because it is README-only.
7. PlayGodot needs the Randroids automation Godot fork for actual end-to-end game control. The Python library itself works.
8. GDQuest formatter addon needs its Godot editor setting pointed to `~/.local/bin/gdscript-formatter`.

## Recommended Setup For Building3D / UNIMATE Work

Use this practical stack:

| Purpose | Recommended Tool |
|---|---|
| General Godot automation | Godot AI or GoPeak |
| Compact Codex MCP config | Better Godot MCP |
| Simple fallback MCP | Coding-Solo `godot-mcp` |
| Runtime/game inspection | Erodenn `godot-mcp-runtime` or PlayGodot with custom Godot fork |
| Unit tests | GdUnit4 or GUT |
| Screenshot tests | GDSnap |
| GDScript lint/format | `gdtoolkit` plus `gdscript-formatter` |
| CI/export | `godot-ci` Docker image |

For UNIMATE specifically, I would start with one MCP only:

```text
Godot AI first, or GoPeak if Godot AI is too broad.
```

Then keep `Better Godot MCP` as the backup because its smaller tool surface is easier for Codex to control reliably.

## Verification Commands Used

Representative commands:

```bash
godot --headless --version
godot --headless --script /tmp/godot_builtin_probe/scene_tree_probe.gd
ss -ltnp | rg ':6005|:6006'
gdlint /tmp/gd_tool_probe.gd
gdparse /tmp/gd_tool_probe.gd
gdformat --check /tmp/gd_tool_probe.gd
gdscript-formatter --check /tmp/gd_tool_probe.gd
code --list-extensions --show-versions | rg -i 'godot|geequlim'
docker run --rm barichello/godot-ci:latest godot --version
PYTHONPATH=tools/vendor/godot-tooling/cli/PlayGodot/python \
  tools/vendor/godot-tooling/cli/playgodot-venv/bin/python \
  -m pytest tools/vendor/godot-tooling/cli/PlayGodot/python/tests -q
tools/vendor/godot-tooling/cli/playgodot-venv/bin/python \
  -m pytest tools/vendor/godot-tooling/skills/haxqer-godot-skill/tests -q
```

MCP servers were probed using the MCP SDK client and `tools/list`.

