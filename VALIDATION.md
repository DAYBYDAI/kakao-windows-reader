# Validation record — 0.1.0

Validated on 2026-09-11 with Windows 11 x64, Python 3.12.14 x64, cryptography 50.0.1 and KakaoTalk 26.7.1.5263 x64.

- 102 automated tests passed: authenticated page decoding, WAL commit/checksum handling, x86/x64 synthetic process layouts, read-only snapshots, time/identity/room filters, missing-history diagnostics, DPAPI cache, per-user config, keyword search, hourly scan deduplication and failure/backfill handling, and scheduler argument/ownership checks.
- New CLI discovered the local account/process without hardcoded machine paths and recovered HMAC-verified DB keys into a Windows DPAPI cache.
- 451 messages obtained by the new reader had identical message IDs and bodies to the corresponding previously validated results. One formerly available room key was no longer resident; its 12 messages were correctly reported as unavailable instead of invented.
- The release ZIP was extracted under a separate directory containing Korean characters and spaces. Windows PowerShell 5.1 created a fresh Python virtualenv and installed the dependencies successfully.
- The installed release's setup created a per-user config and local skill profile. Its scan pipeline was run with the clock fixed to an already authorized two-hour window: 451 records, missing-range backfill state, cursor/index, and profile publication succeeded.
- Package generation uses a public-file allowlist. Personal profiles, database keys, chat exports, logs, virtualenvs and local test artifacts are excluded.
- Claude Code 2.1.138 was available. The final Opus execution test was deferred at the user's request after an expired OAuth token returned HTTP 401. This is not an Opus end-to-end validation claim.
- Windows Task Scheduler accepted the generated XML with TASK_VALIDATE_ONLY (0x1); an invalid-interval control was rejected. The temporary task did not exist before or after validation. Actual hourly execution was not tested.
- No recurring Claude task or Windows scheduled task was registered during installation/validation.

Scope: one physical PC was tested. This does not certify every Windows/KakaoTalk version. ARM64 and a real x86 KakaoTalk process have not been tested. HMAC/integrity success does not establish complete server history or complete receipt history.

## GitHub release (2026-10-10)

- Source: the 0.1.0 release ZIP of 2026-09-11. `install-skill.ps1`, `Install-Claude-Skill.cmd`, `run.cmd`, `requirements.txt`, `THIRD_PARTY_NOTICES.md` and every Python module except `export.py` are identical to that ZIP (both `LICENSE` copies keep the original MIT text and gained a second copyright line for the modifications) except line endings (LF in the repository; `.ps1`/`.cmd` are checked out with CRLF through `.gitattributes`).
- One code change, `export.py`: the `messages.md` heading (time, room, sender) is now written as a single-line `<h3>` HTML block with every line-break character replaced. Room and sender names are set by other people; before this change a crafted name could add a fake message heading, a remote image or a link when the Markdown file was rendered. Checked on macOS with a CommonMark renderer (markdown-it-py 3.0.0): with the same crafted input, the original rendered 3 headings for 2 messages plus 1 image and 1 link; the fixed version renders exactly 2 headings and no image or link. CSV and JSON exports are unchanged.
- Documentation: the human README was replaced by an AI-agent landing page, `AGENTS.md` (setup procedure for AI agents) and `CLAUDE.md` were added, and `SKILL.md` gained Korean trigger phrases, a "`tool/.venv`가 없을 때" section and a rule against putting room names into shell commands. `SHA256SUMS.txt` was dropped because git history now records file integrity.
- This release was not re-run on Windows. The 2026-09-11 results above apply to the unchanged code. Checks done for this release are listed in the commit that published it.
